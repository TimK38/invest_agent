"""還原公司行為造成的價格斷點 — TWSE STOCK_DAY 為未調整價

分割會造成假暴跌（0050 4:1 → -74.8%），減資會造成假暴漲（3481 2024-08 → +14.4%）。
兩者都不是真實報酬，都必須還原。

作法：不猜比例。**以「大盤有交易、該檔沒有」判定停牌**，把復牌那天的報酬
      用『大盤同期漲跌 × 標的槓桿倍數』取代，再以報酬序列重建還原價。
      誤差僅限那一天，不會像猜錯比例一樣污染整段序列。

為什麼不用報酬門檻判定：舊版條件是「單日跌 >15%」，只抓得到向下跳空。
減資是**向上**跳空（+14.4%），永遠抓不到；而 3481 2023-08 那次只有 +3.1%，
任何門檻都抓不到。改用停牌天數判定則兩者通吃，且不受幅度大小影響。

⚠ 前提：raw 檔的交易日必須完整。若某月抓取失敗留下空洞，會被誤判為停牌，
   那天的真實報酬會被大盤報酬取代。每次執行都會列出判定到的事件，請核對。
"""
import sys, pathlib
import numpy as np, pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import STOCKS_RAW, ETF_RAW, PRICES_ADJ, TAIEX_RAW, is_etf

st = pd.concat([pd.read_csv(f, parse_dates=["date"], dtype={"sid": str})
                for f in (STOCKS_RAW, ETF_RAW) if f.exists()])
idx = pd.read_csv(TAIEX_RAW, parse_dates=["date"]).set_index("date").close
LEV = {"00631L": 2.0}          # 槓桿型 ETF 的倍數，其餘視為 1.0
SESSIONS = idx.index           # 大盤交易日，用來判定個股停牌
HALT_MIN = 3                   # 漏掉幾個大盤交易日就視為停牌（減資/分割換發新股約 7~8 日）

out = []
for sid, g in st.groupby("sid"):
    g = g.sort_values("date").reset_index(drop=True).copy()
    lev = LEV.get(sid, 1.0)
    r = g.close.pct_change()
    # 停牌判定：該檔相鄰兩筆之間，大盤仍開盤 ≥ HALT_MIN 個交易日
    # （用大盤交易日而非日曆天，連假不會誤判；上市首日無前筆，自然跳過）
    prev = g.date.shift()
    halt = pd.Series(False, index=g.index)
    for k in range(1, len(g)):
        halt.iat[k] = int(((SESSIONS > prev[k]) & (SESSIONS < g.date[k])).sum()) >= HALT_MIN
    for k in g.index[halt]:
        implied = lev * (idx.loc[g.date[k]] / idx.loc[prev[k]] - 1)
        n = int(((SESSIONS > prev[k]) & (SESSIONS < g.date[k])).sum())
        print(f"  {sid} {prev[k]:%Y-%m-%d} → {g.date[k]:%Y-%m-%d} 停牌 {n} 個交易日，"
              f"復牌日還原: 原始報酬 {r[k]*100:+.1f}% → 大盤隱含 {implied*100:+.2f}%"
              f"（隱含比例 {g.close[k-1]*(1+implied)/g.close[k]:.3f}）")
        r[k] = implied
    r = r.fillna(0.0)
    # 錨定在「最新成交價」而非起始價：分割後的價格與實際盤面一致，
    # 分割前的歷史價則被換算成當前尺度(等同除以分割倍數)。報酬率不受影響。
    series = (1 + r).cumprod()
    g["adj_close"] = g.close.iloc[-1] * series / series.iloc[-1]
    g["adj_ret"] = r
    g["is_halt_adj"] = halt.values      # 該日報酬為停牌還原值，非真實成交報酬
    # 高低價按當日 adj/raw 比例縮放，供 ATR 使用
    sc = g.adj_close / g.close
    g["adj_high"], g["adj_low"] = g.high * sc, g.low * sc
    out.append(g)

df = pd.concat(out).sort_values(["sid", "date"])
df["kind"] = np.where(df.sid.map(is_etf), "ETF", "個股")
df.to_csv(PRICES_ADJ, index=False)
print(f"\n還原完成 {len(df)} 筆 -> {PRICES_ADJ}")
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
