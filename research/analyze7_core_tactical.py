"""核心＋戰術 vs 全部戰術：核心該佔多少？以及與狀態槓桿上限的衝突

核心 = 現股寬基ETF(0050)，不擇時
戰術 = 融資個股(友達+聯電 等權 × 1.5)，套用 EMA8/SMA20 出場規則
"""
import sys, pathlib
import numpy as np, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import TAIEX_ENRICHED, STOCKS_ADJ
pd.set_option("display.width", 200)

idx = pd.read_csv(TAIEX_ENRICHED, parse_dates=["date"]).set_index("date")
st = pd.read_csv(STOCKS_ADJ, parse_dates=["date"], dtype={"sid": str})
px = st.pivot_table(index="date", columns="sid", values="adj_close").sort_index().loc["2023-07-03":]
yrs = (px.index[-1] - px.index[0]).days / 365.25
JUL = slice("2026-06-22", "2026-07-31")


def rule_pos(s):
    sma20, ema8 = s.rolling(20).mean(), s.ewm(span=8, adjust=False).mean()
    out, cur = [], 0.0
    for i in range(len(s)):
        if np.isnan(sma20.iloc[i]):
            out.append(0.0); continue
        cur = 0.0 if s.iloc[i] < sma20.iloc[i] else (min(cur, .5) if s.iloc[i] < ema8.iloc[i] else 1.0)
        out.append(cur)
    return pd.Series(out, index=s.index)


def stats(name, ret, extra=None):
    eq = (1 + ret.fillna(0)).cumprod()
    dd = eq / eq.cummax() - 1
    j = ret.loc[JUL].fillna(0)
    eqj = (1 + j).cumprod()
    d = {"配置": name, "3年年化": f"{eq.iloc[-1]**(1/yrs)-1:+.1%}",
         "最大回撤": f"{dd.min():.1%}", "7月這波": f"{eqj.iloc[-1]-1:+.1%}",
         "7月最深": f"{(eqj/eqj.cummax()-1).min():.1%}",
         "報酬/回撤": f"{(eq.iloc[-1]**(1/yrs)-1)/abs(dd.min()):.2f}"}
    if extra:
        d.update(extra)
    return d


core_r = px["0050"].pct_change()
tac_raw = px[["2409", "2303"]].pct_change().mean(axis=1)
p_tac = pd.concat([rule_pos(px["2409"]), rule_pos(px["2303"])], axis=1).mean(axis=1).shift(1)
LEV = 1.5

print("=" * 108)
print("【A】核心佔比掃描（核心=現股0050不擇時；戰術=融資個股1.5x套規則）")
print("=" * 108)
rows = []
for w in (0.0, 0.25, 0.4, 0.5, 0.6, 0.75, 1.0):
    r = w * core_r + (1 - w) * p_tac * tac_raw * LEV
    rows.append(stats(f"核心 {w:.0%} / 戰術 {1-w:.0%}", r))
print(pd.DataFrame(rows).to_string(index=False))

print("\n" + "=" * 108)
print("【B】對照：同樣的資金配置，但核心也套用出場規則（＝全部戰術）")
print("=" * 108)
p_core = rule_pos(px["0050"]).shift(1)
rows = []
for w in (0.25, 0.5, 0.75):
    r6 = w * core_r + (1 - w) * p_tac * tac_raw * LEV
    r7 = w * p_core * core_r + (1 - w) * p_tac * tac_raw * LEV
    rows.append(stats(f"核心{w:.0%}｜核心不擇時", r6))
    rows.append(stats(f"核心{w:.0%}｜核心也擇時", r7))
print(pd.DataFrame(rows).to_string(index=False))

print("\n" + "=" * 108)
print("【C】關鍵衝突：核心部位若受『狀態槓桿上限』約束會怎樣")
print("=" * 108)
mk = idx.reindex(px.index)
state = pd.Series("B", index=px.index)
state[(mk.close < mk.sma120) | (mk.dd <= -0.12)] = "D"
state[((mk.close < mk.sma60) | (mk.atrp > 2.5) | (mk.dd <= -0.08)) & (state != "D")] = "C"
cap = state.map({"A": 1.5, "B": 1.0, "C": 0.5, "D": 0.0})
coef = np.clip(1.8 / mk.atrp, 0.4, 1.0)
target = (cap * coef).shift(1)

W = 0.5
base = W * core_r + (1 - W) * p_tac * tac_raw * LEV
# 情境一：核心也被狀態上限壓縮（RC=1.09，目標曝險 target 倍）
core_allowed = np.minimum(W, target / 1.09).clip(lower=0)
r_capped = core_allowed * core_r + (1 - W) * p_tac * tac_raw * LEV
# 情境二：核心豁免於狀態上限，只受「核心不超過淨資產 50%」約束
r_exempt = base
rows = [stats("核心受狀態上限約束（C狀態被迫砍到近乎空手）", r_capped,
              {"核心平均持倉": f"{(core_allowed/W).mean():.0%}"}),
        stats("核心豁免於狀態上限（只受絕對比例上限）", r_exempt,
              {"核心平均持倉": "100%"})]
print(pd.DataFrame(rows).to_string(index=False))
print(f"""
  說明：目前 C 狀態的曝險上限是 0.5 × 波動係數。以 2026-07-31 的 ATR% 3.36 計算 = 0.27 倍。
        若核心部位（現股0050，風險係數 1.09）也受此約束，核心最多只能持有淨資產的
        {0.27/1.09:.0%}，等於「核心不動」這個設計根本無法成立。
        => 若採用核心＋戰術，核心必須豁免於狀態槓桿上限，改用固定的絕對比例上限。""")

import os
NW = float(os.environ.get("NET_WORTH", 1_000_000))    # 用法: NET_WORTH=5000000 python ...
print("\n" + "=" * 108)
print(f"【D】從 {NW:,.0f} 出發，各配置的實際金額（以 3 年報酬與最大回撤推算）")
print("=" * 108)
rows = []
for lbl, w, core_timed in [("全部戰術（現況）", 0.0, False), ("核心40%", 0.4, False),
                           ("核心50%", 0.5, False), ("核心50%＋核心也擇時", 0.5, True),
                           ("核心75%", 0.75, False)]:
    pc = p_core if core_timed else 1.0
    r = w * (pc * core_r if core_timed else core_r) + (1 - w) * p_tac * tac_raw * LEV
    eq = (1 + r.fillna(0)).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    rows.append({"配置": lbl, "3年後": f"{NW*eq.iloc[-1]:,.0f}",
                 "最大回撤時": f"{NW*(1+dd):,.0f}", "回撤幅度": f"{dd:.1%}"})
print(pd.DataFrame(rows).to_string(index=False))
print("\n  註：3年報酬是 2023-2026 大多頭的結果，不可外推。回撤欄才是該看的。")
