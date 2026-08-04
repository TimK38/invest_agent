#!/usr/bin/env python
"""台股盤面狀態機 — 每日執行，輸出當下的市場狀態、槓桿上限、部位係數

用法:
  python market_state.py                    # 更新資料並輸出盤面狀態
  python market_state.py --no-fetch         # 只用現有資料(不連網)
  python market_state.py --size 淨資產 進場價 停損價   # 反推可買股數
"""
import sys, time, argparse
from datetime import date, datetime
import numpy as np
import pandas as pd

import profile_loader
from portfolio_check import freshness
from paths import TAIEX_RAW as CSV
OHLC = "https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?date={ym}01&response=json"
VOL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={ym}01&response=json"

# ---- 策略門檻(來自 STRATEGY.md，共用；個人參數見 profiles/) ----
ATR_EXTREME, ATR_CALM = 2.5, 2.0      # ATR% 極端 / 平靜門檻
DD_LOCK, DD_FLAT = -0.08, -0.12       # 距高點回撤：鎖利 / 空手


def num(s):
    s = str(s).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return float("nan")


def roc(s):
    y, m, d = s.split("/")
    return date(int(y) + 1911, int(m), int(d))


def fetch_recent(df):
    """補抓最近兩個月的資料"""
    import requests
    S = requests.Session()
    S.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
    today = date.today()
    yms = []
    y, m = today.year, today.month
    for _ in range(2):
        yms.append(f"{y}{m:02d}")
        m -= 1
        if m < 1:
            m, y = 12, y - 1
    o, v = [], []
    for ym in reversed(yms):
        for url, bucket in ((OHLC, o), (VOL, v)):
            try:
                r = S.get(url.format(ym=ym), timeout=20)
                j = r.json()
                if j.get("stat") == "OK":
                    bucket.extend(j["data"])
            except Exception as e:
                print(f"  [warn] {ym} {type(e).__name__}: {e}", file=sys.stderr)
            time.sleep(1.5)
    if not o:
        return df
    no = pd.DataFrame([{"date": roc(r[0]), "open": num(r[1]), "high": num(r[2]),
                        "low": num(r[3]), "close": num(r[4])} for r in o])
    nv = pd.DataFrame([{"date": roc(r[0]), "vol_shares": num(r[1]), "turnover": num(r[2]),
                        "trades": num(r[3]), "chg_pts": num(r[5])} for r in v])
    new = no.merge(nv, on="date", how="left")
    new["date"] = pd.to_datetime(new["date"])
    out = pd.concat([df, new]).drop_duplicates("date", keep="last").sort_values("date")
    out.to_csv(CSV, index=False)
    return out.reset_index(drop=True)


def enrich(df):
    c, h, l = df.close, df.high, df.low
    for n in (5, 8, 10, 20, 60, 120, 200):
        df[f"sma{n}"] = c.rolling(n).mean()
    df["ema8"] = c.ewm(span=8, adjust=False).mean()
    df["ema21"] = c.ewm(span=21, adjust=False).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr20"] = tr.rolling(20).mean()
    df["atrp"] = df.atr20 / c * 100
    df["ret"] = c.pct_change()
    df["dd"] = c / c.cummax() - 1
    df["tovr20"] = df.turnover.rolling(20).mean()
    df["vratio"] = df.turnover / df.tovr20
    return df


def classify(r):
    """回傳 (狀態代碼, 說明, 觸發原因清單)"""
    reasons = []
    if r.close < r.sma120:
        reasons.append(f"收盤 {r.close:,.0f} < SMA120 {r.sma120:,.0f}")
    if r.dd <= DD_FLAT:
        reasons.append(f"距高點 {r.dd:.1%} ≤ {DD_FLAT:.0%}")
    if reasons:
        return "D", "空手", reasons

    if r.close < r.sma60:
        reasons.append(f"收盤 {r.close:,.0f} < SMA60 {r.sma60:,.0f}")
    # v1.7：波動警訊加上價格確認 —— ATR 不分方向，急漲一樣把它推高
    if r.atrp > ATR_EXTREME and r.close < r.sma20:
        reasons.append(f"ATR% {r.atrp:.2f} > {ATR_EXTREME} 且 收盤跌破 SMA20 {r.sma20:,.0f}")
    if r.dd <= DD_LOCK:
        reasons.append(f"距高點 {r.dd:.1%} ≤ {DD_LOCK:.0%}")
    if reasons:
        return "C", "鎖利", reasons

    if r.close < r.sma20:
        return "B", "持有", [f"收盤 {r.close:,.0f} < SMA20 {r.sma20:,.0f}（但仍在 SMA60 上）"]

    ok = r.sma20 > r.sma60 > r.sma120 and r.atrp <= ATR_CALM
    if ok:
        return "A", "攻擊", ["多頭排列 SMA20>SMA60>SMA120", f"ATR% {r.atrp:.2f} ≤ {ATR_CALM}"]
    why = []
    if not (r.sma20 > r.sma60 > r.sma120):
        why.append(f"均線未多頭排列 (20:{r.sma20:,.0f} 60:{r.sma60:,.0f} 120:{r.sma120:,.0f})")
    if r.atrp > ATR_CALM:
        why.append(f"ATR% {r.atrp:.2f} > {ATR_CALM}（波動未收斂）")
    return "B", "持有", why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--asof", metavar="YYYY-MM-DD",
                    help="切回某日重現當時判斷（只用該日及之前的資料，無後見之明）")
    ap.add_argument("--size", nargs=2, type=float, metavar=("進場價", "停損價"),
                    help="反推可買股數（淨資產取自 profile）")
    profile_loader.add_arg(ap)
    a = ap.parse_args()

    cfg = profile_loader.load(a.profile)
    LEV_CAP = cfg["leverage_caps"]
    MAX_RISK_PCT = cfg["max_risk_per_trade"]

    df = pd.read_csv(CSV, parse_dates=["date"])
    if not a.no_fetch and not a.asof:
        try:
            df = fetch_recent(df)
        except Exception as e:
            print(f"[warn] 更新失敗，改用現有資料: {e}", file=sys.stderr)
    df = df.sort_values("date").reset_index(drop=True)
    if a.asof:
        cut = pd.Timestamp(a.asof)
        df = df[df.date <= cut].reset_index(drop=True)
        if df.empty:
            sys.exit(f"錯誤：{a.asof} 之前沒有資料")
    df = enrich(df)
    r = df.iloc[-1]
    code, name, reasons = classify(r)

    pos_coef = float(np.clip(1.8 / r.atrp, 0.4, 1.0))
    cap = LEV_CAP[code]
    target = cap * pos_coef

    print("=" * 66)
    print(f"  台股盤面狀態    資料日 {r.date:%Y-%m-%d}（{'今日' if r.date.date()==date.today() else '最後交易日'}）")
    print(f"  設定檔 {cfg['_name']}   淨資產 {cfg['net_worth']:,.0f}   "
          f"融資 {'可用' if cfg['margin_allowed'] else '不使用'}")
    print("=" * 66)
    print(f"  加權指數 {r.close:>10,.0f}   {r.ret*100:+.2f}%    距歷史高點 {r.dd:+.1%}")
    print(f"  ATR%     {r.atrp:>10.2f}   （3年均 {df.atrp.mean():.2f}，>{ATR_EXTREME} 為極端）")
    if not a.asof:
        print(f"\n  {freshness(r.date)[1]}")
    print(f"  20日均量 {r.tovr20/1e8:>10,.0f} 億   當日量比 {r.vratio:.2f}")
    print("\n  均線位置")
    for k in ("ema8", "ema21", "sma20", "sma60", "sma120", "sma200"):
        v = r[k]
        print(f"    {k.upper():7s} {v:>10,.0f}   {'▲在上' if r.close > v else '▼跌破'}   乖離 {r.close/v-1:+6.1%}")

    print(f"\n  ▶ 狀態  【{code} {name}】")
    for x in reasons:
        print(f"      · {x}")
    print(f"\n  ▶ 有效槓桿上限   {cap:.1f} × 波動係數 {pos_coef:.2f} = 【{target:.2f} 倍】")
    if code in ("C", "D"):
        print("  ▶ 融資餘額       必須為 0")
    elif code == "B":
        print("  ▶ 融資           不得新增")
    print(f"  ▶ 單筆最大虧損   淨資產 × {MAX_RISK_PCT:.1%}")

    # 再進場訊號
    # v1.7：訊號本身就要求收盤 > SMA20，波動警訊（需 收<SMA20）自動不成立 → ATR 條件消失。
    # §7 的表格本來就顯示「站回SMA20 且 在SMA60之上」勝率最高，加 ATR 條件並未改善。
    sig = (r.close > r.sma20) and (r.close > r.sma60)
    print(f"\n  ▶ 再進場訊號（收盤>SMA20 且 >SMA60）: "
          f"{'✅ 成立 — 可建 1/3 部位' if sig else '❌ 不成立'}")
    if not sig:
        need = []
        if r.close <= r.sma20:
            need.append(f"站回 SMA20 {r.sma20:,.0f}")
        if r.close <= r.sma60:
            need.append(f"站回 SMA60 {r.sma60:,.0f}")
        print(f"      待滿足：{'、'.join(need)}")
    if r.atrp > ATR_EXTREME:
        if r.close < r.sma20:
            print(f"      ⚠ ATR% {r.atrp:.2f} > {ATR_EXTREME} 且收盤在 SMA20 之下 → 波動警訊成立，"
                  f"§8 禁令 3：禁止新增融資")
        else:
            print(f"      ℹ ATR% {r.atrp:.2f} > {ATR_EXTREME}，但收盤仍在 SMA20 之上 → "
                  f"視為順勢波動，不構成警訊（v1.7）")

    if cfg["notes"]:
        print(f"\n  ▶ 你的個人弱點提醒\n      {cfg['notes']}")

    if a.size:
        entry, stop = a.size
        eq = cfg["net_worth"]
        risk = entry - stop
        print("\n" + "=" * 66)
        print("  部位大小反推")
        print("=" * 66)
        if risk <= 0:
            print("  ✗ 停損價必須低於進場價")
        else:
            budget = eq * MAX_RISK_PCT
            shares = budget / risk
            print(f"  淨資產 {eq:,.0f} × {MAX_RISK_PCT:.1%} = 可承受虧損 {budget:,.0f} 元")
            print(f"  每股風險 {entry:,.2f} − {stop:,.2f} = {risk:,.2f} 元")
            print(f"  ▶ 最大可買 {shares:,.0f} 股 ≈ {shares/1000:.1f} 張   部位金額 {shares*entry:,.0f} 元"
                  f"（佔淨資產 {shares*entry/eq:.1%}）")
            print(f"  ▶ 此狀態下總曝險上限 {eq*target:,.0f} 元（{target:.2f} 倍）")
    print()


if __name__ == "__main__":
    main()
