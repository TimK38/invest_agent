"""買進前檢查（可盤中用）— STRATEGY.md §5 交易身份證 + §3 曝險上限

盤中即時價從 TWSE MIS 取得；抓不到就退回最後收盤價，並在輸出標明。
既有持股自動從 positions.py 的紀錄讀取，不需要重打。

用法:
  python buy_check.py 3481 100                    # 融資買 100 張（依 profile 預設）
  python buy_check.py 3481 100 --cash             # 現股
  python buy_check.py 3481 100 --stop 42.0        # 指定停損，否則用 2×ATR 與 10 日低取較近
  python buy_check.py 3481 --amount 1000000       # 給金額，自動換算張數
  python buy_check.py 3481 100 --price 45.0       # 手動指定成交價（不連網）
  python buy_check.py 3481 100 --horizon 波段

大盤狀態一律以**最後收盤**判定（§2 是收盤判定規則）。盤中執行時個股價是即時的，
大盤狀態是昨收的，輸出會標明資料時點——這是設計，不是 bug。
"""
import argparse, sys
import numpy as np, pandas as pd

import positions as pos_store
import profile_loader
from paths import STOCKS_ADJ
from portfolio_check import taiex_state, DEFAULT_RC

SINGLE_CAP = {"A": 0.25, "B": 0.20, "C": 0.15, "D": 0.0}   # §3 單一標的曝險上限
MAINT = 1.30                                               # 融資維持率追繳線


def quote(sid):
    """TWSE MIS 即時報價；回傳 (價格, 來源說明) 或 (None, 原因)"""
    try:
        import requests
        for ex in ("tse", "otc"):
            r = requests.get("https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
                             params={"ex_ch": f"{ex}_{sid}.tw", "json": "1", "delay": "0"},
                             headers={"User-Agent": "Mozilla/5.0",
                                      "Referer": "https://mis.twse.com.tw/stock/"}, timeout=10)
            arr = r.json().get("msgArray") or []
            if arr:
                m = arr[0]
                px = m.get("z") or m.get("b", "").split("_")[0] or m.get("y")
                if px and px not in ("-", ""):
                    return float(px), f"即時 {m.get('d','')} {m.get('t','')}"
        return None, "MIS 無回應"
    except Exception as e:
        return None, f"{type(e).__name__}"


def indicators(sid, st, mret):
    g = st[st.sid == sid].sort_values("date").set_index("date").copy()
    if g.empty:
        sys.exit(f"{sid} 無資料。先執行: python fetch/fetch_stocks.py {sid} --since 2023-07"
                 f" 再跑 python fetch/clean_stocks.py")
    c, h, l = g.adj_close, g.adj_high, g.adj_low
    for n in (20, 60, 120):
        g[f"sma{n}"] = c.rolling(n).mean()
    g["ema8"] = c.ewm(span=8, adjust=False).mean()
    g["ema21"] = c.ewm(span=21, adjust=False).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    g["atr20"] = tr.rolling(20).mean()
    g["r"] = c.pct_change()
    g["dd"] = c / c.cummax() - 1
    j = pd.concat([g.r.rename("r"), mret.rename("m")], axis=1, sort=True).dropna()
    if len(j) >= 60:
        dn = j[j.m < 0]
        rc = max(np.cov(dn.r, dn.m)[0, 1] / np.var(dn.m), j.r.std() / j.m.std())
    else:
        rc = DEFAULT_RC
    return g, g.iloc[-1], float(rc), len(j)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sid")
    ap.add_argument("lots", nargs="?", type=float, help="張數；或改用 --amount")
    ap.add_argument("--amount", type=float, help="打算投入的市值（元），自動換算張數")
    ap.add_argument("--cash", action="store_true", help="現股（預設依 profile 的 margin_allowed）")
    ap.add_argument("--stop", type=float, help="停損價；不給就用 2×ATR 與 10 日低取較近")
    ap.add_argument("--price", type=float, help="成交價；不給就抓即時價，抓不到用收盤")
    ap.add_argument("--horizon", default="波段", help="短線/波段/長線")
    ap.add_argument("--net-worth", type=float)
    profile_loader.add_arg(ap)
    a = ap.parse_args()

    cfg = profile_loader.load(a.profile)
    NW = a.net_worth or cfg["net_worth"]
    MAX_RISK = cfg["max_risk_per_trade"]
    mrate = cfg["margin_rate"]
    use_margin = cfg["margin_allowed"] and not a.cash

    idx, ms, code = taiex_state()
    pos_coef = float(np.clip(1.8 / ms.atrp, 0.4, 1.0))
    lev_cap = cfg["leverage_caps"][code] * pos_coef

    st = pd.read_csv(STOCKS_ADJ, parse_dates=["date"], dtype={"sid": str})
    mret = idx.set_index("date").ret
    g, last, rc, nsample = indicators(a.sid, st, mret)
    name = g.name.iloc[-1]

    if a.price:
        px, psrc = a.price, "手動指定"
    else:
        px, psrc = quote(a.sid)
        if px is None:
            px, psrc = float(last.adj_close), f"最後收盤 {last.name:%Y-%m-%d}（即時報價失敗：{psrc}）"

    lots = a.lots if a.lots else (a.amount / (px * 1000) if a.amount else None)
    if not lots:
        sys.exit("要給張數，或用 --amount 給金額")

    stop = a.stop or max(px - 2 * last.atr20, g.adj_close.tail(10).min() * 0.99)
    risk_sh = px - stop
    tgt = px + 2 * risk_sh                        # 盈虧比 2.0 的隱含目標價
    mv = lots * 1000 * px
    exposure = mv * rc

    # ── 既有持股 ──
    store = pos_store.load(cfg)
    ex_have = mv_have = 0.0
    same = None
    for p in store["positions"]:
        pg = st[st.sid == p["sid"]].sort_values("date")
        if pg.empty:
            continue
        pj = pd.concat([pg.set_index("date").adj_close.pct_change().rename("r"),
                        mret.rename("m")], axis=1, sort=True).dropna()
        if len(pj) >= 60:
            pdn = pj[pj.m < 0]
            prc = max(np.cov(pdn.r, pdn.m)[0, 1] / np.var(pdn.m), pj.r.std() / pj.m.std())
        else:
            prc = DEFAULT_RC
        pmv = p["lots"] * 1000 * pg.adj_close.iloc[-1]
        mv_have += pmv
        ex_have += pmv * prc
        if p["sid"] == a.sid:
            same = (p, pg.adj_close.iloc[-1])

    print("=" * 78)
    print(f"  買進檢查　{a.sid} {name}　{lots:g} 張 @ {px:.2f}　"
          f"{'融資' if use_margin else '現股'}　{a.horizon}")
    print(f"  價格來源：{psrc}　｜　大盤狀態依 {ms.date:%Y-%m-%d} 收盤判定（§2 為收盤規則）")
    print("=" * 78)
    print(f"  【盤面】狀態【{code}】  加權 {ms.close:,.0f}  距高點 {ms.dd:.1%}  ATR% {ms.atrp:.2f}")
    print(f"          有效槓桿上限 {cfg['leverage_caps'][code]} × 波動係數 {pos_coef:.2f} "
          f"= {lev_cap:.2f} 倍　單一標的上限 {SINGLE_CAP[code]:.0%}")
    print(f"  【標的】收盤 {last.adj_close:.2f}　EMA8 {last.ema8:.2f}　SMA20 {last.sma20:.2f}　"
          f"SMA60 {last.sma60:.2f}　距高點 {last.dd:.1%}")
    print(f"          風險係數 {rc:.2f}（{nsample} 個交易日實測）"
          + ("　⚠ 樣本偏短" if nsample < 250 else ""))
    print(f"  【這筆】市值 {mv:,.0f}（淨資產 {mv/NW:.1%}）　曝險 {exposure:,.0f}（{exposure/NW:.1%}）")

    fails, passes = [], []

    def chk(ok, msg):
        (passes if ok else fails).append(msg)

    # 0. §7 連續停損禁令
    if until := pos_store.banned(store, a.sid):
        fails.append(f"§7 該檔 30 天內停損 2 次，禁止交易至 {until}")
    # 1. 狀態閘門
    if code == "D":
        fails.append("狀態閘門：D 狀態禁止做多")
    elif code == "C":
        fails.append("狀態閘門：C 狀態禁止新開倉，且融資餘額須為 0")
    elif code == "B" and use_margin:
        fails.append("狀態閘門：B 狀態不得新增融資")
    else:
        passes.append(f"狀態閘門：{code} 狀態允許")
    # 2. 盈虧比（隱含目標）
    resist = [("SMA20", last.sma20), ("SMA60", last.sma60)]
    above = [n for n, v in resist if not np.isnan(v) and px < v <= tgt]
    chk(not above,
        f"盈虧比 2.0 的隱含目標 {tgt:.2f} 上方有壓力：{'、'.join(above)}"
        f" → 目標不可信，視為盈虧比 < 2.0" if above else
        f"隱含目標 {tgt:.2f}（+{tgt/px-1:.1%}）與現價之間無 SMA20/SMA60 壓力"
        f" → 仍須你確認前波高與整數關")
    # 3. 單筆風險
    risk_amt = risk_sh * lots * 1000
    chk(risk_amt <= NW * MAX_RISK,
        f"單筆風險 {risk_amt:,.0f}（{risk_amt/NW:.2%}）"
        f"{'≤' if risk_amt <= NW*MAX_RISK else '>'} 上限 {NW*MAX_RISK:,.0f}（{MAX_RISK:.1%}）")
    # 4. 有效槓桿
    lev_after = (ex_have + exposure) / NW
    chk(lev_after <= lev_cap,
        f"有效槓桿 {ex_have/NW:.2f} → {lev_after:.2f} 倍　上限 {lev_cap:.2f} 倍")
    # 5. 單一標的
    own_ex = (same[0]["lots"] * 1000 * same[1] * rc) if same else 0.0
    chk((own_ex + exposure) / NW <= SINGLE_CAP[code],
        f"單一標的曝險 {(own_ex+exposure)/NW:.1%}　上限 {SINGLE_CAP[code]:.0%}")
    # 6. 攤平
    if same and px < same[0]["cost"]:
        fails.append(f"攤平：已持有 {same[0]['lots']:g} 張，成本 {same[0]['cost']:.2f}，"
                     f"現價 {px:.2f}（{px/same[0]['cost']-1:+.1%}）虧損中 → 鐵則 1")
    elif same:
        passes.append(f"已持有 {same[0]['lots']:g} 張且獲利中，非攤平")
    else:
        passes.append("新標的，非攤平")
    # 7/8. 波動與回撤下的融資禁令
    if use_margin:
        chk(ms.atrp <= 2.5, f"ATR% {ms.atrp:.2f} {'≤' if ms.atrp <= 2.5 else '>'} 2.5"
                            + ("" if ms.atrp <= 2.5 else " → 禁止新增融資"))
        chk(ms.dd > -0.10, f"大盤距高點 {ms.dd:.1%}"
                           + ("" if ms.dd > -0.10 else " → 回撤 >10%，融資餘額須為 0"))
    # 9. 趨勢（進場理由必須是順勢事實）
    chk(px > last.sma20, f"價格 {px:.2f} {'>' if px>last.sma20 else '<'} SMA20 {last.sma20:.2f}"
                         f"{'' if px>last.sma20 else ' → 逆勢，§5 ② 進場理由不成立'}")

    print("\n  【檢查】")
    for m in passes:
        print(f"    ✅ {m}")
    for m in fails:
        print(f"    ❌ {m}")

    # 三個上限取最小 → 可買張數
    cap_risk = NW * MAX_RISK / risk_sh / 1000 if risk_sh > 0 else 0
    cap_lev = max(0.0, (NW * lev_cap - ex_have)) / (rc * px * 1000)
    cap_single = max(0.0, (NW * SINGLE_CAP[code] - own_ex)) / (rc * px * 1000)
    cap = min(cap_risk, cap_lev, cap_single)

    print(f"\n  【結論】{'❌ 不可執行' if fails else '✅ 可執行'}"
          + (f"　違反 {len(fails)} 項" if fails else ""))
    print(f"\n  【張數上限】1.5%風險 {cap_risk:.0f} 張／槓桿上限 {cap_lev:.0f} 張／"
          f"單一標的 {cap_single:.0f} 張　→ 取最小 {cap:.0f} 張（首筆 1/3 = {cap/3:.0f} 張）")

    print(f"\n  【下單卡{'（本次不可執行，以下僅供了解距離多遠）' if fails else ''}】"
          f"{a.sid} {name} · {a.horizon} · {'融資' if use_margin else '現股'}")
    print(f"    進場價    {px:8.2f}")
    print(f"    停損價    {stop:8.2f}  (-{1-stop/px:.1%})"
          + ("  ← 你指定" if a.stop else "  2×ATR 與 10 日低取較近"))
    print(f"    隱含目標  {tgt:8.2f}  (+{tgt/px-1:.1%})  ← 需你確認上方無壓力")
    print(f"    首筆張數  {cap/3:8.0f} 張")
    print(f"    滿倉上限  {cap:8.0f} 張")
    if use_margin:
        call = px * MAINT * mrate
        print(f"    融資追繳  {call:8.2f}  (-{1-call/px:.0%}，約 {(px-call)/last.atr20:.1f} 個 ATR)")
    print(f"    加碼條件  站穩 5 個交易日且未觸停損 → 補到 2/3")
    print(f"  【出場 · 每日收盤判定】")
    print(f"    跌破 EMA8  {last.ema8:.2f} → 出 1/2")
    print(f"    跌破 SMA20 {last.sma20:.2f} → 全部出清")
    print(f"    觸及 {tgt:.2f} → 賣 30%，停損上移至成本 {px:.2f}")
    print(f"    大盤轉 C/D → 不論個股，總曝險降回上限")
    print(f"\n  成交後記錄：./envest_agent/bin/python positions.py "
          f"--add {a.sid}:張數:{px:.2f}:{stop:.2f}"
          f"{'' if use_margin else ':cash'} --horizon {a.horizon}")
    print()


if __name__ == "__main__":
    main()
