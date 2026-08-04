"""把 §3 的槓桿上限拉高，在這三年的實際資料上會怎樣（含融資追繳判定）

使用者提議「融資:現股 = 6:4」。這是**名目**比例，本策略只管曝險，所以先換算：
  每 1 元市值需自備 0.6×0.4 + 0.4×1.0 = 0.64 元 → 800 萬自備款可撐 1,250 萬市值（1.56 倍）
  有效槓桿 = 1.56 × 平均風險係數
    全部 0050 (1.09) → 1.70 倍
    使用者觀察名單平均 (2.31) → 3.61 倍

本腳本把 §3 的 A/B/C 上限等比例放大，測 1.0×（現行）到 2.4×（≈3.6 倍）。

**與其他回測最大的不同：這裡會判定融資追繳。**
一般的複利回測只會讓權益曲線變小，不會歸零；但融資有維持率 130% 的硬底線，
一旦觸及就是強制平倉，那不是帳面虧損，是**永久出局**——之後的反彈與你無關。
不模擬這一點，任何「加槓桿比較賺」的結論都是假的。
"""
import sys, pathlib
import numpy as np, pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import PRICES_ADJ, TAIEX_RAW

BASE_CAP = {"A": 1.5, "B": 1.0, "C": 0.5, "D": 0.0}
SINGLE = {"A": 0.25, "B": 0.20, "C": 0.15, "D": 0.0}
MARGIN_RATE, MAINT = 0.60, 1.30
RC_REFRESH, MIN_OBS = 21, 60
SCALES = [1.0, 1.4, 1.8, 2.4]      # 槓桿上限的放大倍數
LAGS = [1, 3, 5]                   # 每幾個交易日才調整一次部位（1 = 每天）


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


def code_of(r):
    if r.close < r.sma120 or r.dd <= -0.12:
        return "D"
    if r.close < r.sma60 or (r.atrp > 2.5 and r.close < r.sma20) or r.dd <= -0.08:
        return "C"
    if r.close < r.sma20:
        return "B"
    return "A" if (r.sma20 > r.sma60 > r.sma120 and r.atrp <= 2.0) else "B"


def main():
    idx = market()
    st = pd.read_csv(PRICES_ADJ, parse_dates=["date"], dtype={"sid": str})
    full = [s for s, g in st.groupby("sid") if len(g) > 700]
    px = st[st.sid.isin(full)].pivot(index="date", columns="sid", values="adj_close").sort_index()
    ret, sma20 = px.pct_change(), px.rolling(20).mean()
    dates = px.index.intersection(idx.index)

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

    print(f"  標的池 {len(full)} 檔　期間 {dates[0]:%Y-%m-%d} ~ {dates[-1]:%Y-%m-%d}")
    print(f"  加權指數同期最大回撤 {(idx.close/idx.close.cummax()-1).loc[dates].min():.1%}\n")
    print(f"  {'設定':>18s}{'A/B/C':>15s}{'平均有效槓桿':>13s}{'年化':>9s}"
          f"{'最大回撤':>10s}{'報酬/回撤':>10s}{'最低維持率':>11s}{'追繳':>8s}")

    # (槓桿放大倍數, 單一標的放大倍數, 調整頻率)
    RUNS = [(x, x, 1) for x in SCALES] + [(2.4, 2.4, l) for l in LAGS[1:]] \
        + [(1.8, 1.0, 1), (2.4, 1.0, 1), (1.8, 1.0, 3), (1.8, 1.0, 5)]   # 只放大槓桿
    for sc, ssc, LAG in RUNS:
        cap = {k: v * sc for k, v in BASE_CAP.items()}
        eq, curve, levs, minmr, called = 1.0, [], [], 9.9, None
        last_elig, last_c, last_lev = [], 'D', 0.0
        for i in range(1, len(dates)):
            prev, d = dates[i - 1], dates[i]
            if called:
                curve.append(eq)
                continue
            m = idx.loc[prev]
            c_ = code_of(m)
            lev = cap[c_] * float(np.clip(1.8 / m.atrp, 0.4, 1.0))
            elig = [s for s in full if px[s].loc[prev] > sma20[s].loc[prev]
                    and not np.isnan(sma20[s].loc[prev])]
            if (i - 1) % LAG:                     # 非調整日 → 沿用上次的部位
                elig, c_, lev = last_elig, last_c, last_lev
            last_elig, last_c, last_lev = elig, c_, lev
            # 調整日依訊號重設部位 → 得到市值權重與各檔權重
            wts, mv_w, ex_w = {}, 0.0, 0.0
            if elig and lev > 0:
                per = min(SINGLE[c_] * ssc, lev / len(elig))
                for s in elig:
                    w = per / rc_hist[prev][s]
                    wts[s] = w
                    mv_w += w
                    ex_w += per
            levs.append(ex_w)
            # 融資金額在「調整部位的那一刻」固定，不隨當日價格變動
            # 融資成數 60% ⇒ 市值最多 = 自備款 ÷ 40% = 2.5 倍；不足 1 倍時就是純現股
            target_mv = min(mv_w * eq, eq / (1 - MARGIN_RATE))
            borrowed = max(0.0, target_mv - eq)
            cash = eq - (target_mv - borrowed)      # 自備款中沒投入的部分，仍是你的錢
            # 當日部位報酬（部位內權重，非佔淨資產權重）
            r_pos = 0.0
            if mv_w > 0:
                for s, w in wts.items():
                    v = ret[s].loc[d]
                    r_pos += (w / mv_w) * (0.0 if np.isnan(v) else v)
            new_mv = target_mv * (1 + r_pos)
            eq = cash + new_mv - borrowed
            if borrowed > 0 and eq > 0:
                mr = new_mv / borrowed
                minmr = min(minmr, mr)
                if mr < MAINT:
                    called = d
            if eq <= 0:
                eq, called = 0.0, d
            curve.append(eq)
        c = pd.Series(curve, index=dates[1:])
        yrs = (dates[-1] - dates[1]).days / 365.25
        mdd = (c / c.cummax() - 1).min()
        ann = c.iloc[-1] ** (1 / yrs) - 1
        tag = f"{sc:.1f}×" + ("" if LAG == 1 else f" 每{LAG}日調") \
            + ("" if ssc == sc else " 單一不放大")
        print(f"  {tag:>18s}{f'{cap[chr(65)]:.1f}/{cap[chr(66)]:.1f}/{cap[chr(67)]:.1f}':>15s}"
              f"{np.mean(levs):>13.2f}{ann:>9.1%}{mdd:>10.1%}"
              f"{ann/abs(mdd):>10.2f}{minmr:>11.0%}"
              f"{'❗' + called.strftime('%y-%m-%d') if called else '  無':>8s}")

    print(f"\n  對照：加權指數買進持有 年化 "
          f"{(idx.close.loc[dates[-1]]/idx.close.loc[dates[0]])**(1/((dates[-1]-dates[0]).days/365.25))-1:.1%}"
          f"　最大回撤 {(idx.close/idx.close.cummax()-1).loc[dates].min():.1%}")
    print("\n  ⚠ 這三年是 +152% 的大多頭（§12 第 1、2 條）。**在這種環境下加槓桿都打不贏的話，")
    print("     真空頭裡只會更糟。** 追繳一旦發生就是永久出局，之後的反彈與你無關——")
    print("     複利回測的『最大回撤』會低估這件事，因為它假設你一直在場上。")
    print()


if __name__ == "__main__":
    main()
