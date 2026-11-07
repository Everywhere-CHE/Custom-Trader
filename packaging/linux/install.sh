#!/usr/bin/env bash
# CustomTrader Linux 安装：装入用户目录并创建应用菜单/桌面图标
# 幂等：重复运行安全。数据保存在安装目录的 data/ 下。
set -e
cd "$(dirname "$0")"

if [ ! -x ./CustomTrader ]; then
    echo "错误: 未找到 CustomTrader 可执行文件（请在解压后的目录中运行）"
    exit 1
fi

APP_DIR="${HOME}/.local/share/custom-trader"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${HOME}/.local/share/applications"
ICON_DIR="${HOME}/.local/share/icons/hicolor/256x256/apps"

echo "[1/3] 安装程序文件到 ${APP_DIR} ..."
mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"
cp -f ./CustomTrader "$APP_DIR/"
cp -f ./icon.png "$APP_DIR/icon.png" 2>/dev/null || true
chmod +x "$APP_DIR/CustomTrader"

echo "[2/3] 写入应用菜单与桌面图标 ..."
cat > "$DESKTOP_DIR/custom-trader.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=CustomTrader
Name[zh_CN]=CustomTrader 量化交易终端
Comment=量化交易终端（数据保存在本机）
Exec=${APP_DIR}/CustomTrader
Icon=${ICON_DIR}/custom-trader.png
Terminal=false
Categories=Office;Finance;
DESKTOP
cp -f ./icon.png "$ICON_DIR/custom-trader.png" 2>/dev/null || true

# 桌面图标（存在桌面目录时）
XDG_DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || echo "${HOME}/Desktop")"
if [ -d "$XDG_DESKTOP" ]; then
    cp -f "$DESKTOP_DIR/custom-trader.desktop" "$XDG_DESKTOP/" 2>/dev/null || true
    [ -x /usr/bin/gtk-update-icon-cache ] && gtk-update-icon-cache -q -t -f "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi

echo "[3/3] 创建命令行入口 ${BIN_DIR}/custom-trader ..."
ln -sf "$APP_DIR/CustomTrader" "$BIN_DIR/custom-trader"

echo
echo "✅ 安装完成"
echo "   数据目录: ${APP_DIR}/data（随程序自动创建）"
echo "   启动方式: 应用菜单「CustomTrader」/ 桌面图标 / 终端运行 custom-trader"
echo "   卸载: 运行包内 uninstall.sh"
