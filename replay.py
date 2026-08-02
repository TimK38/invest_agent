"""交易回放 — 假設在某天用某價買了某檔，之後每天照 §6 規則會發生什麼

用途是驗證，不是預測。每一天的判斷只用**該日（含）以前**的資料：
均線與 ATR 本來就只看過去，大盤狀態逐日以 taiex_state(asof) 重算，
風險係數固定用進場日當時的實測值（事後重算會是未來資料）。

用法:
  python replay.py 3481 --entry 2026-06-17 --price 55 --lots 100
  python replay.py 3481 --entry 2026-06-17 --price 55 --lots 100 --stop 50 --cash
  python replay.py 3481 --entry 2026-06-17 --price 55 --lots 100 --until 2026-07-15

出場順位（§6 兩條軸線取先觸發者，同日多條觸發取最嚴重的）：
  1. 大盤轉 C/D 且為融資 → 全出（§3 融資須歸零）
  2. 收盤跌破當初停損     → 全出
  3. 收盤跌破 SMA20       → 全出
  4. 收盤跌破 EMA8        → 出 1/2（只執行一次）
  5. 觸及第一目標         → 賣 30%，停損上移至成本（只執行一次）
"""
import argparse, sys
import numpy as np, pandas as pd

import profile_loader
from paths import STOCKS_ADJ
from portfolio_check import taiex_state, DEFAULT_RC

MAINT = 1.30


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sid")
    ap.add_argument("--entry", required=True, help="進場日 YYYY-MM-DD")
    ap.add_argument("--price", type=float, required=True, help="成交價")
    ap.add_argument("--lots", type=float, required=True)
    ap.add_argument("--stop", type=float, help="不給就用進場日的 2×ATR 與 10 日低取較近")
    ap.add_argument("--cash", action="store_true")
    ap.add_argument("--until", help="回放到哪天，預設到資料末日")
    ap.add_argument("--net-worth", type=float)
    profile_loader.add_arg(ap)
    a = ap.parse_args()

    cfg = profile_loader.load(a.profile)
    NW = a.net_worth or cfg["net_worth"]
    mrate = cfg["margin_rate"]
    use_margin = cfg["margin_allowed"] and not a.cash
    entry = pd.Timestamp(a.entry)

    st = pd.read_csv(STOCKS_ADJ, parse_dates=["date"], dtype={"sid": str})
    g = st[st.sid == a.sid].sort_values("date").set_index("date").copy()
    if g.empty:
        sys.exit(f"{a.sid} 無資料")
    c, h, l = g.adj_close, g.adj_high, g.adj_low
    for n in (20, 60):
        g[f"sma{n}"] = c.rolling(n).mean()
    g["ema8"] = c.ewm(span=8, adjust=False).mean()
    g["ema21"] = c.ewm(span=21, adjust=False).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    g["atr20"] = tr.rolling(20).mean()
    if entry not in g.index:
        sys.exit(f"{a.entry} 不是 {a.sid} 的交易日。鄰近："
                 f"{[str(d.date()) for d in g.index[(g.index>=entry-pd.Timedelta('5D'))&(g.index<=entry+pd.Timedelta('5D'))]]}")

    # 風險係數：只用進場日以前的資料
    idx_e, ms_e, code_e = taiex_state(entry)
    mret = idx_e.set_index("date").ret
    hist = g.loc[:entry]
    j = pd.concat([hist.adj_close.pct_change().rename("r"), mret.rename("m")], axis=1,
                  sort=True).dropna()
    if len(j) >= 60:
        dn = j[j.m < 0]
        rc = float(max(np.cov(dn.r, dn.m)[0, 1] / np.var(dn.m), j.r.std() / j.m.std()))
    else:
        rc = DEFAULT_RC

    e = g.loc[entry]
    px0, lots0 = a.price, a.lots
    stop = a.stop or max(px0 - 2 * e.atr20, hist.adj_close.tail(10).min() * 0.99)
    tgt1 = px0 + 2 * (px0 - stop)
    call = px0 * MAINT * mrate if use_margin else None
    mv0 = lots0 * 1000 * px0

    print("=" * 84)
    print(f"  交易回放　{a.sid} {g.name.iloc[-1]}　{entry:%Y-%m-%d} 買進 {lots0:g} 張 @ {px0:.2f}"
          f"　{'融資' if use_margin else '現股'}")
    print("=" * 84)
    print(f"  【進場日盤面】狀態【{code_e}】 加權 {ms_e.close:,.0f} 距高點 {ms_e.dd:.1%} "
          f"ATR% {ms_e.atrp:.2f}")
    print(f"  【進場日個股】收盤 {e.adj_close:.2f}  EMA8 {e.ema8:.2f}  SMA20 {e.sma20:.2f}  "
          f"SMA60 {e.sma60:.2f}  ATR {e.atr20:.2f}")
    print(f"  【部位】市值 {mv0:,.0f}（淨資產 {mv0/NW:.1%}）  風險係數 {rc:.2f}  "
          f"曝險 {mv0*rc:,.0f}（{mv0*rc/NW:.1%}）")
    print(f"  【計畫】停損 {stop:.2f}（-{1-stop/px0:.1%}）"
          + (f"　← 你指定" if a.stop else f"　2×ATR 與 10 日低取較近")
          + f"　第一目標 {tgt1:.2f}（+{tgt1/px0-1:.1%}）"
          + (f"　追繳 {call:.2f}" if call else ""))
    print(f"  【單筆風險】{(px0-stop)*lots0*1000:,.0f} = 淨資產 "
          f"{(px0-stop)*lots0*1000/NW:.2%}（上限 {cfg['max_risk_per_trade']:.1%}）")

    fwd = g.loc[entry:]
    if a.until:
        fwd = fwd.loc[:pd.Timestamp(a.until)]
    fwd = fwd.iloc[1:]

    print(f"\n  ── 逐日回放（{fwd.index[0]:%m/%d} ~ {fwd.index[-1]:%m/%d}，{len(fwd)} 個交易日）"
          + "─" * 20)
    print(f"    {'日期':11s}{'收盤':>8s}{'損益':>8s}{'EMA8':>8s}{'SMA20':>8s}  大盤  動作")

    held, cur_stop, half_done, tgt_done = lots0, stop, False, False
    realized, log = 0.0, []
    for d, r in fwd.iterrows():
        _, _, code = taiex_state(d)
        act, sold = "", 0.0
        if held > 0:
            # §3：現股在 C/D 一樣受曝險上限管，只是不必歸零而是砍到上限內
            cap_lots = (NW * cfg["leverage_caps"][code]
                        * float(np.clip(1.8 / taiex_state(d)[1].atrp, 0.4, 1.0))
                        / (rc * r.adj_close * 1000)) if not use_margin else None
            if use_margin and code in "CD":
                act, sold = f"❗大盤轉 {code}，融資須歸零 → 全出", held
            elif r.adj_close < cur_stop:
                act, sold = f"❗跌破停損 {cur_stop:.2f} → 全出", held
            elif r.adj_close < r.sma20:
                act, sold = f"❗收盤跌破 SMA20 {r.sma20:.2f} → 全出", held
            elif not half_done and r.adj_close < r.ema8:
                act, sold, half_done = f"⚠跌破 EMA8 {r.ema8:.2f} → 出 1/2", held / 2, True
            elif not tgt_done and r.adj_close >= tgt1:
                act, sold, tgt_done = (f"達第一目標 {tgt1:.2f} → 賣 30%，停損上移至成本 {px0:.2f}",
                                       held * 0.30, True)
                cur_stop = px0
            elif cap_lots is not None and held > cap_lots * 1.05:   # 5% 容差，避免每日微幅再平衡
                act = (f"⚠現股曝險超標（{held*1000*r.adj_close*rc/NW:.2f} 倍 > 上限 "
                       f"{cfg['leverage_caps'][code]*float(np.clip(1.8/taiex_state(d)[1].atrp,0.4,1.0)):.2f}）"
                       f" → 減至 {cap_lots:.0f} 張")
                sold = held - cap_lots
            if call and r.adj_close <= call:
                act = f"💀 觸及追繳線 {call:.2f}　" + act
        if sold:
            realized += sold * 1000 * (r.adj_close - px0)
            held -= sold
            log.append((d, r.adj_close, act, sold, held))
        if act or d == fwd.index[-1] or (len(log) and log[-1][0] == d):
            print(f"    {d:%Y-%m-%d} {r.adj_close:>8.2f} {r.adj_close/px0-1:>+7.1%}"
                  f" {r.ema8:>8.2f} {r.sma20:>8.2f}   {code}   {act or ('（無動作）' if held else '（已空手）')}")
        if held <= 1e-9:
            break

    last = fwd.loc[log[-1][0]] if log else fwd.iloc[-1]
    unreal = held * 1000 * (last.adj_close - px0)
    total = realized + unreal
    equity = mv0 * (1 - mrate) if use_margin else mv0

    print(f"\n  ── 結果 " + "─" * 68)
    print(f"    規則執行：實現 {realized:>12,.0f}"
          + (f"　未實現 {unreal:,.0f}（剩 {held:g} 張）" if held else "　（全部出清）"))
    print(f"    　　　　　合計 {total:>12,.0f}　= 投入市值的 {total/mv0:+.1%}"
          + (f"、自備款的 {total/equity:+.1%}" if use_margin else ""))
    bh = lots0 * 1000 * (g.adj_close.iloc[-1] - px0)
    print(f"    買進持有到 {g.index[-1]:%Y-%m-%d}（{g.adj_close.iloc[-1]:.2f}）："
          f"{bh:>12,.0f}　= {bh/mv0:+.1%}"
          + (f"、自備款的 {bh/equity:+.1%}" if use_margin else ""))
    hi, lo = fwd.adj_close.max(), fwd.adj_close.min()
    print(f"    整段期間最高 {hi:.2f} 於 {fwd.adj_close.idxmax():%m/%d}（{hi/px0-1:+.1%}）　"
          f"最低 {lo:.2f} 於 {fwd.adj_close.idxmin():%m/%d}（{lo/px0-1:+.1%}）")
    if use_margin and (fwd.adj_close <= call).any():
        first = fwd[fwd.adj_close <= call].index[0]
        print(f"    ⚠ 若未依規則出場，{first:%Y-%m-%d} 收盤 {fwd.loc[first].adj_close:.2f} "
              f"已跌破追繳線 {call:.2f}")
    print()


if __name__ == "__main__":
    main()
