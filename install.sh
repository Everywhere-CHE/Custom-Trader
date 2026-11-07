#!/usr/bin/env bash
# ============================================================
#  Quant Trader - 依赖安装脚本 (Linux / macOS)
#  在全新机器上部署时运行一次；之后用 ./start.sh 启动。
#  前置要求：Python 3.11+ 与 Node.js 18+（建议 20+）
#  用法: chmod +x install.sh && ./install.sh
# ============================================================
set -e
cd "$(dirname "$0")"

echo "=========================================="
echo " Quant Trader 依赖安装"
echo "=========================================="

# ---------- 检查 Python ----------
PY=python3
command -v $PY >/dev/null 2>&1 || PY=python
if ! command -v $PY >/dev/null 2>&1; then
    echo "[错误] 未找到 Python，请先安装 Python 3.11+"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-venv python3-pip"
    echo "  macOS:         brew install python@3.12"
    exit 1
fi
echo "[OK] $($PY --version)"

# ---------- 检查 Node ----------
if ! command -v node >/dev/null 2>&1; then
    echo "[错误] 未找到 Node.js，请先安装 Node.js 18+"
    echo "  Ubuntu/Debian: curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install nodejs"
    echo "  macOS:         brew install node"
    exit 1
fi
echo "[OK] Node.js $(node --version)"

# ---------- 后端 venv + 依赖 ----------
echo
echo "[1/3] 创建后端虚拟环境并安装依赖 ..."
cd backend
if [ ! -d .venv ]; then
    $PY -m venv .venv
fi
./.venv/bin/python -m pip install --upgrade pip -q
./.venv/bin/python -m pip install -r requirements.txt || {
    echo "[错误] 后端依赖安装失败（网络慢可加国内镜像）："
    echo "  ./.venv/bin/python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple"
    exit 1
}

# 提示：openctp-ctp 在部分 Linux 发行版可能无对应 wheel（CTP 官方主要支持
# Windows/部分 Linux）。若安装失败，平台其余功能不受影响（CTP 网关自动禁用）。

# ---------- .env 初始化 ----------
if [ ! -f .env ]; then
    cp .env.example .env
    echo "[OK] 已生成 backend/.env（默认配置，可稍后修改）"
else
    echo "[OK] backend/.env 已存在，保留现有配置"
fi
cd ..

# ---------- 前端依赖 ----------
echo
echo "[2/3] 安装前端依赖 ..."
cd frontend
npm install || {
    echo "[错误] 前端依赖安装失败（网络慢可用国内镜像）："
    echo "  npm install --registry=https://registry.npmmirror.com"
    exit 1
}

# ---------- 前端生产构建 ----------
echo
echo "[3/3] 构建前端生产包 ..."
npm run build
cd ..

echo
echo "=========================================="
echo " 安装完成！运行 ./start.sh 一键启动。"
echo " 配置文件: backend/.env（CTP 账号 / AI Key 也可在网页设置页配置）"
echo "=========================================="
