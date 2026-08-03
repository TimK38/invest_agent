"""「現股ETF + 融資個股」這種配置到底安不安全？該不該一起擇時？

回答兩件事：
  1. 這套策略是動能專屬，還是長線也適用？
  2. 現股 ETF 那塊，該不該跟著融資個股一起出場？
"""
import sys, pathlib
import numpy as np, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import TAIEX_ENRICHED, PRICES_ADJ
pd.set_option("display.width", 210)

idx = pd.read_csv(TAIEX_ENRICHED, parse_dates=["date"]).set_index("date")
st = pd.read_csv(PRICES_ADJ, parse_dates=["date"], dtype={"sid": str})
px = st.pivot_table(index="date", columns="sid", values="adj_close").sort_index()
px = px.loc["2023-07-03":]
yrs = (px.index[-1] - px.index[0]).days / 365.25
JUL = slice("2026-06-22", "2026-07-31")


def rule_pos(s):
    """個股層級：跌破EMA8出半、跌破SMA20全出、站回SMA20全進"""
    sma20 = s.rolling(20).mean()
    ema8 = s.ewm(span=8, adjust=False).mean()
    out, cur = [], 0.0
    for i in range(len(s)):
        if np.isnan(sma20.iloc[i]):
            out.append(0.0); continue
        cur = 0.0 if s.iloc[i] < sma20.iloc[i] else (min(cur, .5) if s.iloc[i] < ema8.iloc[i] else 1.0)
        out.append(cur)
    return pd.Series(out, index=s.index)


def stats(name, ret):
    eq = (1 + ret.fillna(0)).cumprod()
    dd = eq / eq.cummax() - 1
    j = ret.loc[JUL]
    eqj = (1 + j.fillna(0)).cumprod()
    ddj = (eqj / eqj.cummax() - 1).min()
    return {"配置": name, "3年年化": f"{eq.iloc[-1]**(1/yrs)-1:+.1%}", "3年總報酬": f"{eq.iloc[-1]-1:+.0%}",
            "最大回撤": f"{dd.min():.1%}", "7月這波": f"{eqj.iloc[-1]-1:+.1%}", "7月最深": f"{ddj:.1%}"}


ETF = px["0050"]                                   # 現股 ETF 代表
STK = px[["2409", "2303"]].pct_change().mean(axis=1)   # 融資個股：等權 友達(2409)+聯電
etf_r = ETF.pct_change()
LEV = 1.5

rows = []
# ── 只有現股 ETF ──
rows.append(stats("① 100% 現股 0050，買進持有", etf_r))
rows.append(stats("② 100% 現股 0050，套用出場規則", rule_pos(ETF).shift(1) * etf_r))

# ── 只有融資個股 ──
rows.append(stats("③ 100% 融資個股 1.5x，買進持有", STK * LEV))
p_stk = pd.concat([rule_pos(px["2409"]), rule_pos(px["2303"])], axis=1).mean(axis=1)
rows.append(stats("④ 100% 融資個股 1.5x，套用出場規則", p_stk.shift(1) * STK * LEV))

# ── 混合配置（你原本的想法）──
rows.append(stats("⑤ 半現股ETF + 半融資個股，兩邊都不動",
                  0.5 * etf_r + 0.5 * STK * LEV))
rows.append(stats("⑥ 半現股ETF不動 + 半融資個股套規則",
                  0.5 * etf_r + 0.5 * p_stk.shift(1) * STK * LEV))
rows.append(stats("⑦ 半現股ETF也套規則 + 半融資個股套規則",
                  0.5 * rule_pos(ETF).shift(1) * etf_r + 0.5 * p_stk.shift(1) * STK * LEV))

print("=" * 118)
print("【A】配置比較（融資個股 = 等權 友達(2409)+聯電 × 1.5 倍）")
print("=" * 118)
print(pd.DataFrame(rows).to_string(index=False))

print("\n" + "=" * 118)
print("【B】現股 ETF 到底該不該擇時？三種強度的濾網")
print("=" * 118)
sma120 = ETF.rolling(120).mean()
mk = idx.reindex(ETF.index)
variants = {
    "不擇時（買進持有）": pd.Series(1.0, index=ETF.index),
    "跌破自身 SMA20 全出": rule_pos(ETF),
    "跌破自身 SMA120 才出": (ETF > sma120).astype(float),
    "只在大盤 D 狀態出（破SMA120 或 距高點-12%）": ((mk.close > mk.sma120) & (mk.dd > -0.12)).astype(float),
}
rows = []
for k, p in variants.items():
    p = p.fillna(0)
    r = p.shift(1) * etf_r
    s = stats(k, r)
    s["平均持倉"] = f"{p.mean():.0%}"
    s["進出次數"] = int((p.diff().abs() > 0.01).sum())
    rows.append(s)
print(pd.DataFrame(rows).to_string(index=False))

print("\n" + "=" * 118)
print("【C】7 月這波：現股 ETF 與融資個股的實際差距")
print("=" * 118)
w = px.loc[JUL]
rows = []
for sid, nm in [("0050", "0050 (現股ETF)"), ("00981A", "00981A"), ("009816", "009816"),
                ("2409", "友達"), ("2303", "聯電"), ("00631L", "0050正2")]:
    if sid not in w or w[sid].dropna().empty:
        continue
    s = w[sid].dropna()
    rows.append({"標的": nm, "6/22": round(s.iloc[0], 2), "波段最低": round(s.min(), 2),
                 "最大跌幅": f"{s.min()/s.iloc[0]-1:.1%}",
                 "融資1.5x後": f"{(s.min()/s.iloc[0]-1)*1.5:.1%}"})
print(pd.DataFrame(rows).to_string(index=False))

print("\n" + "=" * 118)
print("【D】現股與融資個股的相關性 —— 「分散」是真的嗎？")
print("=" * 118)
r0050 = px["0050"].pct_change()
for lbl, m in [("全期", slice(None)), ("大盤下跌日", idx.reindex(px.index).ret < 0),
               ("大盤跌超過2%的日子", idx.reindex(px.index).ret < -0.02)]:
    a = r0050[m] if not isinstance(m, slice) else r0050
    b = STK[m] if not isinstance(m, slice) else STK
    j = pd.concat([a, b], axis=1).dropna()
    print(f"  {lbl:20s} 相關係數 {j.corr().iloc[0,1]:.3f}   (n={len(j)})")
print("""
  相關係數在大跌日不降反升 = 「一部分現股一部分融資」在崩盤時**不構成分散**。
  兩邊同時虧，只是虧的幅度不同。真正的分散是「有一部分是現金」。""")
