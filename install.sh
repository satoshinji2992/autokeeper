#!/usr/bin/env bash
# AutoKeeper 一键安装（Linux、macOS、WSL）：
#   curl -fsSL https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/install.sh | bash
# 没有 uv 就先装 uv，再用 uv tool 装好 autokeeper 命令；再运行一次就是更新。
# 已经有 uv 的话，这个脚本做的就是：uv tool install autokeeper，然后 autokeeper setup。
# 可选环境变量：
#   AUTOKEEPER_VERSION  装哪个版本，如 0.0.3（默认最新）
#   AUTOKEEPER_EXTRAS   可选组件，如 graphrag（模组原文检索，约 1 GB）
#   AUTOKEEPER_FROM     auto（默认；发行版优先）、pypi、github（发行包）或 git
#   AUTOKEEPER_REF      从 GitHub 装时用哪个分支或标签（默认 main）
#   AUTOKEEPER_SHA256   从 GitHub 发行包装时，可指定发行页公布的 SHA256
#   AUTOKEEPER_NO_MODIFY_PATH=1  不改 shell 配置文件里的 PATH
set -euo pipefail

REPO="${AUTOKEEPER_REPO:-https://github.com/satoshinji2992/autokeeper}"
REF="${AUTOKEEPER_REF:-}"
FROM="${AUTOKEEPER_FROM:-auto}"
VERSION="${AUTOKEEPER_VERSION:-}"
EXTRAS="${AUTOKEEPER_EXTRAS:-}"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

if ! command -v uv >/dev/null; then
  say "安装 uv（Python 环境管理）…"
  command -v curl >/dev/null || die "需要 curl 来下载 uv：Debian/Ubuntu 用 sudo apt install curl"
  curl -LsSf https://astral.sh/uv/install.sh | sh || die "uv 没装好：上面是它的报错。检查网络后再运行一次。"
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null || die "uv 装好了但找不到，开一个新终端再运行一次"
fi

if ! command -v node >/dev/null || ! command -v npm >/dev/null; then
  warn "没找到 Node.js：伤害、理智这些骰子要用它。装好 Node.js 18 或更新的版本（https://nodejs.org，或 sudo apt install nodejs npm）后运行 autokeeper setup。"
fi

# setup checks the renderer after Python dependencies (including chafa.py) are installed.

name="autokeeper${EXTRAS:+[$EXTRAS]}"
case "$FROM" in auto|pypi|github|git) ;; *) die "AUTOKEEPER_FROM 只能是 auto、pypi、github 或 git。" ;; esac
if [ -n "$VERSION" ]; then
  VERSION="${VERSION#v}"
  [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.a-zA-Z0-9+-]*)$ ]] || die "AUTOKEEPER_VERSION 格式无效。"
fi
if [ "$FROM" = auto ] && [ -n "$REF" ]; then FROM=git; fi
if [ "$FROM" = auto ] || [ "$FROM" = github ]; then
  if [ -z "$VERSION" ]; then
    latest="$(curl -fsSL -o /dev/null -w '%{url_effective}' "$REPO/releases/latest")" \
      || die "无法确认最新发行版。检查网络，或用 AUTOKEEPER_VERSION 指定版本。"
    tag="${latest##*/}"
    VERSION="${tag#v}"
    [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.a-zA-Z0-9+-]*)$ ]] || die "未取得有效的发行版标签。"
  else
    tag="v$VERSION"
  fi
  if [ "$FROM" = auto ] && curl -fsS -o /dev/null "https://pypi.org/pypi/autokeeper/$VERSION/json" 2>/dev/null; then
    FROM=pypi
  else
    FROM=github
  fi
fi
if [ "$FROM" = git ]; then
  if [ -n "$VERSION" ]; then
    [ -z "$REF" ] || [ "$REF" = "$VERSION" ] || [ "$REF" = "v$VERSION" ] \
      || die "AUTOKEEPER_VERSION 与 AUTOKEEPER_REF 不一致；请只指定一个版本。"
    REF="${REF:-v$VERSION}"
  else
    REF="${REF:-main}"
  fi
  command -v git >/dev/null || die "从 GitHub 装需要 git：Debian/Ubuntu 用 sudo apt install git，macOS 用 xcode-select --install"
  spec="$name @ git+$REPO@$REF"
elif [ "$FROM" = github ]; then
  spec="$name @ $REPO/releases/download/$tag/autokeeper-$VERSION-py3-none-any.whl"
  if [ -n "${AUTOKEEPER_SHA256:-}" ]; then
    [[ "$AUTOKEEPER_SHA256" =~ ^[0-9a-fA-F]{64}$ ]] || die "AUTOKEEPER_SHA256 必须是发行包的 SHA256。"
    spec="$spec#sha256=$AUTOKEEPER_SHA256"
  fi
else
  spec="$name${VERSION:+==$VERSION}"
fi

say "安装 autokeeper 命令（第一次要下载依赖，几分钟）…"
uv tool install --force --refresh-package autokeeper --python 3.12 "$spec" \
  || die "autokeeper 没装好：上面是 uv 的报错。检查网络后再运行一次。"
BIN="$(uv tool dir --bin)"

say "初始化…"
if { : </dev/tty; } 2>/dev/null; then  # a terminal to ask in; curl | bash gives the script stdin
  "$BIN/autokeeper" setup </dev/tty || die "初始化没完成：上面是报错。解决后运行 autokeeper setup。"
else
  "$BIN/autokeeper" setup --no-login || die "初始化没完成：上面是报错。解决后运行 autokeeper setup。"
fi

case ":$PATH:" in
  *":$BIN:"*) say "装好了。在任意目录输入 autokeeper 开始。" ;;
  *)
    if [ -z "${AUTOKEEPER_NO_MODIFY_PATH:-}" ] && ! uv tool update-shell >/dev/null 2>&1; then
      warn "没能把 $BIN 加进 PATH，自己在 shell 配置文件里加一行：export PATH=\"$BIN:\$PATH\""
    fi
    say "装好了。开一个新终端（或运行 export PATH=\"$BIN:\$PATH\"），在任意目录输入 autokeeper 开始。"
    ;;
esac
