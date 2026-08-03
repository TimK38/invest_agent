"""第二輪：槓桿還原 + 攤平代價 + 鎖利/再進場門檻的推導

【A】用 --loss 帶入一段實際虧損%，反推當時的有效槓桿。不帶則跳過。
"""
import numpy as np, pandas as pd
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import TAIEX_RAW, TAIEX_ENRICHED, PRICES_ADJ, STOCKS_RAW
pd.set_option("display.width", 220)

df = pd.read_csv(TAIEX_ENRICHED, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
c = df.close; yrs = (df.date.iloc[-1] - df.date.iloc[0]).days / 365.25

print("=" * 80); print("【A】把一段實際虧損還原成有效槓桿倍數"); print("=" * 80)
LOSS = float(__import__("os").environ.get("LOSS_PCT", "0")) / 100   # 例: LOSS_PCT=44
peak = df[df.date >= "2026-06-01"].close.max()
pk_d = df.loc[df[df.date >= "2026-06-01"].close.idxmax(), "date"]
trough = df[df.date >= "2026-07-01"].close.min()
tr_d = df.loc[df[df.date >= "2026-07-01"].close.idxmin(), "date"]
idx_dd = trough / peak - 1
print(f"  大盤 高點 {peak:,.0f} ({pk_d:%m/%d}) -> 低點 {trough:,.0f} ({tr_d:%m/%d}) = {idx_dd:+.1%}")
if LOSS:
    print(f"  帶入的實際虧損 = -{LOSS:.1%}")
    print(f"  推算有效槓桿 ≈ {LOSS/abs(idx_dd):.2f} 倍")
else:
    print("  (未帶入實際虧損；設環境變數 LOSS_PCT=44 可反推有效槓桿)")
print(f"""
  曝險怎麼疊上去的：融資(常用1.5~2倍) × 槓桿ETF本身2倍
                    × 高Beta個股波動 1.5~2倍於大盤 × 下跌途中加碼放大部位
  => 名目上「只是融資買股票」，實際曝險可達大盤的 2.5~3 倍。
     大盤 -25% 時，2.7 倍曝險就接近斷頭。""")

print("\n" + "=" * 80); print("【B】『以為是底部所以加碼』的代價 — 攤平在台股值不值得"); print("=" * 80)
print("  情境：大盤已跌破SMA20，之後每再跌 X% 就加碼一次，看 +20/+60 日結果")
below20 = c < df.sma20
for st in ("跌破SMA20當下", "跌破SMA20且跌破SMA60", "距高點-8%以上且破SMA20"):
    if st == "跌破SMA20當下": m = below20
    elif st == "跌破SMA20且跌破SMA60": m = below20 & (c < df.sma60)
    else: m = below20 & (df.dd <= -0.08)
    row = [f"  {st:24s} n={int(m.sum()):3d}"]
    for n in (5, 20, 60):
        f = c.shift(-n) / c - 1
        row.append(f"+{n}日 {f[m].mean()*100:+.2f}% (勝率{(f[m]>0).mean()*100:.0f}%)")
        # 最壞情況：加碼後還要再忍受多少
    mdd = pd.Series([ (c.iloc[i:i+20].min()/c.iloc[i]-1) for i in range(len(c)) ], index=c.index)
    row.append(f"| 加碼後20日內最深再跌 中位數 {mdd[m].median()*100:.1f}% 最糟 {mdd[m].min()*100:.1f}%")
    print("  ".join(row))
print("""
  結論：破SMA20後加碼，期望值是正的(因為這三年是大多頭)，但你必須忍受
        中位數再跌 4~6%、最糟再跌 20%+ 的過程。**有融資的人撐不到那裡。**
        無槓桿可以攤平；有槓桿攤平 = 把「會回來的帳面損失」變成「回不來的斷頭」。""")

print("\n" + "=" * 80); print("【C】鎖利機制比較 — 賺到的怎麼留住"); print("=" * 80)
def bt(name, pos):
    r = (pos.shift(1).fillna(0) * df.ret).fillna(0); eq = (1 + r).cumprod()
    dd = eq / eq.cummax() - 1
    jul = df.date >= "2026-06-23"
    return {"機制": name, "年化": f"{eq.iloc[-1]**(1/yrs)-1:+.1%}", "最大回撤": f"{dd.min():.1%}",
            "6/23起這波": f"{(1+r[jul]).cumprod().iloc[-1]-1:+.1%}",
            "平均持倉": f"{pos.mean():.0%}", "換手(次)": int((pos.diff().abs() > 0.01).sum())}

rows = [bt("A. 全程持有", pd.Series(1.0, index=df.index))]
# 你的原規則
p, cur = [], 0.0
for i in range(len(df)):
    if np.isnan(df.sma20.iloc[i]): p.append(0.0); continue
    cur = 0.0 if c.iloc[i] < df.sma20.iloc[i] else (min(cur, .5) if c.iloc[i] < df.ema8.iloc[i] else 1.0)
    p.append(cur)
rows.append(bt("B. 你的規則 EMA8半出/SMA20全出", pd.Series(p, index=df.index)))

# ATR 移動停利
for k in (2.0, 3.0, 4.0):
    p, cur, hi = [], 0.0, np.nan
    for i in range(len(df)):
        if np.isnan(df.atr20.iloc[i]) or np.isnan(df.sma60.iloc[i]): p.append(0.0); continue
        if cur == 0 and c.iloc[i] > df.sma60.iloc[i] and c.iloc[i] > df.sma20.iloc[i]:
            cur, hi = 1.0, c.iloc[i]
        elif cur > 0:
            hi = max(hi, c.iloc[i])
            if c.iloc[i] < hi - k * df.atr20.iloc[i]: cur = 0.0
        p.append(cur)
    rows.append(bt(f"C. ATR移動停利 {k:.0f}xATR (進場:站上SMA20&60)", pd.Series(p, index=df.index)))

# 分段鎖利：破EMA8出1/3、破SMA20再出1/3、破SMA60全出
p, cur = [], 0.0
for i in range(len(df)):
    if np.isnan(df.sma60.iloc[i]): p.append(0.0); continue
    px = c.iloc[i]
    if px < df.sma60.iloc[i]: cur = 0.0
    elif px < df.sma20.iloc[i]: cur = min(cur, 1/3)
    elif px < df.ema8.iloc[i]: cur = min(cur, 2/3)
    else: cur = 1.0
    p.append(cur)
rows.append(bt("D. 三段式 EMA8→2/3, SMA20→1/3, SMA60→0", pd.Series(p, index=df.index)))

# D + 波動率降槓桿
atr_scale = np.clip(1.8 / df.atrp, 0.4, 1.0).fillna(0)
rows.append(bt("E. D + 波動率調節部位(ATR越高倉越輕)", pd.Series(p, index=df.index) * atr_scale))
print(pd.DataFrame(rows).to_string(index=False))

print("\n" + "=" * 80); print("【D】再上車的條件 — 哪種『站回』最可靠"); print("=" * 80)
print("  訊號當日買進，看未來報酬與『買進後最深套牢』")
sigs = {
    "收盤站回SMA20": (c > df.sma20) & (c.shift() <= df.sma20.shift()),
    "站回SMA20 + SMA20翻揚": (c > df.sma20) & (c.shift() <= df.sma20.shift()) & (df.sma20 > df.sma20.shift(3)),
    "連2日站上SMA20": (c > df.sma20) & (c.shift() > df.sma20.shift()) & (c.shift(2) <= df.sma20.shift(2)),
    "站回SMA20 且 在SMA60之上": (c > df.sma20) & (c.shift() <= df.sma20.shift()) & (c > df.sma60),
    "站回SMA60": (c > df.sma60) & (c.shift() <= df.sma60.shift()),
    "站回SMA20 且 ATR%<2.5": (c > df.sma20) & (c.shift() <= df.sma20.shift()) & (df.atrp < 2.5),
}
out = []
for k, m in sigs.items():
    f20 = c.shift(-20) / c - 1; f60 = c.shift(-60) / c - 1
    mdd = pd.Series([(c.iloc[i:i+20].min() / c.iloc[i] - 1) for i in range(len(c))], index=c.index)
    out.append({"進場訊號": k, "次數": int(m.sum()), "+20日": f"{f20[m].mean()*100:+.2f}%",
                "20日勝率": f"{(f20[m]>0).mean()*100:.0f}%", "+60日": f"{f60[m].mean()*100:+.2f}%",
                "60日勝率": f"{(f60[m]>0).mean()*100:.0f}%",
                "買後最深套牢(中位)": f"{mdd[m].median()*100:.1f}%", "(最糟)": f"{mdd[m].min()*100:.1f}%"})
print(pd.DataFrame(out).to_string(index=False))

print("\n" + "=" * 80); print("【E】波動率是最好的『降槓桿』訊號"); print("=" * 80)
df["atr_q"] = pd.qcut(df.atrp, 4, labels=["Q1最低波動", "Q2", "Q3", "Q4最高波動"])
f20 = c.shift(-20) / c - 1
mdd20 = pd.Series([(c.iloc[i:i+20].min() / c.iloc[i] - 1) for i in range(len(c))], index=c.index)
g = df.groupby("atr_q", observed=True).apply(lambda x: pd.Series({
    "ATR%範圍": f"{x.atrp.min():.2f}~{x.atrp.max():.2f}",
    "隔日均報酬": f"{x.ret.mean()*100:+.3f}%",
    "未來20日均報酬": f"{f20[x.index].mean()*100:+.2f}%",
    "未來20日勝率": f"{(f20[x.index]>0).mean()*100:.0f}%",
    "未來20日最深跌(中位)": f"{mdd20[x.index].median()*100:.1f}%",
}), include_groups=False)
print(g.to_string())
print(f"\n  目前 ATR% = {df.atrp.iloc[-1]:.2f}%  -> 位於 {'Q4 最高波動' if df.atrp.iloc[-1] > df.atrp.quantile(.75) else '較低區'}")

print("\n" + "=" * 80); print("【F】量能 (單位修正: 億元)"); print("=" * 80)
tb = df.turnover / 1e8
print(f"  近3年日均 {tb.mean():,.0f} 億   近60日均 {tb.tail(60).mean():,.0f} 億   最近一日 {tb.iloc[-1]:,.0f} 億")
for th in (1.2, 1.3):
    for cond, nm in [((df.vratio > th) & (df.ret > 0.01), f"量比>{th}長紅"), ((df.vratio > th) & (df.ret < -0.02), f"量比>{th}長黑")]:
        f = c.shift(-20) / c - 1
        if cond.sum() >= 3:
            print(f"    {nm:14s} n={cond.sum():3d}  未來20日 {f[cond].mean()*100:+.2f}%  勝率 {(f[cond]>0).mean()*100:.0f}%")

print("\n" + "=" * 80); print("【G】6/23 高點以來逐日 — 你有幾次出場機會"); print("=" * 80)
j = df[df.date >= "2026-06-19"].copy()
j["訊號"] = ""
j.loc[(c < df.ema8) & (c.shift() >= df.ema8.shift()), "訊號"] += "破EMA8 "
j.loc[(c < df.sma20) & (c.shift() >= df.sma20.shift()), "訊號"] += "破SMA20 "
j.loc[(c < df.sma60) & (c.shift() >= df.sma60.shift()), "訊號"] += "破SMA60 "
j.loc[j.atrp > 2.5, "訊號"] += "高波動 "
print(j[["date", "close", "ret", "dd", "atrp", "訊號"]].assign(
    ret=lambda x: (x.ret*100).round(2), dd=lambda x: (x.dd*100).round(1), atrp=lambda x: x.atrp.round(2)
).rename(columns={"ret": "漲跌%", "dd": "距高點%", "atrp": "ATR%"}).to_string(index=False))
