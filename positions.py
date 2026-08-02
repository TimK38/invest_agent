"""持股紀錄 — 單一事實來源，讓每天不必重打成本與停損

存放於 profiles/<名字>_positions.json（已在 .gitignore 內，不會外流）。
紀錄的是「當初的決定」：成本、**當初設定的停損價**、是否融資、進場日、交易週期。
這些東西事後回想一定失真，所以進場當下就要寫下來。

用法:
  python positions.py                                    # 列出目前持股
  python positions.py --add 00981A:48:26.13:25.60        # 新增/覆寫（SID:張數:成本:停損）
  python positions.py --add 2330:5:1180:1120:cash        # 末欄 cash = 現股，預設依 profile
  python positions.py --add 2330:5:1180:1120 --horizon 波段 --opened 2026-07-31
  python positions.py --close 00981A:25.27:停損          # 出場（SID:出場價[:原因]）
  python positions.py --reconcile 00981A:48:26.13 009816:85:14.65   # 與紀錄比對
  python positions.py --reconcile ... --apply            # 比對後直接更新張數與成本

出場原因用「停損」時會自動計數，同一檔 30 天內累計 2 次 → 依 STRATEGY.md §7
該檔禁止再交易一個月，列表與買進檢查都會擋。
"""
import argparse, json, sys
from datetime import date, datetime, timedelta
from pathlib import Path

import profile_loader

ROOT = Path(__file__).resolve().parent
BAN_DAYS = 30          # §7：同一檔連續停損 2 次 → 禁止交易的天數


def store_path(cfg):
    return ROOT / "profiles" / f"{cfg['_name']}_positions.json"


def load(cfg):
    p = store_path(cfg)
    if not p.exists():
        return {"positions": [], "closed": []}
    d = json.loads(p.read_text(encoding="utf-8"))
    d.setdefault("positions", [])
    d.setdefault("closed", [])
    return d


def save(cfg, d, today=None):
    d["updated"] = (today or date.today()).isoformat()
    store_path(cfg).write_text(
        json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def to_hold_args(d):
    """轉成 portfolio_check.py 的 --hold 格式"""
    out = []
    for p in d["positions"]:
        out.append(f"{p['sid']}:{p['lots']:g}:{p['cost']}:{p['stop']}"
                   if p.get("stop") else f"{p['sid']}:{p['lots']:g}:{p['cost']}")
    return out


def banned(d, sid, today=None):
    """§7：同一檔 BAN_DAYS 內停損 2 次 → 回傳解禁日，否則 None"""
    today = today or date.today()
    hits = sorted(c["date"] for c in d["closed"]
                  if c["sid"] == sid and c.get("reason") == "停損"
                  and (today - date.fromisoformat(c["date"])).days <= BAN_DAYS)
    if len(hits) >= 2:
        return date.fromisoformat(hits[-1]) + timedelta(days=BAN_DAYS)
    return None


def parse_spec(spec, cfg):
    """SID:張數:成本:停損[:cash|margin]"""
    f = spec.split(":")
    if len(f) < 3:
        sys.exit(f"格式錯誤 '{spec}'，至少要 SID:張數:成本")
    rec = {"sid": f[0], "lots": float(f[1]), "cost": float(f[2]),
           "stop": float(f[3]) if len(f) > 3 and f[3] else None,
           "margin": cfg["margin_allowed"]}
    if len(f) > 4 and f[4]:
        rec["margin"] = f[4].lower() not in ("cash", "現股")
    return rec


def fmt(p):
    tag = "融資" if p.get("margin") else "現股"
    stop = f"{p['stop']:.2f}" if p.get("stop") else "⚠ 未設"
    return (f"  {p['sid']:8s} {p['lots']:>6g} 張  成本 {p['cost']:>9.2f}  停損 {stop:>7s}  "
            f"{tag}  {p.get('horizon') or '—'}  進場 {p.get('opened') or '—'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", nargs="+", metavar="SID:張數:成本:停損[:cash]")
    ap.add_argument("--close", nargs="+", metavar="SID:出場價[:原因]")
    ap.add_argument("--reconcile", nargs="+", metavar="SID:張數[:成本]")
    ap.add_argument("--apply", action="store_true", help="--reconcile 後直接寫入差異")
    ap.add_argument("--horizon", help="交易週期：短線/波段/長線（配合 --add）")
    ap.add_argument("--opened", help="進場日 YYYY-MM-DD（配合 --add，預設今天）")
    ap.add_argument("--today", help="覆寫「今天」，用於重現某日判斷 YYYY-MM-DD")
    ap.add_argument("--hold-args", action="store_true",
                    help="只印出 portfolio_check.py 的 --hold 參數")
    profile_loader.add_arg(ap)
    a = ap.parse_args()

    cfg = profile_loader.load(a.profile)
    d = load(cfg)
    today = date.fromisoformat(a.today) if a.today else date.today()
    by_sid = {p["sid"]: p for p in d["positions"]}

    if a.add:
        for spec in a.add:
            rec = parse_spec(spec, cfg)
            old = by_sid.get(rec["sid"])
            rec["horizon"] = a.horizon or (old or {}).get("horizon")
            rec["opened"] = a.opened or (old or {}).get("opened") or today.isoformat()
            if old:
                d["positions"][d["positions"].index(old)] = rec
                print(f"  更新 {rec['sid']}：{old['lots']:g} 張 @ {old['cost']:.2f} "
                      f"→ {rec['lots']:g} 張 @ {rec['cost']:.2f}")
            else:
                d["positions"].append(rec)
                print(f"  新增 {rec['sid']} {rec['lots']:g} 張 @ {rec['cost']:.2f}")
            if not rec["stop"]:
                print(f"    ⚠ {rec['sid']} 沒有停損價。STRATEGY.md §6：沒有預設停損價的單，不下。")
        save(cfg, d, today)

    if a.close:
        for spec in a.close:
            f = spec.split(":")
            sid, px = f[0], float(f[1]) if len(f) > 1 and f[1] else None
            reason = f[2] if len(f) > 2 and f[2] else "停損"
            p = by_sid.get(sid)
            if not p:
                print(f"  ⚠ 紀錄中沒有 {sid}，略過")
                continue
            pnl = (px / p["cost"] - 1) if (px and p.get("cost")) else None
            d["positions"].remove(p)
            d["closed"].append({**p, "exit": px, "reason": reason,
                                "date": today.isoformat(),
                                "pnl": round(pnl, 4) if pnl is not None else None})
            print(f"  出場 {sid} {p['lots']:g} 張 @ {px}　原因 {reason}"
                  + (f"　損益 {pnl:+.1%}" if pnl is not None else ""))
            if b := banned(d, sid, today):
                print(f"    ❗ {sid} 於 {BAN_DAYS} 天內第 2 次停損 → 依 §7 禁止交易至 {b}")
        save(cfg, d, today)

    if a.reconcile:
        print("  ── 你回報的 vs 紀錄 " + "─" * 46)
        seen, diffs = set(), []
        for spec in a.reconcile:
            f = spec.split(":")
            sid, lots = f[0], float(f[1])
            cost = float(f[2]) if len(f) > 2 and f[2] else None
            seen.add(sid)
            p = by_sid.get(sid)
            if not p:
                print(f"    ＋ {sid} {lots:g} 張　紀錄中沒有這檔 → **需要補當初的停損價**")
                diffs.append((sid, None, {"sid": sid, "lots": lots, "cost": cost,
                                          "stop": None, "margin": cfg["margin_allowed"],
                                          "opened": today.isoformat(), "horizon": None}))
                continue
            ch = []
            if abs(p["lots"] - lots) > 1e-9:
                ch.append(f"張數 {p['lots']:g} → {lots:g}")
            if cost and abs(p["cost"] - cost) > 1e-9:
                ch.append(f"成本 {p['cost']:.2f} → {cost:.2f}")
            if ch:
                print(f"    ≠ {sid}　{'、'.join(ch)}　（停損維持 "
                      f"{p['stop'] if p.get('stop') else '未設'}）")
                diffs.append((sid, p, {**p, "lots": lots, "cost": cost or p["cost"]}))
            else:
                print(f"    ✓ {sid} {lots:g} 張 @ {p['cost']:.2f}　停損 "
                      f"{p['stop'] if p.get('stop') else '⚠ 未設'}")
        for p in d["positions"]:
            if p["sid"] not in seen:
                print(f"    － {p['sid']} {p['lots']:g} 張　紀錄中有但你沒提到 → 已經賣掉了嗎？"
                      f"（用 --close {p['sid']}:出場價:原因）")
        if diffs and a.apply:
            for sid, old, new in diffs:
                if old:
                    d["positions"][d["positions"].index(old)] = new
                else:
                    d["positions"].append(new)
            save(cfg, d, today)
            print(f"\n  已更新 {len(diffs)} 筆。缺停損價的請用 --add 補上。")
        elif diffs:
            print("\n  以上僅為比對。要寫入請加 --apply")

    d = load(cfg)
    if a.hold_args:
        print(" ".join(to_hold_args(d)))
        return

    if not (a.add or a.close or a.reconcile):
        print(f"  ── 持股紀錄　{store_path(cfg).name}　更新於 {d.get('updated', '—')} " + "─" * 20)
        if not d["positions"]:
            print("    （空）用 --add SID:張數:成本:停損 建立")
        for p in sorted(d["positions"], key=lambda x: x["sid"]):
            print(fmt(p))
        no_stop = [p["sid"] for p in d["positions"] if not p.get("stop")]
        if no_stop:
            print(f"\n    ⚠ 未設停損：{'、'.join(no_stop)} —— §6 鐵則：沒有停損價的單，不下")
        bans = [(p, b) for p in {c["sid"] for c in d["closed"]}
                if (b := banned(d, p, today))]
        if bans:
            print("\n    ❗ §7 禁止交易中（30 天內停損 2 次）：")
            for sid, until in bans:
                print(f"       {sid} 至 {until}")
        if d["closed"]:
            print(f"\n  ── 最近出場 " + "─" * 54)
            for c in sorted(d["closed"], key=lambda x: x["date"])[-5:]:
                pnl = f"{c['pnl']:+.1%}" if c.get("pnl") is not None else "—"
                print(f"    {c['date']}  {c['sid']:8s} {c['lots']:>5g} 張  "
                      f"@ {c.get('exit') or '—'}　{c.get('reason') or '—'}　損益 {pnl}")
        print(f"\n  帶入每日檢查：./envest_agent/bin/python portfolio_check.py"
              + (f" --profile {cfg['_name']}" if a.profile else ""))


if __name__ == "__main__":
    main()
