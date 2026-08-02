"""還原股票分割 — TWSE STOCK_DAY 為未調整價，分割停牌會造成假暴跌

作法：不猜分割比例。偵測「停牌後復牌的異常跳空」，把那一天的報酬
      用『大盤同期漲跌 × 標的槓桿倍數』取代，再以報酬序列重建還原價。
      誤差僅限那一天，不會像猜錯比例一樣污染整段序列。
"""
import sys, pathlib
import numpy as np, pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import STOCKS_RAW, STOCKS_ADJ, TAIEX_RAW

st = pd.read_csv(STOCKS_RAW, parse_dates=["date"], dtype={"sid": str})
idx = pd.read_csv(TAIEX_RAW, parse_dates=["date"]).set_index("date").close
LEV = {"00631L": 2.0}          # 槓桿型 ETF 的倍數，其餘視為 1.0

out = []
for sid, g in st.groupby("sid"):
    g = g.sort_values("date").reset_index(drop=True).copy()
    lev = LEV.get(sid, 1.0)
    r = g.close.pct_change()
    gap_days = (g.date - g.date.shift()).dt.days
    # 分割判定：單日跌 >15% 且 前一交易日相隔 >4 天(停牌)
    split = (r < -0.15) & (gap_days > 4)
    for k in g.index[split]:
        imv = idx.loc[g.date[k]] / idx.loc[g.date[k - 1]] - 1
        implied = lev * imv
        # 隱含比例接近 1 = 只是連假後的真實暴跌(如 2025/4/7 關稅崩盤)，不是分割
        if g.close[k - 1] * (1 + implied) / g.close[k] < 1.5:
            split[k] = False
            continue
        print(f"  {sid} {g.date[k]:%Y-%m-%d} 分割還原: 原始報酬 {r[k]*100:+.1f}% "
              f"-> 大盤隱含 {implied*100:+.2f}% (停牌 {int(gap_days[k])} 天, "
              f"隱含比例 {g.close[k-1]*(1+implied)/g.close[k]:.2f})")
        r[k] = implied
    r = r.fillna(0.0)
    # 錨定在「最新成交價」而非起始價：分割後的價格與實際盤面一致，
    # 分割前的歷史價則被換算成當前尺度(等同除以分割倍數)。報酬率不受影響。
    series = (1 + r).cumprod()
    g["adj_close"] = g.close.iloc[-1] * series / series.iloc[-1]
    g["adj_ret"] = r
    g["is_split"] = split.values
    # 高低價按當日 adj/raw 比例縮放，供 ATR 使用
    sc = g.adj_close / g.close
    g["adj_high"], g["adj_low"] = g.high * sc, g.low * sc
    out.append(g)

df = pd.concat(out).sort_values(["sid", "date"])
df.to_csv(STOCKS_ADJ, index=False)
print(f"\n還原完成 {len(df)} 筆 -> {STOCKS_ADJ}")
chk = df.groupby(["sid", "name"]).apply(lambda x: pd.Series({
    "期間": f"{x.date.min():%Y-%m}~{x.date.max():%Y-%m}",
    "報酬(還原後)": f"{x.adj_close.iloc[-1]/x.adj_close.iloc[0]-1:+.0%}",
    "報酬(還原前)": f"{x.close.iloc[-1]/x.close.iloc[0]-1:+.0%}",
    "最大回撤": f"{(x.adj_close/x.adj_close.cummax()-1).min():.0%}",
    "末筆還原=實際": "OK" if abs(x.adj_close.iloc[-1] - x.close.iloc[-1]) < 0.01 else "XX",
}), include_groups=False)
print(chk.to_string())
print(f"\n[對照] 加權指數同期 {idx.iloc[-1]/idx.iloc[0]-1:+.0%}")
print("\n註：TWSE 原始價亦未還原『除權息』，故各標的報酬略為低估(高股息標的每年約低 2~4%)。"
      "\n    此偏差對 Beta、波動率、均線訊號的影響可忽略，但比較長期總報酬時需留意。")
