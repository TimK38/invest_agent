"""持股組合體檢 — 曝險、有效槓桿、追繳線、逐檔出場（§6 兩條軸線）

用法:
  python portfolio_check.py                              # 讀 positions.py 的持股紀錄（建議）
  python portfolio_check.py --hold 00981A:48:26.13:25.60 009816:85:14.65:14.30
  python portfolio_check.py --hold 00981A:50:26.13 --cash
  python portfolio_check.py --profile 朋友名字
  python portfolio_check.py --asof 2025-01-06 --hold 0050:20   # 重現當時的判斷

  不給 --hold 就從 profiles/<名字>_positions.json 讀，含每檔的成本、當初停損、是否融資。
  持股用 positions.py 維護，不要每天手打。

  --hold 格式 SID:張數[:成本價[:當初設定的停損價]]   可列多筆（會蓋過持股紀錄）
          成本與停損都給 → 印出 §6 軸線一的第一目標價（盈虧比 1:2）
          只想給停損不給成本 → 中間留空，例 00981A:48::25.60
  --cash  該組持股為現股（全額自備）；只作用於 --hold，紀錄檔以每筆自己的設定為準
  淨資產、單筆風險 %、槓桿上限皆取自 profiles/<名字>.json
"""
import sys, argparse
from datetime import datetime, time as dtime
import numpy as np, pandas as pd

import positions as pos_store
import profile_loader
from paths import PRICES_ADJ, TAIEX_RAW
pd.set_option("display.width", 200)

ATR_EXTREME = 2.5
DEFAULT_RC = 1.8          # 資料不足時的保守預設
SINGLE_CAP = {"A": 0.25, "B": 0.20, "C": 0.15, "D": 0.0}   # §3 單一標的曝險上限
STATE_RANK = {"D": 0, "C": 1, "B": 2, "A": 3}              # 用來判斷狀態是升級還是降級


MARKET_CLOSE = dtime(13, 30)     # 台股收盤
# S20：盤中跌破 SMA20 但收盤守住的比率（本專案資料實測，2023-07~2026-08）
INTRADAY_FAKE = "大盤 15%、個股平均 16%（00981A 高達 34%）"


def freshness(last_date, now=None):
    """S18：資料有多新？回傳 (是否可視為當日收盤判定, 說明字串)

    §6 是**收盤**判定。若在盤中或非交易日提問，工具給的動作其實是「上一個收盤」的，
    不標明就會被當成今天的指令——那是最容易誤判的一種 bug，而且不會報錯。
    """
    now = now or datetime.now()
    last, today = pd.Timestamp(last_date).date(), now.date()
    if last == today:
        return True, f"當日收盤資料（{last}）"
    gap = (today - last).days
    if today.weekday() >= 5:
        return False, (f"⚠ 今天 {today}（{'週六' if today.weekday()==5 else '週日'}）非交易日"
                       f" → 以下全部依 **{last} 收盤** 判定")
    if now.time() < MARKET_CLOSE:
        return False, (f"⚠ 今天 {today} **尚未收盤**（現在 {now:%H:%M}，收盤 13:30）"
                       f" → 以下全部依 **{last} 收盤** 判定，不是今天的盤")
    return False, (f"⚠ 今天 {today} 已收盤，但資料只到 **{last}**（{gap} 天前）"
                   f" → 可能是假日，也可能是**資料未更新**。"
                   f"若今天有開盤，先跑 fetch/fetch_twse.py 與 fetch/fetch_stocks.py")


def vol_warning(r):
    """§2 的波動警訊（v1.7 起加上價格確認）

    ATR% 高於門檻**且**收盤跌破 SMA20 才算警訊。ATR 不分方向——急漲一樣把它推高，
    舊版只看 ATR% 會把「漲得太快」與「要崩了」判成同一件事。
    實測（research/analyze9_atr_gate.py）：ATR%>2.5 獨自觸發 C 的 42 天全落在
    「波動高但價格沒壞」，代價是漲幅捕捉從 62% 掉到 43%。
    """
    return r.atrp > ATR_EXTREME and r.close < r.sma20


def taiex_state(asof=None):
    """asof: 只用該日（含）以前的資料，用於重現當時的判斷，避免用到未來資料"""
    d = pd.read_csv(TAIEX_RAW, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    if asof is not None:
        d = d[d.date <= pd.Timestamp(asof)].reset_index(drop=True)
        if d.empty:
            sys.exit(f"{asof} 之前沒有大盤資料")
    c, h, l = d.close, d.high, d.low
    for n in (20, 60, 120):
        d[f"sma{n}"] = c.rolling(n).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d["atrp"] = tr.rolling(20).mean() / c * 100
    d["ret"] = c.pct_change()
    d["dd"] = c / c.cummax() - 1
    r = d.iloc[-1]
    if r.close < r.sma120 or r.dd <= -0.12:
        code = "D"
    elif r.close < r.sma60 or vol_warning(r) or r.dd <= -0.08:
        code = "C"
    elif r.close < r.sma20:
        code = "B"
    else:
        code = "A" if (r.sma20 > r.sma60 > r.sma120 and r.atrp <= 2.0) else "B"
    return d, r, code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", nargs="+", metavar="SID:張數[:成本[:停損]]",
                    help="不給就讀 positions.py 的持股紀錄")
    ap.add_argument("--net-worth", type=float, help="覆寫 profile 的淨資產")
    ap.add_argument("--cash", action="store_true", help="這批持股為現股（無融資）")
    ap.add_argument("--asof", help="只用該日(含)以前的資料重現當時判斷 YYYY-MM-DD")
    profile_loader.add_arg(ap)
    a = ap.parse_args()

    cfg = profile_loader.load(a.profile)
    LEV_CAP = cfg["leverage_caps"]
    MAX_RISK_PCT = cfg["max_risk_per_trade"]
    margin_rate = cfg["margin_rate"]
    mix = cfg.get("asset_mix") or {"stable_target": 0.72, "tolerance": 0.10, "stable_rc_max": 1.2}

    idx, mstate, code = taiex_state(a.asof)
    # S8：與前一交易日比對，判斷狀態是不是剛降級 —— §3 規定降槓桿必須當日或次日完成
    prev_code, days_since = None, None
    if len(idx) >= 2:
        _, _, prev_code = taiex_state(idx.date.iloc[-2])
        for k in range(2, min(len(idx), 40)):
            _, _, c_k = taiex_state(idx.date.iloc[-k])
            if c_k != code:
                days_since = k - 1
                break
    pos_coef = float(np.clip(1.8 / mstate.atrp, 0.4, 1.0))
    target_lev = LEV_CAP[code] * pos_coef
    NW = a.net_worth or cfg["net_worth"]

    st = pd.read_csv(PRICES_ADJ, parse_dates=["date"], dtype={"sid": str})
    if a.asof:
        st = st[st.date <= pd.Timestamp(a.asof)]
    mret = idx.set_index("date").ret

    # 持股來源：--hold 優先，否則讀 positions.py 的紀錄（含每筆自己的融資設定）
    if a.hold:
        specs, src = [], "指令參數 --hold"
        for spec in a.hold:
            p = spec.split(":")
            specs.append({"sid": p[0], "lots": float(p[1]),
                          "cost": float(p[2]) if len(p) > 2 and p[2] else None,
                          "stop": float(p[3]) if len(p) > 3 and p[3] else None,
                          "margin": cfg["margin_allowed"] and not a.cash})
    else:
        store = pos_store.load(cfg)
        specs = store["positions"]
        src = f"持股紀錄 {pos_store.store_path(cfg).name}（更新於 {store.get('updated', '—')}）"
        if not specs:
            # S17：空手時「現在可以進場嗎」才是真正的問題，不該只丟一句錯誤訊息
            sig = mstate.close > mstate.sma20 and mstate.close > mstate.sma60
            print("=" * 80)
            print(f"  設定檔 {cfg['_name']}   淨資產 {NW:,.0f}   **目前空手**")
            print(f"  盤面（{mstate.date:%Y-%m-%d}）  狀態【{code}】  加權 {mstate.close:,.0f}  "
                  f"距高點 {mstate.dd:.1%}  大盤 ATR% {mstate.atrp:.2f}")
            if not a.asof:
                print(f"  {freshness(mstate.date)[1]}")
            print("=" * 80)
            print(f"\n  ── 現在可以進場嗎（§7 兩層都要成立）" + "─" * 38)
            print(f"    【大盤層級】收盤 > 大盤 SMA20 且 > 大盤 SMA60"
                  f"　{'✅ 成立' if sig else '❌ 不成立'}")
            for nm, v in (("大盤 SMA20", mstate.sma20), ("大盤 SMA60", mstate.sma60)):
                d_ = mstate.close / v - 1
                print(f"        {nm} {v:,.0f}　收盤 {mstate.close:,.0f}"
                      f"（{d_:+.1%}）{'▲ 已站上' if d_ > 0 else '▼ 尚未站回'}")
            if code in "CD":
                print(f"    ⚠ 但大盤為 {code} 狀態，§3「只出不進」對現股與融資一律適用——"
                      f"訊號成立之前不要進場")
            elif sig:
                print(f"    【個股層級】接著要逐檔看：回檔靠近它自己的 SMA20／EMA21、"
                      f"回檔量縮、重新轉強")
                print(f"    【部位】首波只建 1/3，有效槓桿 ≤ "
                      f"{LEV_CAP['C'] * pos_coef:.2f} 倍，且**只能用現股**"
                      f"（狀態多半仍是 B2，§3 禁止新增融資）")
            print(f"\n  ── 目前上限 " + "─" * 60)
            print(f"    有效槓桿上限 {LEV_CAP[code]} × 波動係數 {pos_coef:.2f} = {target_lev:.2f} 倍"
                  f"　→ 曝險預算 {NW * target_lev:,.0f}")
            print(f"    單一標的上限 {SINGLE_CAP[code]:.0%}　→ 單檔曝險上限 "
                  f"{NW * SINGLE_CAP[code]:,.0f}")
            print(f"    單筆風險上限 {MAX_RISK_PCT:.1%}　→ {NW * MAX_RISK_PCT:,.0f}")
            print(f"\n  想買特定標的請跑：./envest_agent/bin/python buy_check.py <代號> <張數>\n")
            return

    rows, detail = [], {}
    for spec in specs:
        sid, lots = spec["sid"], float(spec["lots"])
        cost, ustop, is_margin = spec.get("cost"), spec.get("stop"), spec.get("margin", False)
        g = st[st.sid == sid].sort_values("date").set_index("date").copy()
        if g.empty:
            print(f"⚠ {sid} 無資料，請先執行: python fetch/fetch_stocks.py {sid} --since 2023-07")
            continue
        c, h, l = g.adj_close, g.adj_high, g.adj_low
        for n in (20, 60, 120):
            g[f"sma{n}"] = c.rolling(n).mean()
        g["ema8"] = c.ewm(span=8, adjust=False).mean()      # §6 軸線二：跌破出 1/2
        g["ema21"] = c.ewm(span=21, adjust=False).mean()
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        g["atr20"] = tr.rolling(20).mean()
        g["r"] = c.pct_change()
        g["dd"] = c / c.cummax() - 1

        j = pd.concat([g.r.rename("r"), mret.rename("m")], axis=1, sort=True).dropna()
        if len(j) >= 60:
            dn = j[j.m < 0]
            beta_dn = np.cov(dn.r, dn.m)[0, 1] / np.var(dn.m)
            volr = (j.r.std()) / j.m.std()
            rc, note = max(beta_dn, volr), ("樣本偏短" if len(j) < 250 else "")
        else:
            rc, note = DEFAULT_RC, "樣本不足→用預設1.8"

        r = g.iloc[-1]
        mv = lots * 1000 * r.adj_close
        # §4 資產結構分類：風險係數 ≤ 門檻 且 未使用融資 → 穩健，其餘皆為積極
        klass = "穩健" if (rc <= mix["stable_rc_max"] and not is_margin) else "積極"
        detail[sid] = (g, r, rc, cost, ustop, lots, is_margin)
        rows.append({"標的": f"{sid} {g.name.iloc[-1]}", "類別": klass, "張數": lots,
                     "資": "融" if is_margin else "現",
                     "收盤": r.adj_close, "市值": mv, "風險係數": round(rc, 2), "曝險": mv * rc,
                     "距高點": f"{r.dd:.1%}", "E8": "▲" if r.adj_close > r.ema8 else "▼",
                     "S20": "▲" if r.adj_close > r.sma20 else "▼",
                     "S60": "▲" if r.adj_close > r.sma60 else "▼", "樣本": len(j), "備註": note})

    if not rows:
        sys.exit("沒有可分析的持股")
    D = pd.DataFrame(rows)
    mv_tot, ex_tot = D.市值.sum(), D.曝險.sum()
    margin = D[D.資 == "融"].市值.sum() * margin_rate

    print("=" * 80)
    print(f"  設定檔 {cfg['_name']}   淨資產 {NW:,.0f}   持股來源：{src}")
    print(f"  盤面（{mstate.date:%Y-%m-%d}）  狀態【{code}】  加權 {mstate.close:,.0f}  "
          f"距高點 {mstate.dd:.1%}  大盤 ATR% {mstate.atrp:.2f}")
    if not a.asof:
        fresh, fmsg = freshness(mstate.date)
        print(f"  {fmsg}")
    print(f"  有效槓桿上限 {LEV_CAP[code]} × 波動係數 {pos_coef:.2f} = 【{target_lev:.2f} 倍】"
          + ("   融資須為 0" if code in "CD" else ""))
    if prev_code and prev_code != code:
        arrow = "降級" if STATE_RANK[code] < STATE_RANK[prev_code] else "升級"
        print(f"  ❗ 大盤狀態今日{arrow}：{prev_code} → {code}"
              + ("　§3：降槓桿必須**今日或次一交易日**完成，不得再觀察"
                 if arrow == "降級" else ""))
    elif days_since is not None and STATE_RANK[code] <= 1:
        print(f"  ※ 已在 {code} 狀態第 {days_since} 個交易日")
    print("=" * 80)
    print(D.assign(市值=D.市值.map("{:,.0f}".format), 曝險=D.曝險.map("{:,.0f}".format)).to_string(index=False))

    print("\n  ── 曝險總覽 " + "─" * 60)
    print(f"  淨資產         {NW:>12,.0f}")
    print(f"  持股總市值     {mv_tot:>12,.0f}   （佔淨資產 {mv_tot/NW:.1%}）")
    if margin:
        print(f"  融資金額       {margin:>12,.0f}   自備款 {mv_tot-margin:>10,.0f}")
    print(f"  風險加權曝險   {ex_tot:>12,.0f}")
    ok = ex_tot / NW <= target_lev
    print(f"  有效槓桿       {ex_tot/NW:>12.2f} 倍   上限 {target_lev:.2f} 倍   {'✅ 合規' if ok else '❌ 超標'}")
    if not ok:
        over = ex_tot - NW * target_lev
        print(f"  ▶ 需降低曝險 {over:,.0f} 元（約當市值 {over/(ex_tot/mv_tot):,.0f} 元）")
    else:
        # 加碼空間受兩條限制：§3 槓桿上限、以及自備款。顯示哪一條先綁住。
        room_ex = NW * target_lev - ex_tot
        room_cash = NW - (mv_tot - margin)
        buy_cash = room_cash
        buy_marg = room_cash / (1 - margin_rate) if cfg["margin_allowed"] else room_cash
        print(f"  ▶ 加碼空間　槓桿剩 {room_ex:,.0f} 曝險　│　"
              f"自備款剩 {room_cash:,.0f} → 現股可買 {buy_cash:,.0f}"
              + (f"、融資可買 {buy_marg:,.0f}" if cfg["margin_allowed"] else ""))
        if buy_marg > 0:
            need_rc = room_ex / buy_marg
            if need_rc > 2.2:
                print(f"    綁住你的是**自備款**，不是槓桿上限："
                      f"把自備款用滿也只能再加 {buy_marg*2.2:,.0f} 曝險"
                      f"（即使買風險係數 2.2 的標的），仍達不到上限。")
            elif need_rc > 1.2:
                print(f"    要用滿槓桿上限，新部位的加權風險係數需 ≥ {need_rc:.2f}"
                      f"（0050 為 1.09、00981A 1.43、聯電 1.75、0050正2 2.11）。")
            else:
                print(f"    綁住你的是**槓桿上限**：自備款還夠，但曝險只剩 {room_ex:,.0f}。")

    # ── §4 資產結構 ──
    print("\n  ── 資產結構（§4：依風險係數分類，不依名稱）" + "─" * 34)
    stable_mv = D[D.類別 == "穩健"].市值.sum()
    aggr_mv = D[D.類別 == "積極"].市值.sum()
    tgt, tol = mix["stable_target"], mix["tolerance"]
    actual = stable_mv / mv_tot if mv_tot else 0.0
    print(f"    穩健（風險係數 ≤ {mix['stable_rc_max']} 且現股）  {stable_mv:>11,.0f}   {actual:>6.1%}"
          f"   目標 {tgt:.0%}")
    print(f"    積極（其餘）                        {aggr_mv:>11,.0f}   {1-actual:>6.1%}"
          f"   目標 {1-tgt:.0%}")
    dev = actual - tgt
    if abs(dev) <= tol:
        print(f"    ▶ 偏離 {dev:+.1%}，在容許範圍（±{tol:.0%}）內 ✅")
    else:
        need = (tgt * mv_tot - stable_mv)
        print(f"    ▶ 偏離 {dev:+.1%}，超出容許範圍（±{tol:.0%}）❌")
        print(f"      需將約 {abs(need):,.0f} 元從{'積極轉到穩健' if need > 0 else '穩健轉到積極'}"
              f"（併入既有進出場動作即可，勿為此單獨交易）")

    if margin:
        print("\n  ── 融資追繳線（維持率 130%）" + "─" * 46)
        for sid, (g, r, rc, cost, ustop, lots, is_margin) in detail.items():
            if not is_margin:
                continue
            call = (cost or r.adj_close) * 1.30 * margin_rate
            print(f"    {sid:8s} 現價 {r.adj_close:7.2f}   追繳 {call:7.2f}（-{1-call/r.adj_close:.0%}）"
                  f"   ATR {r.atr20:.2f}/日 → 約 {(r.adj_close-call)/r.atr20:.1f} 個 ATR")

    todo = []          # S14：跨標的的今日必做，最後依嚴重度排序輸出
    print("\n  ── 逐檔出場（§6 兩條軸線，取先觸發者；每日收盤判定）" + "─" * 22)
    if not a.asof:
        fresh, _ = freshness(mstate.date)
        if not fresh:
            print(f"    ⚠ 以下動作依 {mstate.date:%Y-%m-%d} **收盤**判定，不是即時的。")
            print(f"      §6 是收盤判定規則。**盤中假訊號率**（盤中跌破 SMA20 但收盤守住）："
                  f"{INTRADAY_FAKE}——照盤中訊號賣會被洗掉。")
            print(f"      要在今天執行出場，請於 13:20~13:25 用當下價格再判一次。")
    for sid, (g, r, rc, cost, ustop, lots, is_margin) in detail.items():
        stop = max(r.adj_close - 2 * r.atr20, g.adj_close.tail(10).min() * 0.99)
        risk_sh = r.adj_close - stop
        max_lots = NW * MAX_RISK_PCT / risk_sh / 1000 if risk_sh > 0 else 0
        # §6 軸線一第一階：盈虧比 1:2 的目標價，需成本與當初停損才算得出來
        tgt1 = cost + 2 * (cost - ustop) if (cost and ustop and cost > ustop) else None

        print(f"\n    ◆ {sid} {g.name.iloc[-1]}   {lots:g} 張 @ {r.adj_close:.2f}"
              + (f"   成本 {cost:.2f}（損益 {r.adj_close/cost-1:+.1%}）" if cost else ""))
        s120 = f"  SMA120 {r.sma120:.2f} {'▲' if r.adj_close>r.sma120 else '▼'}" if not np.isnan(r.sma120) else "  SMA120 樣本不足"
        print(f"        EMA8 {r.ema8:.2f} {'▲' if r.adj_close>r.ema8 else '▼'}   "
              f"SMA20 {r.sma20:.2f} {'▲' if r.adj_close>r.sma20 else '▼'}   "
              f"SMA60 {r.sma60:.2f} {'▲' if r.adj_close>r.sma60 else '▼'}   "
              f"EMA21 {r.ema21:.2f} {'▲' if r.adj_close>r.ema21 else '▼'}{s120}")
        if ustop:
            tight = (r.adj_close - ustop) / r.atr20
            print(f"        當初停損 {ustop:.2f}（距現價 -{1-ustop/r.adj_close:.1%}，{tight:.2f} 個 ATR）"
                  + ("   ⚠ 不足 2 ATR，落在雜訊內" if tight < 2 else ""))
        print(f"        建議停損 {stop:.2f}（-{1-stop/r.adj_close:.1%}；2×ATR 與 10 日低取較近）   "
              f"風險{MAX_RISK_PCT:.1%}反推上限 {max_lots:.0f} 張，現有 {lots:g} 張 "
              f"{'❌ 超過' if lots > max_lots else '✅'}")
        # S9：沒有新交易也可能違規 —— 漲上去或狀態降級都會讓既有部位被動超標
        own_ex = lots * 1000 * r.adj_close * rc
        cap_ex = NW * SINGLE_CAP[code]
        over_single = own_ex - cap_ex
        print(f"        單一標的曝險 {own_ex:,.0f}（{own_ex/NW:.1%}）　{code} 狀態上限 "
              f"{SINGLE_CAP[code]:.0%}　"
              + (f"❌ 超標 {over_single:,.0f}，需減 {over_single/(rc*r.adj_close*1000)*1000:,.0f} 股"
                 if over_single > 0 else "✅"))
        if tgt1:
            print(f"        第一目標 {tgt1:.2f}（盈虧比 1:2，成本 +{tgt1/cost-1:.1%}）   "
                  + ("✅ 已達" if r.adj_close >= tgt1 else f"尚差 {tgt1/r.adj_close-1:+.1%}"))
        elif cost:
            print(f"        第一目標 無法計算 → 補上當初停損價：--hold {sid}:{lots:g}:{cost:.2f}:<停損>")

        # §6 兩條軸線取先觸發者；同一檔同日多條觸發，取最嚴重的。
        # pri 數字越小越急，供下方【今日必做】跨標的排序（S14）。
        if ustop and r.adj_close < ustop:
            pri, act = 1, f"❗ 跌破當初停損 {ustop:.2f} → 全部出清（不討論、不等反彈）"
        elif r.adj_close < r.sma20:
            pri, act = 2, f"❗ 收盤跌破 SMA20 {r.sma20:.2f} → 全部出清（軸線二）"
        elif over_single > 0:
            pri, act = 3, (f"❗ 單一標的曝險超標 {over_single:,.0f} → 減至 "
                           f"{cap_ex/(rc*r.adj_close*1000)*1000:,.0f} 股以內")
        elif r.adj_close < r.ema8:
            pri, act = 4, f"⚠ 收盤跌破 EMA8 {r.ema8:.2f}（仍在 SMA20 之上）→ 出 1/2（軸線二）"
        elif tgt1 and r.adj_close >= tgt1:
            pri, act = 5, f"達第一目標 {tgt1:.2f} → 賣 30%，停損上移至成本 {cost:.2f}（軸線一）"
        else:
            pri, act = 9, "續抱。停損只能往上移，永遠不能往下移"
        print(f"        ▶ {act}")
        if pri < 9:
            todo.append((pri, sid, g.name.iloc[-1], act, is_margin, rc))

    # ── S14：跨標的的今日必做，依嚴重度排序 ──
    print("\n  ── 今日必做（按優先序）" + "─" * 50)
    n = 0
    if code in "CD":
        n += 1
        print(f"    {n}. ❗ 大盤 {code} 狀態：總曝險降至 {target_lev:.2f} 倍以內"
              + ("、**融資餘額歸零**" if margin else "")
              + ("　※ 今日剛降級，§3 要求今日或次一交易日完成"
                 if prev_code and prev_code != code
                 and STATE_RANK[code] < STATE_RANK[prev_code] else ""))
        print(f"       先減融資部位，再減風險係數高的標的"
              f"（目前最高：{max(todo, key=lambda x: x[5])[1] if todo else '—'}）")
    if not ok:
        n += 1
        print(f"    {n}. ❗ 有效槓桿 {ex_tot/NW:.2f} > 上限 {target_lev:.2f}"
              f"　→ 降低曝險 {ex_tot - NW*target_lev:,.0f}")
    for pri, sid, nm, act, is_m, _ in sorted(todo):
        n += 1
        print(f"    {n}. {sid} {nm}{'（融資）' if is_m else ''}　{act}")
    if n == 0:
        print("    （無）所有部位都在規則內，續抱。停損只能往上移。")
    print()


if __name__ == "__main__":
    main()
