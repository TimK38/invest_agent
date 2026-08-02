"""爬個股/ETF 日線，增量併入 data/stocks_daily.csv（單一檔案，用 sid 區分）

用法:
  python fetch/fetch_stocks.py                    # 更新檔案內既有標的（最近 2 個月）
  python fetch/fetch_stocks.py 2454 00981A        # 新增/更新指定標的（最近 2 個月）
  python fetch/fetch_stocks.py 2454 --since 2023-07   # 指定起始年月，初次建檔用

標的名稱自動從 TWSE 回傳的 title 取得，不需手動維護對照表。
"""
import time, sys, re, argparse, pathlib
from datetime import date
import requests
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import STOCKS_RAW

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date={ym}01&stockNo={sid}&response=json"


def months(start, end):
    y, m = start
    while (y, m) <= end:
        yield f"{y}{m:02d}"
        m += 1
        if m > 12:
            m, y = 1, y + 1


def num(x):
    x = str(x).replace(",", "").strip()
    try:
        return float(x)
    except ValueError:
        return float("nan")


def parse_name(title, sid):
    """'115年07月 00981A 主動統一台股增長 各日成交資訊' -> '主動統一台股增長'"""
    m = re.search(rf"{re.escape(sid)}\s+(.+?)\s+各日成交資訊", title or "")
    return m.group(1).strip() if m else sid


def fetch_one(sid, rng):
    rows, name = [], sid
    for ym in months(*rng):
        for attempt in range(3):
            try:
                r = S.get(URL.format(ym=ym, sid=sid), timeout=20)
                if r.status_code == 200:
                    j = r.json()
                    if j.get("stat") == "OK":
                        name = parse_name(j.get("title", ""), sid)
                        for d in j["data"]:
                            y, m, dd = d[0].split("/")
                            rows.append({"sid": sid, "date": date(int(y) + 1911, int(m), int(dd)),
                                         "vol": num(d[1]), "open": num(d[3]), "high": num(d[4]),
                                         "low": num(d[5]), "close": num(d[6])})
                    break          # stat 非 OK = 該月無資料(未上市/停牌)，正常跳過
            except Exception as e:
                print(f"  {sid} {ym} {type(e).__name__}", file=sys.stderr)
                time.sleep(4 * (attempt + 1))
        time.sleep(2.2)
    for r in rows:
        r["name"] = name
    return rows, name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sids", nargs="*", help="股票代號，省略則更新檔案內既有標的")
    ap.add_argument("--since", metavar="YYYY-MM", help="起始年月（預設只抓最近 2 個月）")
    a = ap.parse_args()

    old = (pd.read_csv(STOCKS_RAW, parse_dates=["date"], dtype={"sid": str})
           if STOCKS_RAW.exists() else pd.DataFrame())
    sids = a.sids or (sorted(old.sid.unique()) if len(old) else [])
    if not sids:
        sys.exit("沒有指定標的，且 data/stocks_daily.csv 不存在")

    today = date.today()
    if a.since:
        y, m = map(int, a.since.split("-"))
        rng = ((y, m), (today.year, today.month))
    else:
        y, m = today.year, today.month - 1
        if m < 1:
            m, y = 12, y - 1
        rng = ((y, m), (today.year, today.month))

    frames = []
    for sid in sids:
        rows, name = fetch_one(sid, rng)
        print(f"  {sid:8s} {name:16s} {len(rows):5d} 列", flush=True)
        if rows:
            frames.append(pd.DataFrame(rows))

    if not frames:
        sys.exit("沒有取得任何資料")
    new = pd.concat(frames)
    new["date"] = pd.to_datetime(new["date"])
    cols = ["sid", "name", "date", "vol", "open", "high", "low", "close"]
    n0 = len(old)
    df = (pd.concat([old, new[cols]]) if n0 else new[cols])
    df = df.drop_duplicates(["sid", "date"], keep="last").sort_values(["sid", "date"]).reset_index(drop=True)
    df.to_csv(STOCKS_RAW, index=False)

    print(f"\n原有 {n0} 列 → 更新後 {len(df)} 列（新增 {len(df)-n0}）")
    print(df.groupby(["sid", "name"]).agg(列數=("close", "size"), 起=("date", "min"),
                                          迄=("date", "max")).to_string())
    print("\n※ 若標的曾發生股票分割，記得重跑：python fetch/clean_stocks.py")


if __name__ == "__main__":
    main()
