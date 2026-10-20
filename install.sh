#!/usr/bin/env bash
# AutoKeeper 一键安装（Linux、macOS、WSL）：
#   curl -fsSL https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/install.sh | bash
# 再运行一次就是更新。可选环境变量：
#   AUTOKEEPER_DIR     装到哪里（默认 ~/.local/share/autokeeper，存档和模组也在这里）
#   AUTOKEEPER_REF     用哪个分支或版本（默认 main）
#   AUTOKEEPER_EXTRAS  可选组件（默认 claude,graphrag；graphrag 是模组知识库，约 1 GB）
set -euo pipefail

REPO="${AUTOKEEPER_REPO:-https://github.com/satoshinji2992/autokeeper}"
REF="${AUTOKEEPER_REF:-main}"
DIR="${AUTOKEEPER_DIR:-$HOME/.local/share/autokeeper}"
EXTRAS="${AUTOKEEPER_EXTRAS:-claude,graphrag}"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

command -v git >/dev/null || die "需要 git：Debian/Ubuntu 用 sudo apt install git，macOS 用 xcode-select --install"

if ! command -v uv >/dev/null; then
  say "安装 uv（Python 环境管理）…"
  command -v curl >/dev/null || die "需要 curl 来下载 uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null || die "uv 装好了但找不到，开一个新终端再运行一次"
fi

if ! command -v npm >/dev/null; then
  warn "没找到 Node.js：掷伤害和理智的骰子要用它。装好 Node.js 18+（https://nodejs.org，或 sudo apt install nodejs npm）后运行 autokeeper setup。"
fi

if [ -d "$DIR/.git" ]; then
  say "更新 $DIR …"
  git -C "$DIR" fetch --quiet --tags origin
  git -C "$DIR" checkout --quiet "$REF"
  git -C "$DIR" pull --quiet --ff-only origin "$REF" 2>/dev/null || true  # a tag has nothing to pull
else
  say "下载到 $DIR …"
  mkdir -p "$(dirname "$DIR")"
  git clone --quiet --branch "$REF" "$REPO" "$DIR"
fi

say "安装 autokeeper 命令（第一次要下载依赖，几分钟）…"
uv tool install --quiet --force --python 3.12 --editable "$DIR[$EXTRAS]"
BIN="$(uv tool dir --bin)"

say "初始化…"
if { : </dev/tty; } 2>/dev/null; then  # a terminal to ask in
  "$BIN/autokeeper" setup </dev/tty  # curl | bash gives the script stdin: the questions read the terminal
else
  "$BIN/autokeeper" setup --no-login
fi

case ":$PATH:" in
  *":$BIN:"*) say "装好了。在任意目录输入 autokeeper 开始。" ;;
  *)
    uv tool update-shell >/dev/null 2>&1 || true
    say "装好了。开一个新终端（或运行 export PATH=\"$BIN:\$PATH\"），在任意目录输入 autokeeper 开始。"
    ;;
esac
