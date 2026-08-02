"""台股大盤三年價量統計 — 為策略訂出「有數字根據」的門檻"""
import numpy as np, pandas as pd
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import TAIEX_RAW, TAIEX_ENRICHED, STOCKS_ADJ, STOCKS_RAW

pd.set_option("display.width", 200)
df = pd.read_csv(TAIEX_RAW, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
c, h, l = df.close, df.high, df.low

# ---------- 指標 ----------
for n in (5, 8, 10, 20, 60, 120, 200):
    df[f"sma{n}"] = c.rolling(n).mean()
    df[f"ema{n}"] = c.ewm(span=n, adjust=False).mean()
tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
df["atr20"] = tr.rolling(20).mean()
df["atrp"] = df.atr20 / c * 100
df["ret"] = c.pct_change()
df["ath"] = c.cummax()
df["dd"] = c / df.ath - 1
df["tovr20"] = df.turnover.rolling(20).mean()
df["vratio"] = df.turnover / df.tovr20

print("=" * 78)
print("【0】基本盤面")
print("=" * 78)
yrs = (df.date.iloc[-1] - df.date.iloc[0]).days / 365.25
print(f"期間 {df.date.iloc[0]:%Y-%m-%d} ~ {df.date.iloc[-1]:%Y-%m-%d}  ({yrs:.2f} 年, {len(df)} 交易日)")
print(f"指數 {c.iloc[0]:,.0f} -> {c.iloc[-1]:,.0f}   買進持有報酬 {c.iloc[-1]/c.iloc[0]-1:+.1%}  年化 {(c.iloc[-1]/c.iloc[0])**(1/yrs)-1:+.1%}")
print(f"年化波動 {df.ret.std()*np.sqrt(252):.1%}   最大回撤 {df.dd.min():.1%}")
print(f"單日漲跌 |>3%| 共 {(df.ret.abs()>0.03).sum()} 天   |>5%| 共 {(df.ret.abs()>0.05).sum()} 天")

print("\n" + "=" * 78)
print("【1】從歷史高點的回撤有多深？(你最痛的問題：什麼叫『真的轉勢』)")
print("=" * 78)
for t in (0.03, 0.05, 0.08, 0.10, 0.15, 0.20):
    d = df[df.dd <= -t]
    print(f"  回撤 >= {t:5.0%} 的天數 {len(d):4d} ({len(d)/len(df):5.1%})")

# 每一段獨立回撤事件
ev, in_dd, pk, tr_i = [], False, 0, 0
for i in range(len(df)):
    if df.dd.iloc[i] < -0.001 and not in_dd:
        in_dd, pk, tr_i = True, i, i
    elif in_dd:
        if df.dd.iloc[i] < df.dd.iloc[tr_i]:
            tr_i = i
        if df.dd.iloc[i] >= -0.001:
            ev.append({"start": df.date.iloc[pk], "trough": df.date.iloc[tr_i], "end": df.date.iloc[i],
                       "depth": df.dd.iloc[tr_i], "days_down": tr_i - pk, "days_rec": i - tr_i})
            in_dd = False
if in_dd:
    ev.append({"start": df.date.iloc[pk], "trough": df.date.iloc[tr_i], "end": pd.NaT,
               "depth": df.dd.iloc[tr_i], "days_down": tr_i - pk, "days_rec": np.nan})
E = pd.DataFrame(ev)
print(f"\n  三年內共 {len(E)} 段回撤，其中 >5% 的有 {len(E[E.depth<=-0.05])} 段：")
print(E[E.depth <= -0.05].assign(depth=lambda x: (x.depth*100).round(1)).to_string(index=False))

print("\n" + "=" * 78)
print("【2】均線當多空分界：哪一條假訊號最少？(收盤價 vs MA，做多/空手)")
print("=" * 78)
res = []
for kind in ("sma", "ema"):
    for n in (10, 20, 60, 120, 200):
        col = f"{kind}{n}"
        sig = (c > df[col]).astype(int).shift(1).fillna(0)
        r = (sig * df.ret).fillna(0)
        eq = (1 + r).cumprod()
        trades = int((sig.diff().abs() > 0).sum())
        # 每次進場後的結果
        ent = sig.diff() == 1
        segs, pos, st = [], False, 0
        for i in range(len(sig)):
            if sig.iloc[i] == 1 and not pos:
                pos, st = True, i
            elif sig.iloc[i] == 0 and pos:
                segs.append(c.iloc[i] / c.iloc[st] - 1); pos = False
        if pos: segs.append(c.iloc[-1] / c.iloc[st] - 1)
        segs = np.array(segs)
        res.append({"MA": col.upper(), "年化": (eq.iloc[-1])**(1/yrs)-1, "總報酬": eq.iloc[-1]-1,
                    "最大回撤": (eq/eq.cummax()-1).min(), "進出次數": len(segs),
                    "勝率": (segs > 0).mean() if len(segs) else np.nan,
                    "假訊號(<1%虧損出場)": int(((segs > -0.01) & (segs < 0.01)).sum()),
                    "在場比例": sig.mean()})
R = pd.DataFrame(res)
R["年化"] = (R["年化"]*100).round(1); R["總報酬"] = (R["總報酬"]*100).round(1)
R["最大回撤"] = (R["最大回撤"]*100).round(1); R["勝率"] = (R["勝率"]*100).round(0)
R["在場比例"] = (R["在場比例"]*100).round(0)
print(R.to_string(index=False))
print(f"\n  [對照] 買進持有  年化 {((c.iloc[-1]/c.iloc[0])**(1/yrs)-1)*100:.1f}%  總報酬 {(c.iloc[-1]/c.iloc[0]-1)*100:.1f}%  最大回撤 {df.dd.min()*100:.1f}%")

print("\n" + "=" * 78)
print("【3】跌破均線之後會怎樣？(決定『跌破就跑』值不值得)")
print("=" * 78)
print("  情境 = 當天收盤首次跌破該均線，看之後 N 日大盤報酬")
for col in ("ema8", "sma20", "sma60", "sma120"):
    brk = (c < df[col]) & (c.shift() >= df[col].shift())
    row = {"訊號次數": int(brk.sum())}
    for n in (5, 10, 20, 60):
        fwd = c.shift(-n) / c - 1
        row[f"+{n}日均"] = f"{fwd[brk].mean()*100:+.2f}%"
        row[f"+{n}日負報酬率"] = f"{(fwd[brk]<0).mean()*100:.0f}%"
    print(f"  {col.upper():7s} " + "  ".join(f"{k}={v}" for k, v in row.items()))

print("\n" + "=" * 78)
print("【4】市場狀態分層：SMA60 / SMA120 的過濾力")
print("=" * 78)
st = pd.Series("其他", index=df.index)
st[(c > df.sma60) & (df.sma60 > df.sma120)] = "A 多頭排列(攻擊)"
st[(c > df.sma60) & (df.sma60 <= df.sma120)] = "B 反彈未確認"
st[(c <= df.sma60) & (c > df.sma120)] = "C 中期整理"
st[(c <= df.sma120)] = "D 空頭(危險)"
fwd20 = c.shift(-20) / c - 1
out = []
for k in ["A 多頭排列(攻擊)", "B 反彈未確認", "C 中期整理", "D 空頭(危險)"]:
    m = st == k
    out.append({"狀態": k, "天數佔比": f"{m.mean()*100:.0f}%", "隔日均報酬": f"{df.ret[m].mean()*100:+.3f}%",
                "未來20日均報酬": f"{fwd20[m].mean()*100:+.2f}%", "未來20日勝率": f"{(fwd20[m]>0).mean()*100:.0f}%",
                "當日波動(ATR%)": f"{df.atrp[m].mean():.2f}%"})
print(pd.DataFrame(out).to_string(index=False))

print("\n" + "=" * 78)
print("【5】你現在的策略 vs 加上風控 (以大盤模擬，含融資槓桿情境)")
print("=" * 78)

def run(name, sig, lev=1.0):
    """sig: 每日持倉比例(0~1)，lev: 槓桿"""
    r = (sig.shift(1).fillna(0) * lev * df.ret).fillna(0)
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1)
    # 七月崩盤區間
    jul = df.date >= "2026-07-01"
    eq_j = (1 + r[jul]).cumprod()
    return {"策略": name, "年化": f"{eq.iloc[-1]**(1/yrs)-1:+.1%}", "總報酬": f"{eq.iloc[-1]-1:+.1%}",
            "最大回撤": f"{dd.min():.1%}", "2026年7月": f"{eq_j.iloc[-1]-1:+.1%}",
            "平均持倉": f"{sig.mean():.0%}"}

full = pd.Series(1.0, index=df.index)
# 你的規則: 跌破EMA8 出一半, 跌破SMA20 全出, 站回SMA20 全進
pos, cur = [], 0.0
for i in range(len(df)):
    if np.isnan(df.sma20.iloc[i]):
        pos.append(0.0); continue
    px, e8, s20 = c.iloc[i], df.ema8.iloc[i], df.sma20.iloc[i]
    if px < s20: cur = 0.0
    elif px < e8: cur = min(cur, 0.5)
    else: cur = 1.0
    pos.append(cur)
yours = pd.Series(pos, index=df.index)

# 加上大盤濾網: 只有 收盤>SMA120 才允許滿倉，否則上限 50%；跌破SMA120 全出
filt = pd.Series(np.where(c > df.sma120, 1.0, 0.0), index=df.index)
yours_f = yours * filt

# 再加上「回撤煞車」: 距ATH回撤>8% 一律空手，直到重新站上SMA60
brake = pd.Series(1.0, index=df.index)
off = False
for i in range(len(df)):
    if df.dd.iloc[i] <= -0.08: off = True
    if off and c.iloc[i] > df.sma60.iloc[i] and df.dd.iloc[i] > -0.05: off = False
    brake.iloc[i] = 0.0 if off else 1.0
yours_fb = yours_f * brake

rows = [run("買進持有", full),
        run("買進持有 + 融資1.6倍", full, 1.6),
        run("你的規則 (EMA8半出/SMA20全出)", yours),
        run("你的規則 + 融資1.6倍", yours, 1.6),
        run("你的規則 + SMA120大盤濾網", yours_f),
        run("你的規則 + SMA120濾網 + 8%回撤煞車", yours_fb),
        run("上者 + 融資1.6倍", yours_fb, 1.6)]
print(pd.DataFrame(rows).to_string(index=False))

print("\n" + "=" * 78)
print("【6】量能：什麼樣的量是危險訊號")
print("=" * 78)
df["tovr_bil"] = df.turnover / 1e9
print(f"  近3年日均成交金額 {df.tovr_bil.mean():,.0f} 億  近60日 {df.tovr_bil.tail(60).mean():,.0f} 億")
q = df.vratio.quantile([.1, .25, .5, .75, .9, .95])
print(f"  量能比(當日/20日均) 分位數: " + "  ".join(f"{int(k*100)}%={v:.2f}" for k, v in q.items()))
print("\n  『爆量』後的表現 (量能比>1.5)：")
for cond, nm in [((df.vratio > 1.5) & (df.ret > 0.01), "爆量長紅"), ((df.vratio > 1.5) & (df.ret < -0.01), "爆量長黑")]:
    for n in (5, 20):
        f = c.shift(-n) / c - 1
        print(f"    {nm} (n={cond.sum():3d})  未來{n:2d}日 均報酬 {f[cond].mean()*100:+.2f}%  勝率 {(f[cond]>0).mean()*100:.0f}%")

print("\n  高檔量能萎縮 (創新高後20日均量較前期縮 >15%)：")
vol_shrink = (df.tovr20 / df.tovr20.shift(20) - 1) < -0.15
near_ath = df.dd > -0.03
cond = vol_shrink & near_ath
for n in (10, 20, 60):
    f = c.shift(-n) / c - 1
    print(f"    n={cond.sum():3d}  未來{n:2d}日 均報酬 {f[cond].mean()*100:+.2f}%  勝率 {(f[cond]>0).mean()*100:.0f}%")

print("\n" + "=" * 78)
print("【7】2026年7月這波：逐日還原")
print("=" * 78)
j = df[df.date >= "2026-07-10"].copy()
j["距高點"] = (j.dd * 100).round(1)
j["vs EMA8"] = np.where(j.close > j.ema8, "上", "破")
j["vs SMA20"] = np.where(j.close > j.sma20, "上", "破")
j["vs SMA60"] = np.where(j.close > j.sma60, "上", "破")
j["vs SMA120"] = np.where(j.close > j.sma120, "上", "破")
j["量比"] = j.vratio.round(2)
j["漲跌%"] = (j.ret * 100).round(2)
print(j[["date", "close", "漲跌%", "距高點", "vs EMA8", "vs SMA20", "vs SMA60", "vs SMA120", "量比"]].to_string(index=False))

print("\n" + "=" * 78)
print("【8】現在的位置")
print("=" * 78)
last = df.iloc[-1]
print(f"  日期 {last.date:%Y-%m-%d}  收盤 {last.close:,.0f}")
for n in (8, 20, 60, 120, 200):
    k = f"ema8" if n == 8 else f"sma{n}"
    v = last[k]
    print(f"    {k.upper():6s} {v:9,.0f}   {'▲在上' if last.close > v else '▼跌破'}  乖離 {last.close/v-1:+.1%}")
print(f"  距歷史高點 {last.dd:+.1%}   ATR20 {last.atrp:.2f}%（3年均 {df.atrp.mean():.2f}%）")
print(f"  20日均量 {last.tovr20/1e9:,.0f} 億   當日量比 {last.vratio:.2f}")
df.to_csv(TAIEX_ENRICHED, index=False)
