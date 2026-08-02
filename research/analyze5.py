"""台股版市場廣度 — 0051(中型100) ÷ 0050(權值50)，測試頂部/底部背離"""
import numpy as np, pandas as pd
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import TAIEX_RAW, TAIEX_ENRICHED, STOCKS_ADJ, STOCKS_RAW
pd.set_option("display.width", 200)

idx = pd.read_csv(TAIEX_ENRICHED, parse_dates=["date"]).set_index("date")
_st = pd.read_csv(STOCKS_ADJ, parse_dates=["date"], dtype={"sid": str})
s50 = _st[_st.sid == "0050"].set_index("date").adj_close       # 權值 50
mid = _st[_st.sid == "0051"].set_index("date").adj_close       # 中型 100

d = pd.concat([idx.close.rename("idx"), s50.rename("big"), mid.rename("mid")], axis=1, sort=True).dropna()
d["ratio"] = d["mid"] / d["big"]
d["ratio_ma20"] = d.ratio.rolling(20).mean()
d["ratio_ma60"] = d.ratio.rolling(60).mean()
d["idx_dd"] = d.idx / d.idx.cummax() - 1
d["ratio_dd"] = d.ratio / d.ratio.cummax() - 1

print("=" * 78); print("【A】廣度比 (0051中型100 ÷ 0050權值50) 基本統計"); print("=" * 78)
print(f"  期間 {d.index[0]:%Y-%m-%d} ~ {d.index[-1]:%Y-%m-%d}  {len(d)} 天")
print(f"  比值 {d.ratio.iloc[0]:.4f} -> {d.ratio.iloc[-1]:.4f}  ({d.ratio.iloc[-1]/d.ratio.iloc[0]-1:+.1%})")
print(f"  = 三年來中型股相對權值股 {'走弱(權值主導)' if d.ratio.iloc[-1] < d.ratio.iloc[0] else '走強(廣度healthy)'}")

print("\n" + "=" * 78); print("【B】頂部背離測試：指數創新高時，廣度比在做什麼？"); print("=" * 78)
newhigh = (d.idx >= d.idx.cummax() * 0.999)
d["ratio_slope20"] = d.ratio / d.ratio.shift(20) - 1
fwd = {n: d.idx.shift(-n) / d.idx - 1 for n in (10, 20, 60)}
rows = []
for lbl, m in [("指數創新高 + 廣度20日走強", newhigh & (d.ratio_slope20 > 0.01)),
               ("指數創新高 + 廣度20日走平", newhigh & (d.ratio_slope20.between(-0.01, 0.01))),
               ("指數創新高 + 廣度20日走弱(背離)", newhigh & (d.ratio_slope20 < -0.01))]:
    m = m.fillna(False)
    if m.sum() < 5: continue
    r = {"情境": lbl, "天數": int(m.sum())}
    for n in (10, 20, 60):
        r[f"未來{n}日"] = f"{fwd[n][m].mean()*100:+.2f}%"
        r[f"{n}日勝率"] = f"{(fwd[n][m] > 0).mean()*100:.0f}%"
    mdd = pd.Series([(d.idx.iloc[i:i+40].min() / d.idx.iloc[i] - 1) for i in range(len(d))], index=d.index)
    r["未來40日最深跌(中位)"] = f"{mdd[m].median()*100:.1f}%"
    rows.append(r)
print(pd.DataFrame(rows).to_string(index=False))

print("\n" + "=" * 78); print("【C】2026年6月創高前後：廣度有沒有提前示警？"); print("=" * 78)
w = d.loc["2026-05-15":"2026-07-31"].copy()
w["廣度20日變化"] = (w.ratio_slope20 * 100).round(2)
w["距高點%"] = (w.idx_dd * 100).round(1)
w["廣度距高點%"] = (w.ratio_dd * 100).round(1)
print(w[["idx", "距高點%", "ratio", "廣度20日變化", "廣度距高點%"]].iloc[::3].to_string())

pk = d.loc["2026-06-22"]
print(f"\n  大盤高點日 2026-06-22:")
print(f"    指數 {pk.idx:,.0f} (歷史高點)   廣度比 {pk.ratio:.4f}")
print(f"    廣度比距其自身高點 {pk.ratio_dd:+.1%}   前20日變化 {pk.ratio_slope20*100:+.2f}%")
rmax_d = d.ratio.idxmax()
print(f"    廣度比的高點出現在 {rmax_d:%Y-%m-%d}，領先指數高點 {(pd.Timestamp('2026-06-22')-rmax_d).days} 天")

print("\n" + "=" * 78); print("【D】底部背離測試：指數破底時廣度有沒有跟著破"); print("=" * 78)
lo = (d.idx_dd <= -0.08)
rows = []
for lbl, m in [("指數回撤>8% + 廣度同步破底(<-8%)", lo & (d.ratio_dd <= -0.08)),
               ("指數回撤>8% + 廣度相對抗跌(>-8%)", lo & (d.ratio_dd > -0.08))]:
    m = m.fillna(False)
    if m.sum() < 5: continue
    r = {"情境": lbl, "天數": int(m.sum())}
    for n in (20, 60):
        r[f"未來{n}日"] = f"{fwd[n][m].mean()*100:+.2f}%"
        r[f"{n}日勝率"] = f"{(fwd[n][m] > 0).mean()*100:.0f}%"
    rows.append(r)
print(pd.DataFrame(rows).to_string(index=False) if rows else "  樣本不足")

print("\n" + "=" * 78); print("【E】把廣度加進濾網，有沒有比 ATR% 更好？"); print("=" * 78)
ind = idx.reindex(d.index)
c = d.idx
sma20 = c.rolling(20).mean(); ema8 = c.ewm(span=8, adjust=False).mean(); sma60 = c.rolling(60).mean()
pos, cur = [], 0.0
for i in range(len(d)):
    if np.isnan(sma20.iloc[i]): pos.append(0.0); continue
    cur = 0.0 if c.iloc[i] < sma20.iloc[i] else (min(cur, .5) if c.iloc[i] < ema8.iloc[i] else 1.0)
    pos.append(cur)
p = pd.Series(pos, index=d.index)
ret = c.pct_change()
yrs = (d.index[-1] - d.index[0]).days / 365.25
tests = {
    "① 基準：跌破SMA20全出": p,
    "② + ATR%<2.5": p * (ind.atrp < 2.5).astype(float),
    "③ + 廣度20日不走弱": p * (d.ratio_slope20 > -0.01).astype(float),
    "④ + ATR%<2.5 + 廣度不走弱": p * ((ind.atrp < 2.5) & (d.ratio_slope20 > -0.01)).astype(float),
}
rows = []
for k, pp in tests.items():
    pp = pp.fillna(0)
    rr = (pp.shift(1).fillna(0) * ret).fillna(0); eq = (1 + rr).cumprod()
    jun = d.index >= "2026-06-22"
    rows.append({"濾網": k, "年化": f"{eq.iloc[-1]**(1/yrs)-1:+.1%}", "最大回撤": f"{(eq/eq.cummax()-1).min():.1%}",
                 "6/22起": f"{(1+rr[jun]).cumprod().iloc[-1]-1:+.1%}", "持倉比": f"{pp.mean():.0%}"})
print(pd.DataFrame(rows).to_string(index=False))
print(f"\n  [對照] 買進持有 年化 {(c.iloc[-1]/c.iloc[0])**(1/yrs)-1:+.1%}  最大回撤 {d.idx_dd.min():.1%}  6/22起 {c.iloc[-1]/c.loc['2026-06-22']-1:+.1%}")
