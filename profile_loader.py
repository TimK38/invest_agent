"""個人設定載入器 — 策略是共用的，參數是個人的

設定檔放在 profiles/<名字>.json，用以下順序決定要載入哪一份：
  1. 指令參數 --profile <名字>
  2. 環境變數 INVEST_PROFILE
  3. profiles/ 底下唯一的一份（若只有一份）

profiles/ 已列入 .gitignore，不會被推上共用倉庫。
"""
import json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFILES = ROOT / "profiles"

DEFAULTS = {
    "name": "unnamed",
    "net_worth": None,
    "margin_allowed": True,
    "margin_rate": 0.60,
    "max_risk_per_trade": 0.015,
    "leverage_caps": {"A": 1.5, "B": 1.0, "C": 0.5, "D": 0.0},
    "instruments": [],
    "notes": "",
}


def available():
    # <名字>_positions.json 是 positions.py 的持股紀錄，不是設定檔
    return sorted(p.stem for p in PROFILES.glob("*.json")
                  if p.stem != "example" and not p.stem.endswith("_positions"))


def load(name=None):
    PROFILES.mkdir(exist_ok=True)
    name = name or os.environ.get("INVEST_PROFILE")
    if not name:
        found = available()
        if len(found) == 1:
            name = found[0]
        elif not found:
            sys.exit(f"找不到任何設定檔。請複製 {PROFILES}/example.json 成 "
                     f"{PROFILES}/<你的名字>.json 並填入淨資產。")
        else:
            sys.exit(f"有多份設定檔 {found}，請用 --profile <名字> 或設定 INVEST_PROFILE 環境變數。")

    p = PROFILES / f"{name}.json"
    if not p.exists():
        sys.exit(f"找不到 {p}。現有的有：{available() or '（無）'}")

    cfg = {**DEFAULTS, **json.loads(p.read_text(encoding="utf-8"))}
    cfg["_name"] = name
    if not cfg.get("net_worth"):
        sys.exit(f"{p} 尚未填入 net_worth（淨資產）。")
    return cfg


def add_arg(ap):
    ap.add_argument("--profile", metavar="名字", help=f"要用哪份設定檔（現有：{', '.join(available()) or '無'}）")
