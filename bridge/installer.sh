#!/bin/bash
# bridge/installer.sh — 一次性把 infill bridge 装到 mini 的 launchd 上
#
# 用法（mini 上首次安装）：
#     git clone https://github.com/kyoicn/infill.git
#     cd infill/bridge && ./installer.sh init
#
# 之后所有更新由 GitHub Actions deploy job 自动接管（拉 release asset 的
# bridge.pyz 覆盖到 ~/.infill-bridge/bin/ 然后 launchctl kickstart）。
#
# init 子命令做的事：
#   1. 建 ~/.infill-bridge/{bin,log}
#   2. 从最新 release 下载 bridge.pyz 到 bin/
#   3. 渲染 plist 模板（替 @@INSTALL_DIR@@ / @@HOME@@）
#   4. launchctl bootstrap 加载服务、立刻拉起
#
# 二次跑 init 安全 —— bootstrap 会失败但被吞掉，bridge.pyz 会被新版本覆盖。

set -euo pipefail

REPO="${INFILL_REPO:-kyoicn/infill}"
INSTALL_DIR="$HOME/.infill-bridge"
PLIST_NAME="com.infill.bridge.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"
SERVICE_TARGET="gui/$(id -u)/com.infill.bridge"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_SRC="$SCRIPT_DIR/launchd/$PLIST_NAME.template"

cmd="${1:-init}"

case "$cmd" in
  init)
    echo "==> installing infill bridge to $INSTALL_DIR"
    mkdir -p "$INSTALL_DIR/bin" "$INSTALL_DIR/log"

    echo "==> downloading bridge.pyz from latest $REPO release"
    if ! command -v gh >/dev/null 2>&1; then
      echo "error: gh CLI is required (brew install gh)" >&2
      exit 1
    fi
    gh release download -R "$REPO" -p bridge.pyz -D "$INSTALL_DIR/bin" --clobber
    chmod 755 "$INSTALL_DIR/bin/bridge.pyz"

    echo "==> writing $PLIST_DEST"
    mkdir -p "$(dirname "$PLIST_DEST")"
    sed -e "s|@@INSTALL_DIR@@|$INSTALL_DIR|g" \
        -e "s|@@HOME@@|$HOME|g" \
        "$PLIST_SRC" > "$PLIST_DEST"

    echo "==> bootstrapping launchd service"
    # bootstrap 已存在会失败；先尝试 bootout 再 bootstrap，幂等
    launchctl bootout "gui/$(id -u)/com.infill.bridge" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST"
    launchctl enable "$SERVICE_TARGET" 2>/dev/null || true
    launchctl kickstart -k "$SERVICE_TARGET"

    echo "==> done. tail logs: tail -F $INSTALL_DIR/log/bridge.err.log"
    ;;
  status)
    launchctl print "$SERVICE_TARGET" 2>&1 | head -30 || true
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/com.infill.bridge" 2>/dev/null || true
    rm -f "$PLIST_DEST"
    echo "==> service removed. files at $INSTALL_DIR left in place (manual rm if you want)"
    ;;
  *)
    echo "usage: $0 {init|status|uninstall}"
    exit 1
    ;;
esac
