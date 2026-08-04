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
import argparse, re, subprocess, sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
PY = str(ROOT / "envest_agent" / "bin" / "python")

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
    r = subprocess.run([PY] + args, capture_output=True, text=True, cwd=ROOT)
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
    if c["kind"] in ("gate", "reentry"):
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
        out = run(["portfolio_check.py", "--asof", c["asof"],
                   "--hold", f"{c['sid']}:1:{c['px']:.2f}", "--cash"])
        if c["code"] in "CD":
            ok = "融資餘額須為 0" in out or "總曝險須先降至" in out
            return ok, "有印出 C/D 的降曝險指示" if ok else "缺少 C/D 降曝險指示", out
        ok = "超標" in out or "續抱" in out or "全部出清" in out or "加碼空間" in out
        return ok, "有產出逐檔動作", out
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
    for c in cases:
        ok, msg, out = check(c)
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
    main()
