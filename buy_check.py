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
from paths import PRICES_ADJ
from portfolio_check import taiex_state, DEFAULT_RC, vol_warning, SINGLE_CAP

MAINT = 1.30                                               # 融資維持率追繳線
DEV_WARN = 4.0            # §7 急漲警示：距 SMA20 幾個 ATR（全樣本 95 百分位）


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
    ap.add_argument("--asof", help="只用該日(含)以前的資料重現當時判斷 YYYY-MM-DD")
    ap.add_argument("--net-worth", type=float)
    profile_loader.add_arg(ap)
    a = ap.parse_args()

    cfg = profile_loader.load(a.profile)
    NW = a.net_worth or cfg["net_worth"]
    MAX_RISK = cfg["max_risk_per_trade"]
    mrate = cfg["margin_rate"]
    use_margin = cfg["margin_allowed"] and not a.cash

    idx, ms, code = taiex_state(a.asof)
    pos_coef = float(np.clip(1.8 / ms.atrp, 0.4, 1.0))
    lev_cap = cfg["leverage_caps"][code] * pos_coef

    st = pd.read_csv(PRICES_ADJ, parse_dates=["date"], dtype={"sid": str})
    if a.asof:
        st = st[st.date <= pd.Timestamp(a.asof)]
    mret = idx.set_index("date").ret
    g, last, rc, nsample = indicators(a.sid, st, mret)
    name = g.name.iloc[-1]

    if a.price:
        px, psrc = a.price, "手動指定"
    elif a.asof:
        px, psrc = float(last.adj_close), f"{last.name:%Y-%m-%d} 收盤（--asof 重現模式）"
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
        # --asof 重現模式：當時還沒建立的部位不能算進去（否則是反向的未來資料污染）
        if a.asof and p.get("opened") and p["opened"] > a.asof:
            continue
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

    add_on = same is not None          # S6：已持有 → 這是加碼，不是新開倉
    print("=" * 78)
    print(f"  {'加碼檢查' if add_on else '買進檢查'}　{a.sid} {name}　{lots:g} 張 @ {px:.2f}　"
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

    if add_on:
        p0, cur_px = same
        held_lots, opened = p0["lots"], p0.get("opened")
        # 進場後經過幾個交易日（§7 分批建倉節奏要求站穩 5 個交易日才補到 2/3）
        sess = st[st.sid == a.sid].date
        held_days = int((sess > pd.Timestamp(opened)).sum()) if opened else None
        print(f"  【加碼】已持有 {held_lots:g} 張 @ 成本 {p0['cost']:.2f}"
              f"（現價 {cur_px:.2f}，損益 {cur_px/p0['cost']-1:+.1%}）"
              f"　進場日 {opened or '—'}"
              + (f"　已 {held_days} 個交易日" if held_days is not None else ""))

    fails, passes = [], []

    def chk(ok, msg):
        (passes if ok else fails).append(msg)

    # S6：加碼的專屬閘門 —— §3「A 狀態才可加碼獲利部位」與 §7 分批建倉節奏
    if add_on:
        chk(code == "A", f"§3 加碼只允許在 A 狀態（目前 {code}）"
            + ("" if code == "A" else "　→ 加碼與新開倉一樣受狀態閘門限制"))
        if held_days is not None:
            chk(held_days >= 5, f"§7 分批建倉：站穩 {held_days} 個交易日"
                + ("（≥ 5，可補到 2/3）" if held_days >= 5 else "（< 5，未達補倉條件）"))
        if p0.get("stop") and cur_px < p0["stop"]:
            fails.append(f"已跌破當初停損 {p0['stop']:.2f} → 該出場而不是加碼")

    # 0. §7 連續停損禁令
    if until := pos_store.banned(store, a.sid):
        fails.append(f"§7 該檔 30 天內停損 2 次，禁止交易至 {until}")
    # 1. 狀態閘門
    if code == "D":
        fails.append("狀態閘門：D 狀態禁止做多")
    elif code == "C":
        fails.append("狀態閘門：C 狀態禁止新開倉，且融資餘額須為 0")
    elif code == "B":
        # §2 的 B 有兩條路徑，對進場的意義完全相反（§3 的例外只適用 B2）：
        #   B1 收盤跌破 SMA20        → 只出不進，現股融資一律禁止
        #   B2 收盤站上 SMA20 未達 A → §7 訊號成立，可用現股建首波 1/3，融資仍禁止
        b2 = ms.close > ms.sma20
        if use_margin:
            fails.append(f"狀態閘門：B{'2' if b2 else '1'} 狀態不得新增融資")
        elif not b2:
            fails.append(f"狀態閘門：B1 狀態（大盤收盤 {ms.close:,.0f} 跌破大盤 SMA20 "
                         f"{ms.sma20:,.0f}）只出不進——§3 的現股例外只適用 B2")
        else:
            passes.append(f"狀態閘門：B2 狀態允許現股建首波 1/3"
                          f"（大盤收盤 {ms.close:,.0f} > 大盤 SMA20 {ms.sma20:,.0f}）")
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
        fails.append(f"**攤平**：已持有 {same[0]['lots']:g} 張，成本 {same[0]['cost']:.2f}，"
                     f"現價 {px:.2f}（{px/same[0]['cost']-1:+.1%}）虧損中"
                     f" → 鐵則 1，加碼只能加在獲利部位")
    elif same:
        passes.append(f"鐵則 1：已持有 {same[0]['lots']:g} 張且獲利中"
                      f"（{px/same[0]['cost']-1:+.1%}），非攤平")
    else:
        passes.append("新標的，非攤平")
    # 7/8. 波動與回撤下的融資禁令
    if use_margin:
        # §8 禁令 3（v1.7）：與 §2 的 C 閘門用同一個波動警訊定義，兩處必須一致，
        # 否則「不強制出場但也不准回補」，放寬的效果會被進場側抵銷掉一半。
        vw = vol_warning(ms)
        chk(not vw, f"ATR% {ms.atrp:.2f}"
                    + (f" > 2.5 但收盤 {ms.close:,.0f} 仍在 SMA20 {ms.sma20:,.0f} 之上"
                       "（順勢波動，非警訊）" if ms.atrp > 2.5 and not vw else "")
                    + (" > 2.5 且收盤跌破 SMA20 → 禁止新增融資" if vw else
                       (" ≤ 2.5" if ms.atrp <= 2.5 else "")))
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

    # §7「不追連續急漲」是二度上車 SOP 的建議，不在 §8 絕對禁令內 → 警示，不否決。
    # 門檻 4.0 個 ATR 約為全樣本 95 百分位；用 ATR 標準化才不會冤枉低波動標的。
    dev_atr = (px - last.sma20) / last.atr20
    if dev_atr > DEV_WARN:
        print(f"\n  【警示】距 SMA20 乖離 {px/last.sma20-1:+.1%} = {dev_atr:.1f} 個 ATR "
              f"（> {DEV_WARN:.0f}，約全樣本 95 百分位）")
        print(f"    §7「不追連續急漲」：乖離大 → 停損遠 → 盈虧比爛。"
              f"考慮等回檔至 SMA20/EMA21 附近，或先用 20~30% 試單。")
        print(f"    這不是否決條件，是要你自己確認：現在進場的停損 {stop:.2f} "
              f"（-{1-stop/px:.1%}）你接受嗎？")

    # 三個上限取最小 → 可買張數
    cap_risk = NW * MAX_RISK / risk_sh / 1000 if risk_sh > 0 else 0
    cap_lev = max(0.0, (NW * lev_cap - ex_have)) / (rc * px * 1000)
    cap_single = max(0.0, (NW * SINGLE_CAP[code] - own_ex)) / (rc * px * 1000)
    cap = min(cap_risk, cap_lev, cap_single)

    def lots_txt(x):
        """張數一律無條件捨去；不足 1 張改用股數表示（盤中零股可買）。
        四捨五入會把 0.6 張顯示成「1 張」，而那 1 張其實過不了檢查。"""
        if x >= 1:
            return f"{int(x)} 張"
        return f"{int(x * 1000 / 10) * 10} 股" if x > 0 else "0"

    print(f"\n  【結論】{'❌ 不可執行' if fails else '✅ 可執行'}"
          + (f"　違反 {len(fails)} 項" if fails else ""))
    print(f"\n  【張數上限】1.5%風險 {lots_txt(cap_risk)}／槓桿上限 {lots_txt(cap_lev)}／"
          f"單一標的 {lots_txt(cap_single)}　→ 取最小 {lots_txt(cap)}"
          f"（首筆 1/3 = {lots_txt(cap/3)}）")

    print(f"\n  【下單卡{'（本次不可執行，以下僅供了解距離多遠）' if fails else ''}】"
          f"{a.sid} {name} · {a.horizon} · {'融資' if use_margin else '現股'}")
    print(f"    進場價    {px:8.2f}")
    print(f"    停損價    {stop:8.2f}  (-{1-stop/px:.1%})"
          + ("  ← 你指定" if a.stop else "  2×ATR 與 10 日低取較近"))
    print(f"    隱含目標  {tgt:8.2f}  (+{tgt/px-1:.1%})  ← 需你確認上方無壓力")
    print(f"    首筆部位  {lots_txt(cap/3):>8s}")
    print(f"    滿倉上限  {lots_txt(cap):>8s}")
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
