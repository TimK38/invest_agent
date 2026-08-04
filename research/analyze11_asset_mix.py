"""§4 的「穩健 72% 現股」可不可以換成「融資:現股 = 6:4」？

使用者的提案：拿掉 §4 的資產結構約束，改用資金來源的比例（6 成融資、4 成現股）。
這兩者管的東西不同：
  §4  管「買什麼」——風險係數 ≤ 1.2 且現股者必須佔 72%
  6:4 管「錢從哪來」——市值的 6 成用融資買

先講一個必須先釐清的矛盾：
  · §4 的原始回測（analyze6）說「穩健 0%」報酬/回撤只有 0.88，遠差於穩健 75% 的 1.69
  · 但 analyze10（v1.9）**完全沒有 §4 約束**，靠單一標的上限分散到 4~5 檔，拿到 2.03
  兩者差別不在穩健比重，而在**積極部位的檔數**：analyze6 的積極腿只有 2 檔
  （融資 1.5 倍的友達+聯電），analyze10 有 10~12 檔且受單一標的 25% 上限強制分散。

所以真正該問的是：**在有單一標的上限的前提下，§4 的 72% 還有邊際貢獻嗎？**

三種配置在同一套 §3 曝險上限、§6 出場規則下比較（含融資追繳判定）：
  A 現行 §4      穩健(rc≤1.2)必須現股且佔 72%，積極 28% 可融資
  B 使用者的 6:4  不分穩健積極，市值 6 成融資 4 成現股
  C 無資金結構    只受 §3 曝險上限與單一標的上限管（= analyze10 的配置）
"""
import sys, pathlib
import numpy as np, pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import PRICES_ADJ, TAIEX_RAW

CAP = {"A": 2.7, "B": 1.8, "C": 0.9, "D": 0.0}      # v1.9 之後的個人化上限
SINGLE = {"A": 0.25, "B": 0.20, "C": 0.15, "D": 0.0}
MR, MAINT = 0.60, 1.30
STABLE_RC, STABLE_W = 1.2, 0.72
RC_REFRESH, MIN_OBS = 21, 60


def market():
    d = pd.read_csv(TAIEX_RAW, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    c, h, l = d.close, d.high, d.low
    for n in (20, 60, 120):
        d[f"sma{n}"] = c.rolling(n).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d["atrp"] = tr.rolling(20).mean() / c * 100
    d["ret"] = c.pct_change()
    d["dd"] = c / c.cummax() - 1
    return d.set_index("date")


def code_of(r):
    if r.close < r.sma120 or r.dd <= -0.12:
        return "D"
    if r.close < r.sma60 or (r.atrp > 2.5 and r.close < r.sma20) or r.dd <= -0.08:
        return "C"
    if r.close < r.sma20:
        return "B"
    return "A" if (r.sma20 > r.sma60 > r.sma120 and r.atrp <= 2.0) else "B"


def main():
    idx = market()
    st = pd.read_csv(PRICES_ADJ, parse_dates=["date"], dtype={"sid": str})
    full = [s for s, g in st.groupby("sid") if len(g) > 700]
    px = st[st.sid.isin(full)].pivot(index="date", columns="sid", values="adj_close").sort_index()
    ret, sma20 = px.pct_change(), px.rolling(20).mean()
    dates = px.index.intersection(idx.index)

    rc_hist, cur = {}, {s: 1.8 for s in full}
    for i, d in enumerate(dates):
        if i % RC_REFRESH == 0:
            for s in full:
                j = pd.concat([ret[s].loc[:d].rename("r"), idx.ret.loc[:d].rename("m")],
                              axis=1).dropna()
                if len(j) >= MIN_OBS:
                    dn = j[j.m < 0]
                    cur[s] = float(max(np.cov(dn.r, dn.m)[0, 1] / np.var(dn.m),
                                       j.r.std() / j.m.std()))
        rc_hist[d] = dict(cur)

    def run(mode):
        eq, curve, minmr, called, levs, stw = 1.0, [], 9.9, None, [], []
        for i in range(1, len(dates)):
            prev, d = dates[i - 1], dates[i]
            if called:
                curve.append(eq)
                continue
            m = idx.loc[prev]
            c_ = code_of(m)
            lev = CAP[c_] * float(np.clip(1.8 / m.atrp, 0.4, 1.0))
            rcs = rc_hist[prev]
            elig = [s for s in full if px[s].loc[prev] > sma20[s].loc[prev]
                    and not np.isnan(sma20[s].loc[prev])]
            wts = {}
            if elig and lev > 0:
                if mode == "A":      # §4：穩健/積極分開配，各自受單一標的上限
                    stb = [s for s in elig if rcs[s] <= STABLE_RC]
                    agg = [s for s in elig if rcs[s] > STABLE_RC]
                    for grp, share in ((stb, STABLE_W), (agg, 1 - STABLE_W)):
                        if not grp:
                            continue
                        per = min(SINGLE[c_], lev * share / len(grp))
                        for s in grp:
                            wts[s] = per / rcs[s]
                else:                # B / C：不分類，平均配
                    per = min(SINGLE[c_], lev / len(elig))
                    for s in elig:
                        wts[s] = per / rcs[s]
            mv_w = sum(wts.values())
            # 自備款約束：每 1 元市值需要多少自己的錢
            if mode == "A":
                sw = sum(v for s, v in wts.items() if rcs[s] <= STABLE_RC)
                need = (sw + (mv_w - sw) * (1 - MR)) / mv_w if mv_w else 1.0
            elif mode == "B":
                need = 0.6 * (1 - MR) + 0.4 * 1.0      # 6 成融資、4 成現股
            else:
                need = 1 - MR                           # 只受融資成數限制
            target_mv = min(mv_w * eq, eq / need) if mv_w else 0.0
            borrowed = max(0.0, target_mv - eq)
            cash = eq - (target_mv - borrowed)
            r_pos = 0.0
            if mv_w > 0:
                for s, w in wts.items():
                    v = ret[s].loc[d]
                    r_pos += (w / mv_w) * (0.0 if np.isnan(v) else v)
            new_mv = target_mv * (1 + r_pos)
            eq = cash + new_mv - borrowed
            if borrowed > 0 and eq > 0:
                mr = new_mv / borrowed
                minmr = min(minmr, mr)
                if mr < MAINT:
                    called = d
            if eq <= 0:
                eq, called = 0.0, d
            levs.append(sum(wts[s] * rcs[s] for s in wts))
            if mv_w:
                stw.append(sum(v for s, v in wts.items() if rcs[s] <= STABLE_RC) / mv_w)
            curve.append(eq)
        c = pd.Series(curve, index=dates[1:])
        yrs = (dates[-1] - dates[1]).days / 365.25
        mdd = (c / c.cummax() - 1).min()
        ann = c.iloc[-1] ** (1 / yrs) - 1
        return dict(年化=ann, 回撤=mdd, 比值=ann / abs(mdd), 曝險=np.mean(levs),
                    穩健比=np.mean(stw) if stw else 0.0, 維持率=minmr, 追繳=called)

    print("=" * 96)
    print("  §4 的「穩健 72% 現股」vs 使用者提案的「融資:現股 = 6:4」")
    print(f"  標的池 {len(full)} 檔　期間 {dates[0]:%Y-%m-%d} ~ {dates[-1]:%Y-%m-%d}"
          f"　槓桿上限 {CAP['A']}/{CAP['B']}/{CAP['C']}　單一標的 25/20/15%")
    last_rc = rc_hist[dates[-1]]
    stb = sorted([s for s in full if last_rc[s] <= STABLE_RC], key=lambda x: last_rc[x])
    print(f"  符合「穩健」定義（風險係數 ≤ {STABLE_RC}）的只有 {len(stb)}/{len(full)} 檔："
          f"{'、'.join(f'{s}({last_rc[s]:.2f})' for s in stb) or '無'}")
    print("=" * 96)
    print(f"  {'配置':32s}{'平均曝險':>9s}{'穩健比重':>9s}{'年化':>9s}"
          f"{'最大回撤':>10s}{'報酬/回撤':>10s}{'最低維持率':>11s}{'追繳':>7s}")
    out = {}
    for mode, nm in (("A", "A 現行 §4（穩健72%現股）"),
                     ("B", "B 使用者提案（融資:現股 6:4）"),
                     ("C", "C 無資金結構（只受 §3 上限）")):
        r = run(mode)
        out[mode] = r
        print(f"  {nm:32s}{r['曝險']:>9.2f}{r['穩健比']:>9.0%}{r['年化']:>9.1%}"
              f"{r['回撤']:>10.1%}{r['比值']:>10.2f}{r['維持率']:>11.0%}"
              f"{('❗' + r['追繳'].strftime('%y-%m-%d')) if r['追繳'] else '  無':>7s}")
    bh = (idx.close.loc[dates[-1]] / idx.close.loc[dates[0]]) ** (
        1 / ((dates[-1] - dates[0]).days / 365.25)) - 1
    bhdd = (idx.close / idx.close.cummax() - 1).loc[dates].min()
    print(f"  {'加權指數買進持有':32s}{'—':>9s}{'—':>9s}{bh:>9.1%}{bhdd:>10.1%}"
          f"{bh/abs(bhdd):>10.2f}")

    print(f"\n  ── 判讀 " + "─" * 84)
    a, b = out["A"], out["B"]
    print(f"  A vs B：年化 {a['年化']:.1%} → {b['年化']:.1%}"
          f"（{(b['年化']-a['年化'])*100:+.1f}pp）、"
          f"最大回撤 {a['回撤']:.1%} → {b['回撤']:.1%}"
          f"（{(b['回撤']-a['回撤'])*100:+.1f}pp）")
    print(f"  §4 的 72% 在這個樣本裡的邊際貢獻 = 報酬/回撤 {a['比值']:.2f} vs {b['比值']:.2f}")
    print()
    print("  ⚠ 三個限制，結論不可外推：")
    print("    1. 樣本是 +152% 的大多頭（§12 第 1、2 條）。")
    print("    2. 標的池只有 10 檔且高度集中在半導體/面板，"
          "「分散」的效果被低估、單一產業風險被低估。")
    print(f"    3. **A 組被測試設計嚴重低估**：符合穩健定義的只有 {len(stb)} 檔，"
          f"受單一標的上限壓制後")
    print(f"       實際穩健比重只做到 {a['穩健比']:.0%}（目標 72%），"
          f"平均曝險也只有 {a['曝險']:.2f}（B/C 為 {b['曝險']:.2f}）。")
    print("       A 輸的主因是**曝險被壓到只有一半**，不是穩健核心本身沒用——"
          "要公平比較，標的池得有足夠多的低風險係數標的。")
    print("    4. 模擬用收盤價、每日再平衡、無跳空與滑價。實際執行做不到這麼準。")
    print()


if __name__ == "__main__":
    main()
