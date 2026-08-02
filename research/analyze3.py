"""第三輪：個股 Beta 實測 + 0050正2 槓桿耗損 + 七月各標的實際跌幅"""
import numpy as np, pandas as pd
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import TAIEX_RAW, TAIEX_ENRICHED, STOCKS_ADJ, STOCKS_RAW
pd.set_option("display.width", 220)

idx = pd.read_csv(TAIEX_ENRICHED, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
st = pd.read_csv(STOCKS_ADJ, parse_dates=["date"])
mkt = idx.set_index("date")[["close", "ret", "dd", "atrp", "sma20", "sma60", "sma120"]].rename(columns={"close": "idx_close", "ret": "idx_ret"})

print("=" * 84); print("【A】實測 Beta 與波動 — 直接拿去算你的有效槓桿"); print("=" * 84)
rows = []
for nm, g in st.groupby("name"):
    g = g.sort_values("date").set_index("date")
    r = g.adj_close.pct_change()
    j = pd.concat([r.rename("r"), mkt.idx_ret], axis=1).dropna()
    beta = np.cov(j.r, j.idx_ret)[0, 1] / np.var(j.idx_ret)
    # 下跌時的 beta（大盤跌的日子）— 這才是風險真正的樣子
    dn = j[j.idx_ret < 0]
    beta_dn = np.cov(dn.r, dn.idx_ret)[0, 1] / np.var(dn.idx_ret)
    up = j[j.idx_ret > 0]
    beta_up = np.cov(up.r, up.idx_ret)[0, 1] / np.var(up.idx_ret)
    dd = (g.adj_close / g.adj_close.cummax() - 1).min()
    rows.append({"標的": nm, "Beta(全期)": round(beta, 2), "Beta(大盤下跌日)": round(beta_dn, 2),
                 "Beta(大盤上漲日)": round(beta_up, 2), "年化波動": f"{r.std()*np.sqrt(252):.0%}",
                 "3年報酬": f"{g.adj_close.iloc[-1]/g.adj_close.iloc[0]-1:+.0%}", "最大回撤": f"{dd:.0%}"})
B = pd.DataFrame(rows).sort_values("Beta(大盤下跌日)", ascending=False)
print(B.to_string(index=False))
print(f"\n  [大盤本身] 年化波動 {idx.ret.std()*np.sqrt(252):.0%}   3年報酬 {idx.close.iloc[-1]/idx.close.iloc[0]-1:+.0%}   最大回撤 {idx.dd.min():.0%}")
print("""
  重點：看「Beta(大盤下跌日)」欄。這是大盤下跌時你實際承受的倍數，
        通常高於全期 Beta —— 也就是「跌的時候跌更兇」。算槓桿要用這個數字。""")

print("\n" + "=" * 84); print("【B】0050正2 的槓桿耗損 — 它不是「0050 的兩倍」"); print("=" * 84)
a = st[st.sid == "0050"].sort_values("date").set_index("date").adj_close
b = st[st.sid == "00631L"].sort_values("date").set_index("date").adj_close
j = pd.concat([a.rename("x1"), b.rename("x2")], axis=1).dropna()
r1, r2 = j.x1.pct_change(), j.x2.pct_change()
print(f"  3年總報酬   0050 {j.x1.iloc[-1]/j.x1.iloc[0]-1:+.1%}   0050正2 {j.x2.iloc[-1]/j.x2.iloc[0]-1:+.1%}")
print(f"  『理論兩倍』應為 {(j.x1.iloc[-1]/j.x1.iloc[0]-1)*2:+.1%}  ->  實際 {j.x2.iloc[-1]/j.x2.iloc[0]-1:+.1%}"
      f"   {'超額' if (j.x2.iloc[-1]/j.x2.iloc[0]-1) > (j.x1.iloc[-1]/j.x1.iloc[0]-1)*2 else '耗損'} "
      f"{abs((j.x2.iloc[-1]/j.x2.iloc[0]-1) - (j.x1.iloc[-1]/j.x1.iloc[0]-1)*2):.1%}")
print(f"  日報酬實測倍數 (回歸斜率) = {np.cov(r2.dropna(), r1.dropna()[r2.dropna().index])[0,1]/np.var(r1.dropna()[r2.dropna().index]):.2f}")
print(f"  最大回撤   0050 {(j.x1/j.x1.cummax()-1).min():.1%}   0050正2 {(j.x2/j.x2.cummax()-1).min():.1%}")

print("\n  分年看『複利耗損』(0050正2實際報酬 − 0050報酬×2)：")
for y in (2023, 2024, 2025, 2026):
    s = j[j.index.year == y]
    if len(s) < 20: continue
    e1, e2 = s.x1.iloc[-1]/s.x1.iloc[0]-1, s.x2.iloc[-1]/s.x2.iloc[0]-1
    vol = s.x1.pct_change().std()*np.sqrt(252)
    print(f"    {y}: 0050 {e1:+7.1%}  正2實際 {e2:+7.1%}  理論2x {e1*2:+7.1%}  差 {e2-e1*2:+6.1%}   當年0050波動 {vol:.0%}")

print("\n  盤整期(大盤在SMA20上下震盪)的耗損：")
choppy = mkt.reindex(j.index)
ch = ((choppy.idx_close - choppy.sma20).abs() / choppy.idx_close < 0.02)
print(f"    盤整日數 {ch.sum()} 天   0050 累積 {(1+r1[ch]).prod()-1:+.2%}   正2 累積 {(1+r2[ch]).prod()-1:+.2%}"
      f"   理論2x {((1+r1[ch]).prod()-1)*2:+.2%}")

print("\n" + "=" * 84); print("【C】2026/6/22 高點 -> 7/30 低點，各標的實際跌幅"); print("=" * 84)
rows = []
for nm, g in st.groupby("name"):
    g = g.sort_values("date").set_index("date")
    w = g.loc["2026-06-22":"2026-07-31"]
    if w.empty: continue
    pk = w.adj_close.iloc[0]
    tr = w.adj_close.min()
    rows.append({"標的": nm, "6/22收盤": round(pk, 2), "波段最低": round(tr, 2),
                 "高低跌幅": f"{tr/pk-1:.1%}", "7/31收盤": round(w.adj_close.iloc[-1], 2),
                 "7/31 vs 6/22": f"{w.adj_close.iloc[-1]/pk-1:.1%}"})
D = pd.DataFrame(rows)
D["_s"] = D["高低跌幅"].str.rstrip("%").astype(float)
print(D.sort_values("_s").drop(columns="_s").to_string(index=False))
i0 = idx[idx.date == "2026-06-22"].close.iloc[0]
i1 = idx[idx.date >= "2026-06-22"].close.min()
print(f"\n  [加權指數] 6/22 {i0:,.0f} -> 最低 {i1:,.0f} = {i1/i0-1:.1%}")

print("\n  ** 若持有組合(等權: 友達/聯電/0050正2) 且用融資1.5倍 **")
port = []
for sid in ("2409", "2303", "00631L"):
    g = st[st.sid == sid].sort_values("date").set_index("date").adj_close
    w = g.loc["2026-06-22":"2026-07-31"]
    port.append(w / w.iloc[0])
P = pd.concat(port, axis=1).mean(axis=1)
print(f"     等權組合 高低跌幅 {P.min()-1:.1%}   × 融資1.5倍 = {(P.min()-1)*1.5:.1%}")

print("\n" + "=" * 84); print("【D】把 §4 停損規則套在個股上會怎樣 (跌破SMA20全出)"); print("=" * 84)
rows = []
for nm, g in st.groupby("name"):
    g = g.sort_values("date").set_index("date").copy()
    g["sma20"] = g.adj_close.rolling(20).mean()
    g["ema8"] = g.adj_close.ewm(span=8, adjust=False).mean()
    g["r"] = g.adj_close.pct_change()
    pos, cur = [], 0.0
    for i in range(len(g)):
        if np.isnan(g.sma20.iloc[i]): pos.append(0.0); continue
        cur = 0.0 if g.adj_close.iloc[i] < g.sma20.iloc[i] else (min(cur, .5) if g.adj_close.iloc[i] < g.ema8.iloc[i] else 1.0)
        pos.append(cur)
    p = pd.Series(pos, index=g.index)
    # 加上大盤濾網
    mk = mkt.reindex(g.index)
    filt = pd.Series(np.where((mk.idx_close > mk.sma60) & (mk.atrp < 2.5), 1.0, 0.0), index=g.index).fillna(0)
    for lbl, pp in [("純個股規則", p), ("+大盤濾網(>SMA60 & ATR%<2.5)", p * filt)]:
        rr = (pp.shift(1).fillna(0) * g.r).fillna(0); eq = (1 + rr).cumprod()
        jul = g.index >= "2026-06-22"
        rows.append({"標的": nm, "規則": lbl, "總報酬": f"{eq.iloc[-1]-1:+.0%}",
                     "最大回撤": f"{(eq/eq.cummax()-1).min():.0%}",
                     "6/22起": f"{(1+rr[jul]).cumprod().iloc[-1]-1:+.1%}", "平均持倉": f"{pp.mean():.0%}"})
    rows.append({"標的": nm, "規則": "  (買進持有對照)", "總報酬": f"{g.adj_close.iloc[-1]/g.adj_close.iloc[0]-1:+.0%}",
                 "最大回撤": f"{(g.adj_close/g.adj_close.cummax()-1).min():.0%}",
                 "6/22起": f"{g.adj_close.iloc[-1]/g.loc['2026-06-22':].adj_close.iloc[0]-1:+.1%}", "平均持倉": "100%"})
print(pd.DataFrame(rows).to_string(index=False))
