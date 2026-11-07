#!/usr/bin/env bash
# CustomTrader Linux 卸载（保留用户数据，如需彻底清除请手动删除 data/）
set -e
APP_DIR="${HOME}/.local/share/custom-trader"
rm -f "${HOME}/.local/share/applications/custom-trader.desktop"
rm -f "${HOME}/.local/share/icons/hicolor/256x256/apps/custom-trader.png"
rm -f "${HOME}/.local/bin/custom-trader"
XDG_DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || echo "${HOME}/Desktop")"
rm -f "$XDG_DESKTOP/custom-trader.desktop" 2>/dev/null || true
echo "已移除程序与快捷方式；数据保留在 ${APP_DIR}/data"
echo "如需彻底清除数据: rm -rf ${APP_DIR}"
