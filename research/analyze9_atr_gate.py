"""§2 的 ATR% > 2.5 → C 狀態，這條門檻該不該改

問題來源：2026/06/08~06/26 大盤收盤全程在 SMA60 之上（+8.5%~+18.7%）、距高點最深 -7.1%、
06/18 與 06/22 還創新高，但 ATR% 落在 2.64~2.91，於是被判成 C。C 禁止新開倉且融資須歸零，
結果是 06/10 被掃出場後，整段 44→70 的第二腿完全上不了車。

**ATR 不分方向**：急漲會拉高 ATR，急跌也會。這條門檻把「漲得太快」和「要崩了」判成同一件事。

測五個變體（只動 C 的波動條件，A 狀態的 ATR% ≤ 2.0 與其他條件都不動）：
  V0 現行            ATR% > 2.5
  V1 加價格確認      ATR% > 2.5 且 收盤 < SMA20      ← 急漲不算警訊
  V2 單純放寬        ATR% > 3.0
  V3 加回撤確認      ATR% > 2.5 且 距高點 ≤ -4%
  V4 完全拿掉        （測這條門檻到底貢獻多少）

§7 的再進場訊號同樣含「ATR% < 2.5」，變體會**一併套用**，否則出場規則放寬了
但回不去，比較沒有意義。

兩個維度都要看，缺一不可：
  A. 吃到漲幅 —— 五個真實起漲點的逐日模擬（含 §7 再進場）
  B. 避開跌幅 —— 組合層級最大回撤，並**單獨檢查 2026/6/22 那個頭部**
     （§3 宣稱「ATR% < 2.5 才持倉」讓 6/22 起的損失從 -18.4% 變成 0%，
      任何變體若守不住這一點，就是拿掉了這條規則唯一被證實有效的部分）
"""
import sys, pathlib
import numpy as np, pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import STOCKS_ADJ, TAIEX_RAW

LEV_CAP = {"A": 1.5, "B": 1.0, "C": 0.5, "D": 0.0}
RC_REFRESH, MIN_OBS = 21, 60

VARIANTS = {
    "V0 現行 ATR%>2.5":          lambda r: r.atrp > 2.5,
    "V1 ATR%>2.5 且 收<SMA20":   lambda r: r.atrp > 2.5 and r.close < r.sma20,
    "V2 ATR%>3.0":               lambda r: r.atrp > 3.0,
    "V3 ATR%>2.5 且 距高≤-4%":   lambda r: r.atrp > 2.5 and r.dd <= -0.04,
    "V4 拿掉 ATR 條件":           lambda r: False,
}

# V1b：C 閘門用 V1（不強制出場），但 §8 禁令 3「ATR%>2.5 禁止新增融資」維持原樣（進場側）。
# 這對應「高波動時可以續抱，但不可以加碼或新開融資」——正是使用者指出的不對稱：
# 被強制出場是問題，禁止追高不是。
V1B = ("V1b V1 + 保留§8-3進場禁令", lambda r: r.atrp > 2.5 and r.close < r.sma20,
       lambda r: r.atrp > 2.5)

# 使用者實際問到的五個起漲點
CASES = [("2454", "2026-04-21", 2090.0), ("6182", "2026-05-21", 63.60),
         ("2303", "2026-04-17", 73.00), ("2605", "2026-01-16", 27.85),
         ("3481", "2026-05-05", 27.25)]


def market():
    d = pd.read_csv(TAIEX_RAW, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    c, h, l = d.close, d.high, d.low
    for n in (20, 60, 120):
        d[f"sma{n}"] = c.rolling(n).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d["atrp"] = tr.rolling(20).mean() / c * 100
    d["ret"] = c.pct_change()
    d["dd"] = c / c.cummax() - 1
    return d.set_index("date")


def code_of(r, vol_bad):
    if r.close < r.sma120 or r.dd <= -0.12:
        return "D"
    if r.close < r.sma60 or vol_bad(r) or r.dd <= -0.08:
        return "C"
    if r.close < r.sma20:
        return "B"
    return "A" if (r.sma20 > r.sma60 > r.sma120 and r.atrp <= 2.0) else "B"


def stock_frame(st, sid):
    g = st[st.sid == sid].sort_values("date").set_index("date").copy()
    c = g.adj_close
    g["sma20"] = c.rolling(20).mean()
    g["ema8"] = c.ewm(span=8, adjust=False).mean()
    h, l = g.adj_high, g.adj_low
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    g["atr20"] = tr.rolling(20).mean()
    return g


def case_sim(g, idx, entry, px0, vol_bad, entry_block=None):
    """單筆交易的逐日模擬（融資），含 §7 再進場。回傳 (總報酬, 持有期間最大回撤, 動作紀錄)"""
    e = g.loc[entry]
    stop = max(px0 - 2 * e.atr20, g.loc[:entry].adj_close.tail(10).min() * 0.99)
    held, cost, eq, half, log = 1.0, px0, 1.0, False, []
    peak_eq, mdd = 1.0, 0.0
    fwd = g.loc[entry:].iloc[1:]
    for d, r in fwd.iterrows():
        if d not in idx.index:
            continue
        m = idx.loc[d]
        c_ = code_of(m, vol_bad)
        if held > 0:
            cur = eq * (1 + held * (r.adj_close / cost - 1))
            peak_eq = max(peak_eq, cur)
            mdd = min(mdd, cur / peak_eq - 1)
            sold, why = 0.0, ""
            if c_ in "CD":
                sold, why = held, f"大盤{c_}"
            elif r.adj_close < stop:
                sold, why = held, "破停損"
            elif r.adj_close < r.sma20:
                sold, why = held, "破SMA20"
            elif not half and r.adj_close < r.ema8:
                sold, why, half = held / 2, "破EMA8", True
            if sold:
                eq *= (1 + sold * (r.adj_close / cost - 1))
                held -= sold
                log.append(f"{d:%m/%d} {why} @{r.adj_close:.1f}")
        elif (m.close > m.sma20 and m.close > m.sma60 and not vol_bad(m)
              and not (entry_block and entry_block(m))      # §8 禁令 3：進場側
              and r.adj_close > r.sma20):
            held, cost, half = 1.0, r.adj_close, False
            stop = max(r.adj_close - 2 * r.atr20, g.loc[:d].adj_close.tail(10).min() * 0.99)
            log.append(f"{d:%m/%d} 再進場 @{r.adj_close:.1f}")
    if held > 0:
        eq *= (1 + held * (fwd.adj_close.iloc[-1] / cost - 1))
    return eq - 1, mdd, log


def main():
    idx = market()
    st = pd.read_csv(STOCKS_ADJ, parse_dates=["date"], dtype={"sid": str})
    full = [s for s, g in st.groupby("sid") if len(g) > 700]
    px = st[st.sid.isin(full)].pivot(index="date", columns="sid", values="adj_close").sort_index()
    ret, sma20 = px.pct_change(), px.rolling(20).mean()
    dates = px.index.intersection(idx.index)
    names = st.groupby("sid").name.last()

    # 風險係數：擴張窗格，五個變體共用（與 C 門檻無關）
    rc_hist, cur = {}, {s: 1.8 for s in full}
    for i, d in enumerate(dates):
        if i % RC_REFRESH == 0:
            for s in full:
                j = pd.concat([ret[s].loc[:d].rename("r"), idx.ret.loc[:d].rename("m")],
                              axis=1).dropna()
                if len(j) >= MIN_OBS:
                    dn = j[j.m < 0]
                    cur[s] = float(max(np.cov(dn.r, dn.m)[0, 1] / np.var(dn.m),
                                       j.r.std() / j.m.std()))
        rc_hist[d] = dict(cur)

    def portfolio(vol_bad):
        eq, curve, days = 1.0, [], {"A": 0, "B": 0, "C": 0, "D": 0}
        for i in range(1, len(dates)):
            prev, d = dates[i - 1], dates[i]
            m = idx.loc[prev]
            c_ = code_of(m, vol_bad)
            days[c_] += 1
            lev = LEV_CAP[c_] * float(np.clip(1.8 / m.atrp, 0.4, 1.0))
            elig = [s for s in full if px[s].loc[prev] > sma20[s].loc[prev]
                    and not np.isnan(sma20[s].loc[prev])]
            r = 0.0
            if elig and lev > 0:
                per = min(0.25, lev / len(elig))
                for s in elig:
                    v = ret[s].loc[d]
                    r += (per / rc_hist[prev][s]) * (0.0 if np.isnan(v) else v)
            eq *= (1 + r)
            curve.append(eq)
        c = pd.Series(curve, index=dates[1:])
        yrs = (dates[-1] - dates[1]).days / 365.25
        top = c.loc["2026-06-22":]
        return {"年化": c.iloc[-1] ** (1 / yrs) - 1, "最大回撤": (c / c.cummax() - 1).min(),
                "6/22後": top.iloc[-1] / top.iloc[0] - 1,
                "6/22後最深": (top / top.cummax() - 1).min(),
                "C+D天數": days["C"] + days["D"]}

    print("=" * 100)
    print("  A. 組合層級（12 檔、§6 軸線二篩選、單一標的上限 25%、依狀態調曝險）")
    print("=" * 100)
    print(f"  {'變體':26s}{'年化':>8s}{'最大回撤':>10s}{'報酬/回撤':>10s}"
          f"{'6/22後報酬':>11s}{'6/22後最深':>11s}{'C+D天數':>9s}")
    port = {}
    for k, f in VARIANTS.items():
        p = portfolio(f)
        port[k] = p
        print(f"  {k:26s}{p['年化']:>8.1%}{p['最大回撤']:>10.1%}"
              f"{p['年化']/abs(p['最大回撤']):>10.2f}{p['6/22後']:>11.1%}"
              f"{p['6/22後最深']:>11.1%}{p['C+D天數']:>9d}")

    print("\n" + "=" * 100)
    print("  B. 五個真實起漲點（融資規則 + §7 再進場，模擬到 2026-07-31）")
    print("=" * 100)
    frames = {sid: stock_frame(st, sid) for sid, _, _ in CASES}
    tbl, mdds = {}, {}
    for sid, ds, p0 in CASES:
        g = frames[sid]
        e = pd.Timestamp(ds)
        fut = g.loc[e:].iloc[1:]
        bh = fut.adj_close.iloc[-1] / p0 - 1
        peak = fut.adj_close.max() / p0 - 1
        print(f"\n  ◆ {sid} {names[sid]}　{ds} @ {p0}　"
              f"買進持有 {bh:+.0%}　期間最高 {peak:+.0%}（{fut.adj_close.idxmax():%m/%d}）")
        for k, f in list(VARIANTS.items()) + [(V1B[0], V1B[1])]:
            eb = V1B[2] if k == V1B[0] else None
            r, mdd, log = case_sim(g, idx, e, p0, f, eb)
            tbl.setdefault(k, []).append(r / peak if peak > 0 else 0)
            mdds.setdefault(k, []).append(mdd)
            print(f"    {k:26s}{r:>+9.1%}　吃到漲幅 {r/peak:>5.0%}　"
                  f"持有期最深 {mdd:>6.1%}　{'／'.join(log[:4]) or '（無動作）'}")

    # ── C. 單一集中高 Beta 部位：§3 對這條門檻的原始論據 ──
    print("\n" + "=" * 100)
    print("  C. 單一集中部位：0050正2 於 2026-06-22（大盤頭部當天）買進")
    print("     §3 宣稱這條門檻讓 6/22 起的損失從 -18.4% 變成 0%。")
    print("     組合層級測不出它的價值（見 A），但集中部位是另一回事——這裡單獨驗。")
    print("=" * 100)
    lev = stock_frame(st, "00631L")
    w = lev.loc["2026-06-22":"2026-07-31"]
    bh = w.adj_close.iloc[-1] / w.adj_close.iloc[0] - 1
    worst = w.adj_close.min() / w.adj_close.iloc[0] - 1
    print(f"  買進持有到 7/31 {bh:+.1%}　期間最低 {worst:+.1%}")
    conc = {}
    for k, f in list(VARIANTS.items()) + [(V1B[0], V1B[1])]:
        if k == V1B[0] and V1B[2](idx.loc[pd.Timestamp("2026-06-22")]):
            conc[k] = (0.0, 0.0)
            print(f"  {k:26s}{0.0:>+9.1%}　持有期最深 {0.0:>6.1%}　"
                  f"§8 禁令 3 擋下這筆（ATR% "
                  f"{idx.loc[pd.Timestamp('2026-06-22')].atrp:.2f} > 2.5）→ 根本沒買")
            continue
        held, eq, cost, peak, mdd, ex = True, 1.0, w.adj_close.iloc[0], 1.0, 0.0, None
        for dt, r in w.iloc[1:].iterrows():
            if dt not in idx.index:
                continue
            c_ = code_of(idx.loc[dt], f)
            if held:
                cur = eq * (r.adj_close / cost)
                peak = max(peak, cur)
                mdd = min(mdd, cur / peak - 1)
                if c_ in "CD" or r.adj_close < r.sma20:
                    eq *= r.adj_close / cost
                    held, ex = False, (dt, r.adj_close, "大盤" + c_ if c_ in "CD" else "破SMA20")
        if held:
            eq *= w.adj_close.iloc[-1] / cost
        conc[k] = (eq - 1, mdd)
        print(f"  {k:26s}{eq-1:>+9.1%}　持有期最深 {mdd:>6.1%}　"
              + (f"出場 {ex[0]:%m/%d} @{ex[1]:.2f}（{ex[2]}）" if ex else "未出場"))

    print("\n" + "=" * 100)
    print("  D. 綜合")
    print("=" * 100)
    print(f"  {'變體':26s}{'五案吃到漲幅':>14s}{'五案最深':>10s}"
          f"{'集中部位6/22':>13s}{'集中最深':>10s}{'組合最大回撤':>13s}{'組合年化':>10s}")
    for k in list(VARIANTS) + [V1B[0]]:
        p, cc = port.get(k, port["V1 ATR%>2.5 且 收<SMA20"]), conc[k]
        print(f"  {k:26s}{np.mean(tbl[k]):>14.0%}{np.mean(mdds[k]):>10.1%}"
              f"{cc[0]:>+13.1%}{cc[1]:>10.1%}{p['最大回撤']:>13.1%}{p['年化']:>10.1%}")

    print("\n  ⚠ 判讀時必須記得的偏誤：")
    print("    B 段那五個進場點是**事後挑出來的大漲標的**（+56%~+198%）。在只有贏家的樣本上，")
    print("    「抱越久的變體看起來越好」是必然的——真正的代價要在輸家身上才看得到，而這裡沒有輸家。")
    print("    A 段的組合回測才是無選擇偏誤的那個；C 段代表你實際的交易方式（集中、高 Beta、融資）。")
    print("    三段結論不一致時，以 A + C 為準，B 只能當作「這條門檻確實會讓你錯過大行情」的佐證。")
    print()


if __name__ == "__main__":
    main()
