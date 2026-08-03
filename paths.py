"""集中管理檔案路徑 — 所有腳本都從這裡取，不要各自寫死

子目錄的腳本用法：
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from paths import TAIEX_RAW, STOCKS_RAW
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

# ---- 原始資料（永久保留，逐日累積，重爬成本高）----
# 三類分開存放，方便各自追蹤；代號以 "00" 開頭者視為 ETF（台股慣例）
TAIEX_RAW = DATA / "taiex_daily.csv"      # 大盤：加權指數日 OHLC + 成交量值
STOCKS_RAW = DATA / "stocks_daily.csv"    # 個股
ETF_RAW = DATA / "etf_daily.csv"          # ETF

# ---- 衍生資料（可隨時重生，改原始資料後務必重跑）----
TAIEX_ENRICHED = DATA / "taiex_enriched.csv"   # 由 research/analyze.py 產生
# 個股與 ETF 合併後還原分割/減資；多一個 kind 欄位可篩選。分析腳本一律讀這份，
# 因為組合裡本來就同時有個股與 ETF，拆成兩份反而每支腳本都要 concat。
PRICES_ADJ = DATA / "prices_adj.csv"           # 由 fetch/clean_stocks.py 產生


def is_etf(sid):
    """台股 ETF 代號以 00 開頭（0050、00631L、009816、00981A…）"""
    return str(sid).startswith("00")

ARTICLES = ROOT / "文章"
STRATEGY = ROOT / "STRATEGY.md"
