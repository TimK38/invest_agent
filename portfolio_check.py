"""持股組合體檢 — 曝險、有效槓桿、追繳線、逐檔停損

用法:
  python portfolio_check.py --hold 00981A:50 009816:85
  python portfolio_check.py --hold 00981A:50:26.13 --cash
  python portfolio_check.py --profile 朋友名字 --hold 0050:20

  --hold 格式 SID:張數[:成本價]   可列多筆
  --cash  該組持股為現股（全額自備）；預設依 profile 的 margin_allowed 判斷
  淨資產、單筆風險 %、槓桿上限皆取自 profiles/<名字>.json
"""
import sys, argparse
import numpy as np, pandas as pd

import profile_loader
from paths import STOCKS_ADJ, TAIEX_RAW
pd.set_option("display.width", 200)

ATR_EXTREME = 2.5
DEFAULT_RC = 1.8          # 資料不足時的保守預設


def taiex_state():
    d = pd.read_csv(TAIEX_RAW, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    c, h, l = d.close, d.high, d.low
    for n in (20, 60, 120):
        d[f"sma{n}"] = c.rolling(n).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d["atrp"] = tr.rolling(20).mean() / c * 100
    d["ret"] = c.pct_change()
    d["dd"] = c / c.cummax() - 1
    r = d.iloc[-1]
    if r.close < r.sma120 or r.dd <= -0.12:
        code = "D"
    elif r.close < r.sma60 or r.atrp > ATR_EXTREME or r.dd <= -0.08:
        code = "C"
    elif r.close < r.sma20:
        code = "B"
    else:
        code = "A" if (r.sma20 > r.sma60 > r.sma120 and r.atrp <= 2.0) else "B"
    return d, r, code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", nargs="+", required=True, metavar="SID:張數[:成本]")
    ap.add_argument("--net-worth", type=float, help="覆寫 profile 的淨資產")
    ap.add_argument("--cash", action="store_true", help="這批持股為現股（無融資）")
    profile_loader.add_arg(ap)
    a = ap.parse_args()

    cfg = profile_loader.load(a.profile)
    LEV_CAP = cfg["leverage_caps"]
    MAX_RISK_PCT = cfg["max_risk_per_trade"]
    margin_rate = cfg["margin_rate"]
    use_margin = cfg["margin_allowed"] and not a.cash
    mix = cfg.get("asset_mix") or {"stable_target": 0.72, "tolerance": 0.10, "stable_rc_max": 1.2}

    idx, mstate, code = taiex_state()
    pos_coef = float(np.clip(1.8 / mstate.atrp, 0.4, 1.0))
    target_lev = LEV_CAP[code] * pos_coef
    NW = a.net_worth or cfg["net_worth"]

    st = pd.read_csv(STOCKS_ADJ, parse_dates=["date"], dtype={"sid": str})
    mret = idx.set_index("date").ret

    rows, detail = [], {}
    for spec in a.hold:
        parts = spec.split(":")
        sid, lots = parts[0], float(parts[1])
        cost = float(parts[2]) if len(parts) > 2 else None
        g = st[st.sid == sid].sort_values("date").set_index("date").copy()
        if g.empty:
            print(f"⚠ {sid} 無資料，請先執行: python fetch/fetch_stocks.py {sid} --since 2023-07")
            continue
        c, h, l = g.adj_close, g.adj_high, g.adj_low
        for n in (20, 60, 120):
            g[f"sma{n}"] = c.rolling(n).mean()
        g["ema21"] = c.ewm(span=21, adjust=False).mean()
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        g["atr20"] = tr.rolling(20).mean()
        g["r"] = c.pct_change()
        g["dd"] = c / c.cummax() - 1

        j = pd.concat([g.r.rename("r"), mret.rename("m")], axis=1, sort=True).dropna()
        if len(j) >= 60:
            dn = j[j.m < 0]
            beta_dn = np.cov(dn.r, dn.m)[0, 1] / np.var(dn.m)
            volr = (j.r.std()) / j.m.std()
            rc, note = max(beta_dn, volr), ("樣本偏短" if len(j) < 250 else "")
        else:
            rc, note = DEFAULT_RC, "樣本不足→用預設1.8"

        r = g.iloc[-1]
        mv = lots * 1000 * r.adj_close
        # §4 資產結構分類：風險係數 ≤ 門檻 且 未使用融資 → 穩健，其餘皆為積極
        klass = "穩健" if (rc <= mix["stable_rc_max"] and not use_margin) else "積極"
        detail[sid] = (g, r, rc, cost)
        rows.append({"標的": f"{sid} {g.name.iloc[-1]}", "類別": klass, "張數": lots,
                     "收盤": r.adj_close, "市值": mv, "風險係數": round(rc, 2), "曝險": mv * rc,
                     "距高點": f"{r.dd:.1%}", "S20": "▲" if r.adj_close > r.sma20 else "▼",
                     "S60": "▲" if r.adj_close > r.sma60 else "▼", "樣本": len(j), "備註": note})

    if not rows:
        sys.exit("沒有可分析的持股")
    D = pd.DataFrame(rows)
    mv_tot, ex_tot = D.市值.sum(), D.曝險.sum()
    margin = mv_tot * margin_rate if use_margin else 0.0

    print("=" * 80)
    print(f"  設定檔 {cfg['_name']}   淨資產 {NW:,.0f}   "
          f"融資 {'可用' if cfg['margin_allowed'] else '不使用'}")
    print(f"  盤面（{mstate.date:%Y-%m-%d}）  狀態【{code}】  加權 {mstate.close:,.0f}  "
          f"距高點 {mstate.dd:.1%}  ATR% {mstate.atrp:.2f}")
    print(f"  有效槓桿上限 {LEV_CAP[code]} × 波動係數 {pos_coef:.2f} = 【{target_lev:.2f} 倍】"
          + ("   融資須為 0" if code in "CD" else ""))
    print("=" * 80)
    print(D.assign(市值=D.市值.map("{:,.0f}".format), 曝險=D.曝險.map("{:,.0f}".format)).to_string(index=False))

    print("\n  ── 曝險總覽 " + "─" * 60)
    print(f"  淨資產         {NW:>12,.0f}")
    print(f"  持股總市值     {mv_tot:>12,.0f}   （佔淨資產 {mv_tot/NW:.1%}）")
    if margin:
        print(f"  融資金額       {margin:>12,.0f}   自備款 {mv_tot-margin:>10,.0f}")
    print(f"  風險加權曝險   {ex_tot:>12,.0f}")
    ok = ex_tot / NW <= target_lev
    print(f"  有效槓桿       {ex_tot/NW:>12.2f} 倍   上限 {target_lev:.2f} 倍   {'✅ 合規' if ok else '❌ 超標'}")
    if not ok:
        over = ex_tot - NW * target_lev
        print(f"  ▶ 需降低曝險 {over:,.0f} 元（約當市值 {over/(ex_tot/mv_tot):,.0f} 元）")

    # ── §4 資產結構 ──
    print("\n  ── 資產結構（§4：依風險係數分類，不依名稱）" + "─" * 34)
    stable_mv = D[D.類別 == "穩健"].市值.sum()
    aggr_mv = D[D.類別 == "積極"].市值.sum()
    tgt, tol = mix["stable_target"], mix["tolerance"]
    actual = stable_mv / mv_tot if mv_tot else 0.0
    print(f"    穩健（風險係數 ≤ {mix['stable_rc_max']} 且現股）  {stable_mv:>11,.0f}   {actual:>6.1%}"
          f"   目標 {tgt:.0%}")
    print(f"    積極（其餘）                        {aggr_mv:>11,.0f}   {1-actual:>6.1%}"
          f"   目標 {1-tgt:.0%}")
    dev = actual - tgt
    if abs(dev) <= tol:
        print(f"    ▶ 偏離 {dev:+.1%}，在容許範圍（±{tol:.0%}）內 ✅")
    else:
        need = (tgt * mv_tot - stable_mv)
        print(f"    ▶ 偏離 {dev:+.1%}，超出容許範圍（±{tol:.0%}）❌")
        print(f"      需將約 {abs(need):,.0f} 元從{'積極轉到穩健' if need > 0 else '穩健轉到積極'}"
              f"（併入既有進出場動作即可，勿為此單獨交易）")

    if margin:
        print("\n  ── 融資追繳線（維持率 130%）" + "─" * 46)
        for sid, (g, r, rc, cost) in detail.items():
            call = r.adj_close * 1.30 * margin_rate
            print(f"    {sid:8s} 現價 {r.adj_close:7.2f}   追繳 {call:7.2f}（-{1-call/r.adj_close:.0%}）"
                  f"   ATR {r.atr20:.2f}/日 → 約 {(r.adj_close-call)/r.atr20:.1f} 個 ATR")

    print("\n  ── 逐檔停損與動作 " + "─" * 54)
    for sid, (g, r, rc, cost) in detail.items():
        stop = max(r.adj_close - 2 * r.atr20, g.adj_close.tail(10).min() * 0.99)
        risk_sh = r.adj_close - stop
        max_lots = NW * MAX_RISK_PCT / risk_sh / 1000 if risk_sh > 0 else 0
        lots = float([s for s in a.hold if s.startswith(sid + ":")][0].split(":")[1])
        broke = r.adj_close < r.sma20
        print(f"\n    ◆ {sid} {g.name.iloc[-1]}   {lots:g} 張 @ {r.adj_close:.2f}"
              + (f"   成本 {cost:.2f}（損益 {r.adj_close/cost-1:+.1%}）" if cost else ""))
        s120 = f"  SMA120 {r.sma120:.2f} {'▲' if r.adj_close>r.sma120 else '▼'}" if not np.isnan(r.sma120) else "  SMA120 樣本不足"
        print(f"        SMA20 {r.sma20:.2f} {'▲' if r.adj_close>r.sma20 else '▼'}   "
              f"SMA60 {r.sma60:.2f} {'▲' if r.adj_close>r.sma60 else '▼'}   "
              f"EMA21 {r.ema21:.2f} {'▲' if r.adj_close>r.ema21 else '▼'}{s120}")
        print(f"        停損 {stop:.2f}（-{1-stop/r.adj_close:.1%}）   "
              f"風險{MAX_RISK_PCT:.1%}反推上限 {max_lots:.0f} 張，現有 {lots:g} 張 "
              f"{'❌ 超過' if lots > max_lots else '✅'}")
        print(f"        ▶ {'跌破 SMA20 → 依 §5 全部出清' if broke else '仍在 SMA20 之上 → 續抱，停損上移'}")
    print()


if __name__ == "__main__":
    main()
