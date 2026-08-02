"""爬取台股加權指數日 OHLC + 成交量值 (TWSE 官方 API)

用法:
  python fetch/fetch_twse.py                 # 增量更新最近 2 個月
  python fetch/fetch_twse.py --full          # 重爬 2023-07 至今（初次建檔用）
"""
import time, sys, argparse, pathlib
from datetime import date
import requests
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import TAIEX_RAW

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
OHLC_URL = "https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?date={ym}01&response=json"
VOL_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={ym}01&response=json"


def months(start, end):
    y, m = start
    while (y, m) <= end:
        yield f"{y}{m:02d}"
        m += 1
        if m > 12:
            m, y = 1, y + 1


def get(url, tries=4):
    for i in range(tries):
        try:
            r = S.get(url, timeout=20)
            if r.status_code == 200:
                j = r.json()
                return j if j.get("stat") == "OK" else None
        except Exception as e:
            print(f"  {type(e).__name__}: {e}, retry", file=sys.stderr)
        time.sleep(3 * (i + 1))
    return None


def roc(s):
    y, m, d = s.split("/")
    return date(int(y) + 1911, int(m), int(d))


def num(s):
    s = str(s).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="重爬 2023-07 至今")
    a = ap.parse_args()

    today = date.today()
    if a.full:
        rng = ((2023, 7), (today.year, today.month))
    else:
        y, m = today.year, today.month - 1
        if m < 1:
            m, y = 12, y - 1
        rng = ((y, m), (today.year, today.month))

    rows_o, rows_v = [], []
    for ym in months(*rng):
        j = get(OHLC_URL.format(ym=ym))
        if j:
            rows_o += [{"date": roc(r[0]), "open": num(r[1]), "high": num(r[2]),
                        "low": num(r[3]), "close": num(r[4])} for r in j["data"]]
        time.sleep(2.0)
        j = get(VOL_URL.format(ym=ym))
        if j:
            rows_v += [{"date": roc(r[0]), "vol_shares": num(r[1]), "turnover": num(r[2]),
                        "trades": num(r[3]), "chg_pts": num(r[5])} for r in j["data"]]
        time.sleep(2.0)
        print(f"  {ym}  累計 {len(rows_o)} 列", flush=True)

    if not rows_o:
        sys.exit("沒有取得任何資料")
    new = (pd.DataFrame(rows_o).drop_duplicates("date")
           .merge(pd.DataFrame(rows_v).drop_duplicates("date"), on="date", how="left"))
    new["date"] = pd.to_datetime(new["date"])

    if TAIEX_RAW.exists():
        old = pd.read_csv(TAIEX_RAW, parse_dates=["date"])
        n0 = len(old)
        df = (pd.concat([old, new]).drop_duplicates("date", keep="last")
              .sort_values("date").reset_index(drop=True))
        print(f"\n原有 {n0} 列 → 更新後 {len(df)} 列（新增 {len(df)-n0}）")
    else:
        df = new.sort_values("date").reset_index(drop=True)
        print(f"\n建檔 {len(df)} 列")

    df.to_csv(TAIEX_RAW, index=False)
    print(f"{TAIEX_RAW}   {df.date.min():%Y-%m-%d} ~ {df.date.max():%Y-%m-%d}")
    print(df.tail(3).to_string(index=False))


if __name__ == "__main__":
    main()
