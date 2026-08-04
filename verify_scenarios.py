"""場景回歸驗證 —— 每次調整策略後都要跑這支

目的：確認「A 狀態的規則」不會在 B 狀態被誤觸發、兩條規則不會互相卡住，
以及每個使用情境（盤中問買、收盤前看持股、盤後 review、再上車…）都走到正確的分支。

**它跑的是真正的 CLI，不是重新實作一份判斷邏輯。** 重新實作等於自己跟自己對答案，
測不出工具本身的 bug——這正是要防的事。

用法:
  python verify_scenarios.py            # 全跑
  python verify_scenarios.py --list     # 只列場景清單，不執行
  python verify_scenarios.py -k 再上車   # 只跑名稱含關鍵字的場景
"""
import argparse, json, re, subprocess, sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
PY = str(ROOT / "envest_agent" / "bin" / "python")

# 驗證用的臨時設定檔：測試不可依賴使用者的真實持股，否則結果會隨他今天買了什麼而變。
VP = "_verify"
VPROF = ROOT / "profiles" / f"{VP}.json"
VPOS = ROOT / "profiles" / f"{VP}_positions.json"
VPROFILE = {"name": VP, "net_worth": 8_000_000, "margin_allowed": True,
            "margin_rate": 0.60, "max_risk_per_trade": 0.015,
            "leverage_caps": {"A": 2.7, "B": 1.8, "C": 0.9, "D": 0.0},
            "instruments": [], "notes": "verify_scenarios.py 專用，跑完會刪除",
            "asset_mix": {"stable_target": 0.72, "tolerance": 0.1, "stable_rc_max": 1.2}}


def write_positions(positions):
    VPROF.write_text(json.dumps(VPROFILE, ensure_ascii=False, indent=2), encoding="utf-8")
    VPOS.write_text(json.dumps({"positions": positions, "closed": []},
                               ensure_ascii=False, indent=2), encoding="utf-8")


def cleanup():
    for f in (VPROF, VPOS):
        f.unlink(missing_ok=True)

# ── §3 的狀態閘門期望值（改策略時，這張表就是規格） ────────────────────────
# 狀態 → (現股新開倉, 融資新開倉)
GATE = {
    "A":  ("allow", "allow"),   # 可新開倉、可加碼獲利部位
    "B2": ("allow", "block"),   # §3 例外：符合 §7 訊號可用現股建首波 1/3；融資仍禁止
    "B1": ("block", "block"),   # 只出不進（訊號要求站上 SMA20，與 B1 互斥）
    "C":  ("block", "block"),   # 只出不進，融資須歸零
    "D":  ("block", "block"),   # 全部現金
}


def run(args):
    r = subprocess.run([PY] + args + ["--profile", VP],
                       capture_output=True, text=True, cwd=ROOT)
    return r.stdout + r.stderr


def states():
    """重算每日狀態（含 B1/B2 之分），用來挑各狀態的代表日"""
    d = pd.read_csv(ROOT / "data/taiex_daily.csv", parse_dates=["date"]).sort_values("date")
    c, h, l = d.close, d.high, d.low
    for n in (20, 60, 120):
        d[f"sma{n}"] = c.rolling(n).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d["atrp"] = tr.rolling(20).mean() / c * 100
    d["dd"] = c / c.cummax() - 1
    d = d.dropna(subset=["sma120"]).reset_index(drop=True)

    def st(r):
        if r.close < r.sma120 or r.dd <= -0.12:
            return "D"
        if r.close < r.sma60 or (r.atrp > 2.5 and r.close < r.sma20) or r.dd <= -0.08:
            return "C"
        if r.close < r.sma20:
            return "B1"
        return "A" if (r.sma20 > r.sma60 > r.sma120 and r.atrp <= 2.0) else "B2"

    d["s"] = d.apply(st, axis=1)
    return d


def px_on(sid, day):
    p = pd.read_csv(ROOT / "data/prices_adj.csv", parse_dates=["date"], dtype={"sid": str})
    g = p[(p.sid == sid) & (p.date == pd.Timestamp(day))]
    return None if g.empty else float(g.adj_close.iloc[0])


def build_cases():
    d = states()
    cases = []
    # ── 場景 A~E：五個狀態 × 現股/融資 的閘門 ──
    # 每個狀態取「最近 2 天」，用極小部位（10 股）讓部位大小檢查全部過關，
    # 這樣唯一可能的 ❌ 就是狀態閘門本身 —— 才測得出閘門有沒有走錯分支。
    for code in ("A", "B2", "B1", "C", "D"):
        for day in d[d.s == code].tail(2).date:
            for sid, kind in (("2330", "個股"), ("0050", "ETF")):
                px = px_on(sid, day)
                if px is None:
                    continue
                for cash, want in zip((True, False), GATE[code]):
                    cases.append(dict(
                        name=f"{code} 狀態 {kind}{'現股' if cash else '融資'}新開倉",
                        kind="gate", asof=f"{day:%Y-%m-%d}", sid=sid, px=px,
                        cash=cash, want=want, code=code))
    # ── 場景 F：B2 再上車（由 C/D 轉入 B2 的那一天，§7 訊號應成立且現股放行） ──
    d["prev"] = d.s.shift()
    for day in d[(d.s == "B2") & d.prev.isin(["C", "D"])].tail(3).date:
        px = px_on("2330", day)
        if px:
            cases.append(dict(name=f"B2 再上車（前一日 C/D）現股應放行",
                              kind="reentry", asof=f"{day:%Y-%m-%d}", sid="2330",
                              px=px, cash=True, want="allow", code="B2"))
    # ── S6：加碼 ≠ 新開倉（§3 只允許 A 狀態加碼，且只能加在獲利部位）──
    a_day = d[d.s == "A"].tail(1).date.iloc[0]
    c_day = d[d.s == "C"].tail(1).date.iloc[0]
    for day, code, want in ((a_day, "A", "allow"), (c_day, "C", "block")):
        px = px_on("2330", day)
        if px:
            cases.append(dict(name=f"S6 {code} 狀態加碼獲利部位", kind="addon",
                              asof=f"{day:%Y-%m-%d}", sid="2330", px=px,
                              hold_cost=px * 0.7, want=want, code=code))
    # 加碼虧損部位 → 鐵則 1 必擋，不論狀態
    px = px_on("2330", a_day)
    if px:
        cases.append(dict(name="S6 A 狀態加碼**虧損**部位（鐵則1 應擋）", kind="addon_loss",
                          asof=f"{a_day:%Y-%m-%d}", sid="2330", px=px,
                          hold_cost=px * 1.3, want="block", code="A"))
    # ── S8：狀態當日降級 → 必須印出「今日或次一交易日完成」──
    d["prev"] = d.s.shift()
    down = d[(d.s == "D") & (d.prev == "C")].tail(1)
    if len(down):
        day = down.date.iloc[0]
        px = px_on("2330", day)
        if px:
            cases.append(dict(name="S8 狀態當日降級 C→D 應標示時限", kind="downgrade",
                              asof=f"{day:%Y-%m-%d}", sid="2330", px=px, code="D"))
    # ── S9：沒有新交易但被動超標 → 逐檔要標出單一標的超標並給減碼股數 ──
    px = px_on("2330", c_day)
    if px:
        cases.append(dict(name="S9 部位被動超標應標示減碼股數", kind="passive",
                          asof=f"{c_day:%Y-%m-%d}", sid="2330", px=px, code="C"))
    # ── S14：多檔同時觸發 → 今日必做要依嚴重度排序 ──
    px2, px3 = px_on("2330", c_day), px_on("0050", c_day)
    if px2 and px3:
        cases.append(dict(name="S14 多檔同時觸發應有優先序清單", kind="todo",
                          asof=f"{c_day:%Y-%m-%d}", px=px2, px2=px3, code="C"))
    # ── S17：空手時應回答「現在可以進場嗎」，而不是報錯 ──
    for day, code in ((c_day, "C"), (a_day, "A")):
        cases.append(dict(name=f"S17 {code} 狀態空手應顯示進場條件", kind="flat",
                          asof=f"{day:%Y-%m-%d}", code=code))

    # ── 場景 G：持股檢視在各狀態的出場動作 ──
    for code, want_act in (("A", "續抱"), ("C", "全部出清"), ("D", "全部出清")):
        for day in d[d.s == code].tail(1).date:
            px = px_on("2330", day)
            if px:
                cases.append(dict(name=f"{code} 狀態持股檢視：大盤層級指示",
                                  kind="hold", asof=f"{day:%Y-%m-%d}", sid="2330",
                                  px=px, code=code, want=want_act))
    return cases


def check(c):
    if c["kind"] in ("addon", "addon_loss"):
        write_positions([{"sid": c["sid"], "lots": 1, "cost": round(c["hold_cost"], 2),
                          "stop": round(c["hold_cost"] * 0.9, 2), "margin": False,
                          "horizon": "波段", "opened": "2024-01-02"}])
        out = run(["buy_check.py", c["sid"], "0.01", "--asof", c["asof"],
                   "--price", f"{c['px']:.2f}", "--cash"])
        if "【加碼】" not in out:
            return False, "沒有辨識成加碼（仍當作新開倉）", out
        m = re.search(r"§3 加碼只允許在 A 狀態.*", out)
        if c["kind"] == "addon_loss":
            ok = "**攤平**" in out and "鐵則 1" in out
            return ok, "鐵則 1 擋下攤平" if ok else "**未擋下攤平**", out
        got = "allow" if (m and m.group(0) in "\n".join(
            l for l in out.splitlines() if l.strip().startswith("✅"))) else "block"
        ok = got == c["want"]
        return ok, f"加碼閘門 {got}（預期 {c['want']}）", out
    if c["kind"] == "downgrade":
        write_positions([])
        out = run(["portfolio_check.py", "--asof", c["asof"],
                   "--hold", f"{c['sid']}:1:{c['px']:.2f}", "--cash"])
        ok = "今日降級" in out and "次一交易日" in out
        return ok, "有標示降級時限" if ok else "**未標示降級時限**", out
    if c["kind"] == "passive":
        write_positions([])
        out = run(["portfolio_check.py", "--asof", c["asof"],
                   "--hold", f"{c['sid']}:3:{c['px']:.2f}", "--cash"])
        ok = "單一標的曝險" in out and "超標" in out and "股" in out
        return ok, "有標示單一標的超標與減碼股數" if ok else "**未檢查單一標的上限**", out
    if c["kind"] == "todo":
        write_positions([])
        out = run(["portfolio_check.py", "--asof", c["asof"], "--cash", "--hold",
                   f"2330:3:{c['px']:.2f}", f"0050:30:{c['px2']:.2f}"])
        if "今日必做" not in out:
            return False, "**沒有今日必做清單**", out
        block = out.split("今日必做")[1]
        nums = re.findall(r"^\s+(\d+)\. ", block, re.M)
        ok = len(nums) >= 2 and nums == sorted(nums, key=int)
        return ok, f"今日必做 {len(nums)} 項且已排序" if ok else "清單未排序或項目不足", out
    if c["kind"] == "flat":
        write_positions([])
        out = run(["portfolio_check.py", "--asof", c["asof"]])
        ok = "目前空手" in out and "現在可以進場嗎" in out
        return ok, "有回答進場條件" if ok else "**空手時只報錯，沒回答進場條件**", out
    if c["kind"] in ("gate", "reentry"):
        write_positions([])
        out = run(["buy_check.py", c["sid"], "0.01", "--asof", c["asof"],
                   "--price", f"{c['px']:.2f}"] + (["--cash"] if c["cash"] else []))
        m = re.search(r"狀態閘門：(.+)", out)
        if not m:
            return False, "輸出沒有狀態閘門那一行", out
        line = m.group(1).strip()
        got = "allow" if "允許" in line else "block"
        # 順便確認工具認到的狀態與我們算的一致（B1/B2 在工具裡都印 B）
        mc = re.search(r"狀態【([ABCD])】", out)
        tool_code = mc.group(1) if mc else "?"
        if tool_code != c["code"][0]:
            return False, f"狀態不符：工具判 {tool_code}、預期 {c['code']}", out
        ok = got == c["want"]
        return ok, f"閘門 {got}（預期 {c['want']}）：{line[:60]}", out
    if c["kind"] == "hold":
        write_positions([])
        out = run(["portfolio_check.py", "--asof", c["asof"],
                   "--hold", f"{c['sid']}:1:{c['px']:.2f}", "--cash"])
        if "今日必做" not in out:
            return False, "**沒有今日必做清單**", out
        block = out.split("今日必做")[1]
        if c["code"] in "CD":
            # C/D 的降曝險指示必須出現在【今日必做】的第 1 項，而且要講出上限倍數
            ok = bool(re.search(rf"1\. .*大盤 {c['code']} 狀態.*總曝險降至 [\d.]+ 倍", block))
            return ok, "今日必做第 1 項為大盤層級降曝險" if ok else \
                "**C/D 降曝險未列在今日必做第 1 項**", out
        # 逐檔的 ▶ 動作必須是 §6/§3 定義過的其中一種，不能是空白或未知字串
        ACTS = ("續抱", "全部出清", "出 1/2", "賣 30%", "單一標的曝險超標")
        # 逐檔動作縮排 8 格；曝險總覽的「▶ 加碼空間」只縮 2 格，不可混入
        arrows = re.findall(r"^ {8}▶ (.+)", out, re.M)
        bad = [x for x in arrows if not any(k in x for k in ACTS)]
        ok = bool(arrows) and not bad
        return ok, (f"逐檔動作合法（{len(arrows)} 檔）" if ok else
                    f"**出現未定義的動作**：{bad[:1]}" if bad else "**沒有任何逐檔動作**"), out
    return False, "未知場景類型", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只列場景，不執行")
    ap.add_argument("-k", metavar="關鍵字", help="只跑名稱含關鍵字的場景")
    ap.add_argument("-v", action="store_true", help="失敗時印出完整輸出")
    a = ap.parse_args()

    cases = build_cases()
    if a.k:
        cases = [c for c in cases if a.k in c["name"]]
    if a.list:
        for c in cases:
            print(f"  [{c['kind']:7s}] {c['asof']}  {c['name']}")
        print(f"\n  共 {len(cases)} 個場景")
        return

    print("=" * 84)
    print("  場景回歸驗證　（跑真正的 CLI，不是重新實作判斷邏輯）")
    print("=" * 84)
    fails = []
    write_positions([])
    for c in cases:
        try:
            ok, msg, out = check(c)
        except Exception as e:
            ok, msg, out = False, f"{type(e).__name__}: {e}", ""
        print(f"  {'✅' if ok else '❌'} {c['asof']}  {c['name']:32s} {msg}")
        if not ok:
            fails.append((c, msg, out))
    print("\n" + "=" * 84)
    print(f"  {len(cases) - len(fails)}/{len(cases)} 通過")
    if fails:
        print(f"\n  ❌ {len(fails)} 個場景失敗：")
        for c, msg, out in fails:
            print(f"\n    {c['asof']} {c['name']}\n      {msg}")
            if a.v:
                print("      " + "\n      ".join(out.splitlines()[:30]))
        sys.exit(1)
    print("  策略在五個狀態下的閘門行為，以及再上車情境，全部符合 §3 的規格。")


if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup()      # 臨時設定檔一定要刪掉，否則使用者下次跑會遇到「有多份設定檔」
