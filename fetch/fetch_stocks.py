"""爬個股/ETF 日線 —— 個股存 data/stocks_daily.csv、ETF 存 data/etf_daily.csv

用法:
  python fetch/fetch_stocks.py                    # 更新檔案內既有標的
  python fetch/fetch_stocks.py 2454 00981A        # 新增/更新指定標的
  python fetch/fetch_stocks.py 2454 --since 2023-07   # 指定起始年月，新標的建檔用

不給 --since 時，**每檔各自從自己最後一筆資料那個月補到現在**（至少涵蓋最近兩個月），
所以隔了幾個月沒更新也不會留下空洞。執行完會列出缺漏檢查。

標的名稱自動從回傳的 title 取得，不需手動維護對照表。
**上市（TWSE）抓不到時會自動改抓上櫃（TPEx）**——例如合晶 6182 是上櫃股。
兩邊的成交量單位不同（TWSE 給股數、TPEx 給張數），已在 fetch_otc 內統一為股數。
"""
import time, sys, re, argparse, pathlib
from datetime import date
import requests
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import STOCKS_RAW, ETF_RAW, TAIEX_RAW, is_etf

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date={ym}01&stockNo={sid}&response=json"
OTC_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"   # 上櫃


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


def fetch_otc(sid, ym):
    """櫃買中心（上櫃股）。欄位：日期 成交張數 成交仟元 開 高 低 收 漲跌 筆數
    成交量單位是**張**，這裡乘 1000 換成股數，與 TWSE 一致。"""
    r = S.get(OTC_URL, params={"code": sid, "date": f"{ym[:4]}/{ym[4:]}/01",
                               "id": "", "response": "json"}, timeout=20)
    j = r.json()
    if j.get("stat") != "ok" or not j.get("tables"):
        return [], None
    out = []
    for d in j["tables"][0].get("data") or []:
        y, m, dd = d[0].split("/")
        out.append({"sid": sid, "date": date(int(y) + 1911, int(m), int(dd)),
                    "vol": num(d[1]) * 1000, "open": num(d[3]), "high": num(d[4]),
                    "low": num(d[5]), "close": num(d[6])})
    return out, (j.get("name") or "").strip() or None


def fetch_one(sid, rng):
    rows, name, otc = [], sid, False
    for ym in months(*rng):
        for attempt in range(3):
            try:
                got = []
                if not otc:
                    r = S.get(URL.format(ym=ym, sid=sid), timeout=20)
                    if r.status_code == 200:
                        j = r.json()
                        if j.get("stat") == "OK":
                            name = parse_name(j.get("title", ""), sid)
                            for d in j["data"]:
                                y, m, dd = d[0].split("/")
                                got.append({"sid": sid,
                                            "date": date(int(y) + 1911, int(m), int(dd)),
                                            "vol": num(d[1]), "open": num(d[3]), "high": num(d[4]),
                                            "low": num(d[5]), "close": num(d[6])})
                # 上市查無資料 → 改查上櫃。一旦確認是上櫃股，後續月份直接走 TPEx
                if not got:
                    got, oname = fetch_otc(sid, ym)
                    if got:
                        otc, name = True, (oname or name)
                rows.extend(got)
                break          # 兩邊都無資料 = 該月未上市/停牌，正常跳過
            except Exception as e:
                print(f"  {sid} {ym} {type(e).__name__}", file=sys.stderr)
                time.sleep(4 * (attempt + 1))
        time.sleep(2.2)
    for r in rows:
        r["name"] = name
    return rows, (name + "(上櫃)" if otc else name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sids", nargs="*", help="股票代號，省略則更新檔案內既有標的")
    ap.add_argument("--since", metavar="YYYY-MM", help="起始年月（預設只抓最近 2 個月）")
    a = ap.parse_args()

    def read(pth):
        return (pd.read_csv(pth, parse_dates=["date"], dtype={"sid": str})
                if pth.exists() else pd.DataFrame())
    old = pd.concat([read(STOCKS_RAW), read(ETF_RAW)]) if (STOCKS_RAW.exists() or ETF_RAW.exists()) \
        else pd.DataFrame()
    sids = a.sids or (sorted(old.sid.unique()) if len(old) else [])
    if not sids:
        sys.exit("沒有指定標的，且 data/stocks_daily.csv 與 data/etf_daily.csv 都不存在")

    today = date.today()
    m0 = today.month - 1 or 12
    y0 = today.year if today.month > 1 else today.year - 1
    default_rng = ((y0, m0), (today.year, today.month))
    last = (old.groupby("sid").date.max().to_dict() if len(old) else {})

    def range_for(sid):
        """不給 --since 時，從該檔**最後一筆資料那個月**補到現在，而不是固定抓兩個月。
        否則某檔隔了幾個月沒更新就會留下空洞，而 clean_stocks.py 會把空洞誤判成停牌。"""
        if a.since:
            y, m = map(int, a.since.split("-"))
            return ((y, m), (today.year, today.month))
        ld = last.get(sid)
        if ld is None or pd.isna(ld):
            return default_rng
        return (min((ld.year, ld.month), default_rng[0]), (today.year, today.month))

    frames = []
    for sid in sids:
        rng = range_for(sid)
        rows, name = fetch_one(sid, rng)
        print(f"  {sid:8s} {name:16s} {len(rows):5d} 列"
              f"   （{rng[0][0]}-{rng[0][1]:02d} 起）", flush=True)
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
    # 名稱統一用最新的一筆：交易所會改名（00631L 由「0050正2」改為「元大台灣50正2」），
    # 若不統一，同一個 sid 會在 groupby 出現兩列，後續依名稱做的比對也會錯。
    latest = df.groupby("sid").name.last()
    renamed = [(s, n) for s, n in df.groupby("sid").name.unique().items() if len(n) > 1]
    df["name"] = df.sid.map(latest)
    for s, ns in renamed:
        print(f"  ℹ {s} 名稱曾變更 {list(ns)} → 統一為「{latest[s]}」")

    # 個股與 ETF 分開存放，各自一個檔案，方便追蹤
    etf_mask = df.sid.map(is_etf)
    df[~etf_mask].to_csv(STOCKS_RAW, index=False)
    df[etf_mask].to_csv(ETF_RAW, index=False)
    print(f"\n  個股 {(~etf_mask).sum():5d} 列 → {STOCKS_RAW.name}"
          f"　（{df[~etf_mask].sid.nunique()} 檔）")
    print(f"  ETF  {etf_mask.sum():5d} 列 → {ETF_RAW.name}"
          f"　（{df[etf_mask].sid.nunique()} 檔）")

    print(f"\n原有 {n0} 列 → 更新後 {len(df)} 列（新增 {len(df)-n0}）")
    print(df.groupby(["sid", "name"]).agg(列數=("close", "size"), 起=("date", "min"),
                                          迄=("date", "max")).to_string())

    # 缺漏檢查：以大盤交易日為基準，列出每檔在自己上市期間內漏掉的日子。
    # clean_stocks.py 用「大盤有交易而該檔沒有」判定停牌，抓取空洞會被誤判成停牌，
    # 那天的真實報酬會被大盤報酬取代 —— 所以這裡一定要把空洞跟真停牌分開列出來。
    if TAIEX_RAW.exists():
        sess = pd.read_csv(TAIEX_RAW, parse_dates=["date"]).date
        print("\n  ── 缺漏檢查（以大盤交易日為基準）" + "─" * 40)
        clean = True
        for sid, g in df.groupby("sid"):
            span = sess[(sess >= g.date.min()) & (sess <= g.date.max())]
            miss = sorted(set(span) - set(g.date))
            if miss:
                clean = False
                months = sorted({f"{d:%Y-%m}" for d in miss})
                print(f"    {sid:8s} 缺 {len(miss):3d} 天　{months[0]}~{months[-1]}"
                      f"　例：{miss[0]:%Y-%m-%d}")
                print(f"      → 若是抓取失敗請補：fetch_stocks.py {sid} --since {months[0]}")
                print(f"        若確認是停牌（減資/分割換發新股），clean_stocks.py 會自動還原")
        if clean:
            print("    ✅ 每檔在自己的上市期間內都沒有缺漏")

    print("\n※ 補完資料一定要重跑：python fetch/clean_stocks.py（還原分割與減資）")


if __name__ == "__main__":
    main()
