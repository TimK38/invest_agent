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
TAIEX_RAW = DATA / "taiex_daily.csv"      # 大盤日 OHLC + 成交量值
STOCKS_RAW = DATA / "stocks_daily.csv"    # 所有個股/ETF 日線（用 sid 欄位區分）

# ---- 衍生資料（可隨時重生，改原始資料後務必重跑）----
TAIEX_ENRICHED = DATA / "taiex_enriched.csv"   # 由 research/analyze.py 產生
STOCKS_ADJ = DATA / "stocks_adj.csv"           # 由 fetch/clean_stocks.py 產生（分割還原）

ARTICLES = ROOT / "文章"
STRATEGY = ROOT / "STRATEGY.md"
