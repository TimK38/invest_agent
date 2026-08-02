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
# venv 內含硬編碼的絕對路徑；資料夾一旦搬移或改名，pip 等 console script 的
# shebang 會全部失效（python 本身是 symlink 所以還能跑，容易誤判為正常）。
# 這裡比對 pyvenv.cfg 記錄的路徑，不符就整個重建。
if [ -f "$VENV/pyvenv.cfg" ] && ! grep -qF "$VENV" "$VENV/pyvenv.cfg"; then
  echo "→ 偵測到虛擬環境路徑與現況不符（資料夾曾搬移或改名），重建中…"
  rm -rf "$VENV"
fi
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
# 全域版：填入本機絕對路徑，在任何目錄下都能觸發
mkdir -p "$SKILL_DIR"
sed "s|__PROJECT_ROOT__|$ROOT|g" "$ROOT/skill/SKILL.md.template" > "$SKILL_DIR/SKILL.md"
echo "→ 全域 skill 已安裝到 $SKILL_DIR/SKILL.md"

# 專案版：用相對路徑，隨倉庫散布，clone 下來不跑 install.sh 也能用
mkdir -p "$ROOT/.claude/skills/trade-check"
sed "s|__PROJECT_ROOT__|.|g" "$ROOT/skill/SKILL.md.template" > "$ROOT/.claude/skills/trade-check/SKILL.md"
echo "→ 專案 skill 已同步到 .claude/skills/trade-check/SKILL.md"

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
