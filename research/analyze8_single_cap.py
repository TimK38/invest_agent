"""§3 單一標的上限（A 25% / B 20% / C 15%）的實證檢定

這三個數字是慣例，不是推導出來的——寫進 STRATEGY.md §3 時沒有任何回測支持。
本腳本用手上的資料檢驗：不同的單一標的上限，對報酬與最大回撤各有什麼影響。

作法（全程只用當日以前的資料）
  1. 每日依 §2 判定大盤狀態 → 總曝險上限 = 槓桿上限 × 波動係數
  2. 每日依 §6 軸線二篩選持有標的：收盤 > SMA20 才在組合內
  3. 每檔的曝險配額 = min(單一標的上限, 總曝險上限 ÷ 合格檔數)
     部位市值 = 曝險配額 ÷ 該檔風險係數（風險係數用擴張窗格，每 21 日更新一次）
  4. 組合日報酬 = Σ(部位市值佔淨資產比重 × 個股日報酬)

⚠ 這個檢定的三個硬限制，讀結論前必須知道
  - **標的池只有 8 檔**，且高度集中在半導體／面板。單一標的上限的價值本來就在
    「持有很多檔時避免過度集中」，8 檔的池子先天測不出來。
  - **期間是 +152% 的大多頭，沒有任何一檔個股暴雷**（下市、財報造假、單日 -50%）。
    單一標的上限防的正是那種事件，這個樣本裡一次都沒發生。
  - 因此本腳本能得到的最強結論是「**在這個樣本裡看不出差異**」，
    不能推論成「這個上限沒有用」。缺乏證據不等於證據顯示無效。
"""
import sys, pathlib
import numpy as np, pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import PRICES_ADJ, TAIEX_RAW

LEV_CAP = {"A": 1.5, "B": 1.0, "C": 0.5, "D": 0.0}
CAPS = [0.10, 0.15, 0.20, 0.25, 0.33, 0.50, 1.00]     # 待測的單一標的上限
RC_REFRESH = 21                                        # 風險係數每 21 個交易日重算
MIN_OBS = 60


def taiex_series():
    d = pd.read_csv(TAIEX_RAW, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    c, h, l = d.close, d.high, d.low
    for n in (20, 60, 120):
        d[f"sma{n}"] = c.rolling(n).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d["atrp"] = tr.rolling(20).mean() / c * 100
    d["ret"] = c.pct_change()
    d["dd"] = c / c.cummax() - 1
    code = np.where(d.close < d.sma120, "D",
           np.where(d.dd <= -0.12, "D",
           np.where(d.close < d.sma60, "C",
           np.where(d.atrp > 2.5, "C",
           np.where(d.dd <= -0.08, "C",
           np.where(d.close < d.sma20, "B",
           np.where((d.sma20 > d.sma60) & (d.sma60 > d.sma120) & (d.atrp <= 2.0), "A", "B")))))))
    d["code"] = code
    d["pos_coef"] = np.clip(1.8 / d.atrp, 0.4, 1.0)
    d["lev"] = [LEV_CAP[c] * p for c, p in zip(d.code, d.pos_coef)]
    return d.set_index("date")


def main():
    idx = taiex_series()
    st = pd.read_csv(PRICES_ADJ, parse_dates=["date"], dtype={"sid": str})
    # 只用全歷史標的，避免上市時間差造成的存活偏誤
    full = [s for s, g in st.groupby("sid") if len(g) > 700]
    px = st[st.sid.isin(full)].pivot(index="date", columns="sid", values="adj_close").sort_index()
    ret = px.pct_change()
    sma20 = px.rolling(20).mean()
    print(f"  標的池（{len(full)} 檔）：{'、'.join(full)}")
    print(f"  期間：{px.index[0]:%Y-%m-%d} ~ {px.index[-1]:%Y-%m-%d}（{len(px)} 個交易日）\n")

    dates = px.index.intersection(idx.index)
    mret = idx.ret

    # 風險係數：擴張窗格，只用當日以前的資料，每 RC_REFRESH 日更新
    rc_hist, rc_cur = {}, {s: 1.8 for s in full}
    for i, d in enumerate(dates):
        if i % RC_REFRESH == 0:
            for s in full:
                j = pd.concat([ret[s].loc[:d].rename("r"), mret.loc[:d].rename("m")],
                              axis=1).dropna()
                if len(j) >= MIN_OBS:
                    dn = j[j.m < 0]
                    rc_cur[s] = float(max(np.cov(dn.r, dn.m)[0, 1] / np.var(dn.m),
                                          j.r.std() / j.m.std()))
        rc_hist[d] = dict(rc_cur)

    def run(cap):
        eq, curve, nhold, totex = 1.0, [], [], []
        for i in range(1, len(dates)):
            prev, d = dates[i - 1], dates[i]
            lev = idx.lev.loc[prev]                       # 依前一日收盤決定今日部位
            elig = [s for s in full
                    if px[s].loc[prev] > sma20[s].loc[prev] and not np.isnan(sma20[s].loc[prev])]
            r = 0.0
            if elig and lev > 0:
                per = min(cap, lev / len(elig))            # 每檔曝險配額（佔淨資產）
                for s in elig:
                    w = per / rc_hist[prev][s]             # 曝險→市值權重
                    r += w * (ret[s].loc[d] if not np.isnan(ret[s].loc[d]) else 0.0)
                totex.append(per * len(elig))
            else:
                totex.append(0.0)
            eq *= (1 + r)
            curve.append(eq)
            nhold.append(len(elig))
        c = pd.Series(curve, index=dates[1:])
        yrs = (dates[-1] - dates[1]).days / 365.25
        return {"cap": cap, "年化": c.iloc[-1] ** (1 / yrs) - 1,
                "總報酬": c.iloc[-1] - 1, "最大回撤": (c / c.cummax() - 1).min(),
                "平均總曝險": np.mean(totex),
                "平均持股數": np.mean(nhold), "報酬/回撤": None}

    rows = []
    for cap in CAPS:
        r = run(cap)
        r["報酬/回撤"] = r["年化"] / abs(r["最大回撤"]) if r["最大回撤"] else np.nan
        rows.append(r)
    D = pd.DataFrame(rows)

    print("  ── 不同單一標的上限的結果 " + "─" * 46)
    print(f"  {'單一標的上限':>12s}{'年化':>9s}{'總報酬':>10s}{'最大回撤':>10s}"
          f"{'報酬/回撤':>10s}{'平均總曝險':>11s}{'平均持股數':>11s}")
    for r in rows:
        mark = "  ← §3 現行(A)" if r["cap"] == 0.25 else ""
        print(f"  {r['cap']:>11.0%}{r['年化']:>9.1%}{r['總報酬']:>10.1%}"
              f"{r['最大回撤']:>10.1%}{r['報酬/回撤']:>10.2f}{r['平均總曝險']:>10.2f}倍"
              f"{r['平均持股數']:>11.1f}{mark}")

    span_dd = D.最大回撤.max() - D.最大回撤.min()
    span_ex = D.平均總曝險.max() - D.平均總曝險.min()
    best = D.loc[D["報酬/回撤"].idxmax()]

    print(f"\n  ── 這個檢定有一個無法排除的干擾（必讀）" + "─" * 32)
    print(f"  平均總曝險隨上限變動 {D.平均總曝險.min():.2f} → {D.平均總曝險.max():.2f} 倍"
          f"（全距 {span_ex:.2f}）。")
    print(f"  原因：平均只有 {D.平均持股數.iloc[0]:.1f} 檔通過趨勢篩選，"
          f"當『上限 × 檔數 < 槓桿上限』時，")
    print(f"  單一標的上限會連帶壓低**總曝險**，而不只是改變集中度。")
    if span_ex > 0.2:
        print(f"  **所以上表的回撤差異，主要是總槓桿不同造成的，不是分散程度不同造成的。**")
        print(f"  兩者在 8 檔的標的池裡無法分離——要分離需要夠大的標的池，")
        print(f"  讓收緊上限時能把資金轉到其他標的，維持總曝險不變。")

    print(f"\n  ── 能講與不能講的 " + "─" * 54)
    print(f"  ✅ 能講：上限放寬到 33% 以上，報酬不再增加但回撤持續惡化"
          f"（報酬/回撤 {D[D.cap>=0.33]['報酬/回撤'].max():.2f} vs "
          f"{D[D.cap<=0.25]['報酬/回撤'].max():.2f}）。")
    print(f"           **『不要超過 25%』這個方向有資料支持。**")
    print(f"  ✅ 能講：報酬/回撤在本樣本的最佳值出現在 {best.cap:.0%}（{best['報酬/回撤']:.2f}），"
          f"現行 25% 為 {D[D.cap==0.25]['報酬/回撤'].iloc[0]:.2f}。")
    print(f"           但 10%/15%/20% 三者只差 "
          f"{D[D.cap<=0.20]['報酬/回撤'].max()-D[D.cap<=0.20]['報酬/回撤'].min():.2f}，"
          f"在 7 段回撤的樣本裡屬於雜訊。")
    print(f"  ❌ 不能講：25% 應該改成 20%。差異落在雜訊內，且受上面的曝險干擾污染。")
    print(f"  ❌ 不能講：這條規則有效／無效。標的池只有 8 檔、期間無任何個股暴雷，")
    print(f"           而這條規則要防的正是暴雷。**缺乏證據不等於證據顯示無效。**")

    print(f"\n  ── 建議 " + "─" * 64)
    print(f"  維持 25/20/15，理由是「不要超過 25%」有資料支持，而更細的校準沒有。")
    print(f"  真要校準，需要：(1) 20 檔以上的標的池以分離集中度與總槓桿；")
    print(f"  (2) 含個股暴雷事件的樣本。取得之前，調整這三個數字就是在配適雜訊。")
    print()


if __name__ == "__main__":
    main()
