"""第四輪：槓桿ETF耗損的正確算法 + 風險係數表 + ATR濾網的單獨貢獻"""
import numpy as np, pandas as pd
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import TAIEX_RAW, TAIEX_ENRICHED, STOCKS_ADJ, STOCKS_RAW
pd.set_option("display.width", 200)

idx = pd.read_csv(TAIEX_ENRICHED, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
st = pd.read_csv(STOCKS_ADJ, parse_dates=["date"])
mkt = idx.set_index("date")

print("=" * 80); print("【A】0050正2 耗損 — 用『合成2倍』對照(正確算法)"); print("=" * 80)
a = st[st.sid == "0050"].sort_values("date").set_index("date").adj_close
b = st[st.sid == "00631L"].sort_values("date").set_index("date").adj_close
j = pd.concat([a.rename("x1"), b.rename("x2")], axis=1, sort=True).dropna()
r1, r2 = j.x1.pct_change().fillna(0), j.x2.pct_change().fillna(0)
syn = (1 + 2 * r1).cumprod()          # 每日重新平衡的完美2倍(無費用無誤差)
act = (1 + r2).cumprod()
print(f"  0050 三年 {j.x1.iloc[-1]/j.x1.iloc[0]-1:+.1%}")
print(f"  完美2倍(每日重平衡, 無成本)  {syn.iloc[-1]-1:+.1%}   <- 這才是正確的對照基準")
print(f"  0050正2 實際                {act.iloc[-1]-1:+.1%}")
print(f"  => 三年累積落後 {act.iloc[-1]/syn.iloc[-1]-1:+.1%}  (年化約 {(act.iloc[-1]/syn.iloc[-1])**(1/3.08)-1:+.2%} 的費用+融資成本+追蹤誤差)")
print(f"\n  註：『2倍總報酬』不等於『總報酬的2倍』。上一輪拿 213.7%x2 當基準是錯的，此處修正。")

print("\n  分市場狀態看 0050正2 相對表現：")
mk = mkt.reindex(j.index)
regimes = {
    "趨勢向上 (收盤>SMA60 且 SMA20>SMA60)": (mk.close > mk.sma60) & (mk.sma20 > mk.sma60),
    "盤整 (收盤在SMA20 ±2% 內)": ((mk.close - mk.sma20).abs() / mk.close < 0.02),
    "下跌 (收盤<SMA60)": mk.close < mk.sma60,
    "高波動 (ATR%>2.5)": mk.atrp > 2.5,
}
rows = []
for k, m in regimes.items():
    m = m.fillna(False)
    c1, c2 = (1 + r1[m]).prod() - 1, (1 + r2[m]).prod() - 1
    cs = (1 + 2 * r1[m]).prod() - 1
    rows.append({"市場狀態": k, "天數": int(m.sum()), "0050累積": f"{c1:+.1%}",
                 "完美2倍": f"{cs:+.1%}", "0050正2實際": f"{c2:+.1%}",
                 "正2 vs 0050": f"{c2-c1:+.1f}pp", "落後完美2倍": f"{c2-cs:+.1f}pp"})
print(pd.DataFrame(rows).to_string(index=False))

print("\n" + "=" * 80); print("【B】風險係數表 — 直接用來算有效槓桿"); print("=" * 80)
mvol = idx.ret.std() * np.sqrt(252)
rows = []
for nm, g in st.groupby("name"):
    g = g.sort_values("date").set_index("date")
    r = g.adj_close.pct_change()
    jj = pd.concat([r.rename("r"), mkt.ret.rename("m")], axis=1, sort=True).dropna()
    dn = jj[jj.m < 0]
    beta_dn = np.cov(dn.r, dn.m)[0, 1] / np.var(dn.m)
    vol = r.std() * np.sqrt(252)
    volr = vol / mvol
    dd = (g.adj_close / g.adj_close.cummax() - 1).min()
    ddr = dd / idx.dd.min()
    rows.append({"標的": nm, "Beta(下跌日)": round(beta_dn, 2), "波動率比": round(volr, 2),
                 "回撤比": round(ddr, 2), "風險係數(取大者)": round(max(beta_dn, volr), 2),
                 "最大回撤": f"{dd:.0%}"})
R = pd.DataFrame(rows).sort_values("風險係數(取大者)", ascending=False)
print(R.to_string(index=False))
print(f"\n  (大盤: 年化波動 {mvol:.0%}, 最大回撤 {idx.dd.min():.0%})")
print("""
  關鍵：群創(Beta 1.00)、聯電(0.92)、聯發科(1.24) 的 Beta 都不高，
        但波動率是大盤的 1.7~1.9 倍、最大回撤是大盤的 1.5~1.7 倍。
        **只看 Beta 會嚴重低估風險** —— 因為個股風險大半是「非系統性」的，
        Beta 抓不到。所以風險係數改用 max(下跌Beta, 波動率比)。""")
print("\n  範例：等權 群創/聯電/0050正2 + 融資1.5倍 的有效槓桿：")
w = R.set_index("標的")["風險係數(取大者)"]
avg = np.mean([w["群創"], w["聯電"], w["0050正2"]])
print(f"    平均風險係數 {avg:.2f} x 融資 1.5 = 有效槓桿 {avg*1.5:.2f} 倍")
print(f"    大盤 -16.4% x {avg*1.5:.2f} = {-0.164*avg*1.5:.1%}")

print("\n" + "=" * 80); print("【C】拆解濾網：哪一條真正救了你"); print("=" * 80)
print("  對每個標的套用『跌破SMA20全出』，再逐一疊加大盤濾網，看 6/22 起的損失")
res = []
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
    mk = mkt.reindex(g.index)
    variants = {
        "① 只有個股規則": p,
        "② + 大盤>SMA60": p * (mk.close > mk.sma60).astype(float),
        "③ + ATR%<2.5": p * (mk.atrp < 2.5).astype(float),
        "④ + 距高點>-8%": p * (mk.dd > -0.08).astype(float),
        "⑤ 全部(②③④)": p * ((mk.close > mk.sma60) & (mk.atrp < 2.5) & (mk.dd > -0.08)).astype(float),
    }
    row = {"標的": nm}
    for k, pp in variants.items():
        pp = pp.fillna(0)
        rr = (pp.shift(1).fillna(0) * g.r).fillna(0)
        jul = g.index >= "2026-06-22"
        row[k] = f"{(1+rr[jul]).cumprod().iloc[-1]-1:+.1%}"
    row["買進持有"] = f"{g.adj_close.iloc[-1]/g.loc['2026-06-22':].adj_close.iloc[0]-1:+.1%}"
    res.append(row)
print(pd.DataFrame(res).to_string(index=False))
print("""
  ATR%<2.5 這一條(③)在 6/22 當天就已成立(當時 ATR% 2.64%)，
  等於在大盤創新高的那一天就把部位歸零 —— 這是唯一「提前」而非「事後」的訊號。""")

print("\n" + "=" * 80); print("【D】三年全期：各濾網的代價與效益"); print("=" * 80)
res = []
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
    mk = mkt.reindex(g.index)
    for k, pp in [("個股規則", p), ("+ATR%<2.5", p * (mk.atrp < 2.5).astype(float)),
                  ("+全部濾網", p * ((mk.close > mk.sma60) & (mk.atrp < 2.5) & (mk.dd > -0.08)).astype(float))]:
        pp = pp.fillna(0)
        rr = (pp.shift(1).fillna(0) * g.r).fillna(0); eq = (1 + rr).cumprod()
        res.append({"標的": nm, "版本": k, "三年報酬": f"{eq.iloc[-1]-1:+.0%}",
                    "最大回撤": f"{(eq/eq.cummax()-1).min():.0%}", "持倉比": f"{pp.mean():.0%}"})
    res.append({"標的": nm, "版本": "買進持有", "三年報酬": f"{g.adj_close.iloc[-1]/g.adj_close.iloc[0]-1:+.0%}",
                "最大回撤": f"{(g.adj_close/g.adj_close.cummax()-1).min():.0%}", "持倉比": "100%"})
print(pd.DataFrame(res).pivot(index="標的", columns="版本", values=["三年報酬", "最大回撤"]).to_string())
