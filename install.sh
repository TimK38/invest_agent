#!/usr/bin/env bash
# 安裝 / 更新本專案（每台機器各跑一次）
#   1. 建立 Python 虛擬環境並安裝套件
#   2. 從範本產生 profiles/<你的名字>.json
#   3. 把 trade-check skill 安裝到 ~/.claude/skills/（自動填入本機路徑）
#
# 用法:  ./install.sh [你的名字]

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="${1:-}"
VENV="$ROOT/envest_agent"
SKILL_DIR="$HOME/.claude/skills/trade-check"

echo "專案路徑：$ROOT"

# ---- 1. 虛擬環境 ----
if [ ! -x "$VENV/bin/python" ]; then
  echo "→ 建立虛擬環境…"
  python3 -m venv "$VENV"
fi
echo "→ 安裝套件…"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet requests pandas numpy pypdf
"$VENV/bin/python" -c "import requests,pandas,numpy;print('   套件 OK:', pandas.__version__)"

# ---- 2. 個人設定 ----
mkdir -p "$ROOT/profiles"
if [ -z "$NAME" ]; then
  read -r -p "→ 你的名字（設定檔會存成 profiles/<名字>.json）： " NAME
fi
PROFILE="$ROOT/profiles/$NAME.json"
if [ -f "$PROFILE" ]; then
  echo "→ 已存在 $PROFILE，保留不覆寫"
else
  cp "$ROOT/profiles/example.json" "$PROFILE"
  echo "→ 已建立 $PROFILE"
  echo "   ⚠ 請編輯它，至少填入 net_worth（淨資產），否則腳本會拒絕執行"
fi

# ---- 3. 安裝 skill ----
mkdir -p "$SKILL_DIR"
sed "s|__PROJECT_ROOT__|$ROOT|g" "$ROOT/skill/SKILL.md.template" > "$SKILL_DIR/SKILL.md"
echo "→ skill 已安裝到 $SKILL_DIR/SKILL.md"

# ---- 4. 資料檢查 ----
if [ ! -f "$ROOT/data/taiex_daily.csv" ]; then
  echo "→ 沒有大盤資料，開始建檔（約 3 分鐘）…"
  "$VENV/bin/python" "$ROOT/fetch/fetch_twse.py" --full
fi

echo
echo "完成。試跑一次："
echo "  cd \"$ROOT\" && ./envest_agent/bin/python market_state.py --profile $NAME"
echo
echo "之後在 Claude Code 直接說「幫我看持股」「我想買 X」即可觸發檢查。"
