#!/usr/bin/env bash
# CustomTrader Linux 发行包构建（产物: dist/CustomTrader-linux-x64.tar.gz）
# 前置：Node.js 18+、Python 3.11+、后端 .venv 已装依赖（含 pywebview）
set -e
cd "$(dirname "$0")"

echo "[1/3] 构建前端..."
(cd frontend && npm run build)

echo "[2/3] PyInstaller 打包..."
(cd backend && .venv/bin/python -m PyInstaller pack.spec --noconfirm)

echo "[3/3] 组装发行包..."
STAGE="dist/CustomTrader-linux-x64"
rm -rf "$STAGE" dist/CustomTrader-linux-x64.tar.gz
mkdir -p "$STAGE"
cp backend/dist/CustomTrader "$STAGE/"
cp packaging/linux/install.sh "$STAGE/install.sh"
cp packaging/linux/uninstall.sh "$STAGE/uninstall.sh"
cp assets/icons/icon-256.png "$STAGE/icon.png"
cp packaging/linux/使用说明.txt "$STAGE/" 2>/dev/null || true
chmod +x "$STAGE/CustomTrader" "$STAGE/install.sh" "$STAGE/uninstall.sh"
tar -czf "dist/CustomTrader-linux-x64.tar.gz" -C dist "$(basename "$STAGE")"
echo "完成: dist/CustomTrader-linux-x64.tar.gz"
