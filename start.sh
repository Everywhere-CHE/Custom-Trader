#!/usr/bin/env bash
# ============================================================
#  Quant Trader - 一键启动 (Git Bash / Linux / macOS)
#  单窗口运行后端 + 前端，自动打开浏览器
#  用法: ./start.sh
#        ./start.sh --prod    生产模式（仅后端，托管前端构建包）
#        ./start.sh --quiet   静默启动（只打开浏览器，不打印日志）
# ============================================================
set -e
cd "$(dirname "$0")"

APP_NAME="QuantTrader"

# ---------- 环境检查（缺依赖时自动运行 install.sh） ----------
if [ ! -x backend/.venv/bin/python ] && [ ! -x backend/.venv/Scripts/python ]; then
    echo "[提示] 未找到后端虚拟环境，自动运行 ./install.sh ..."
    bash install.sh
fi
if [ ! -x backend/.venv/bin/python ] && [ ! -x backend/.venv/Scripts/python ]; then
    echo "[错误] 依赖安装失败，请检查上方输出后手动运行 ./install.sh"
    exit 1
fi

# 确定 Python 路径（兼容 Git Bash Windows 和 Linux/macOS）
# 注意：相对于 backend/ 目录，启动命令都在 cd backend 之后执行
PYTHON=".venv/bin/python"
[ ! -x "backend/$PYTHON" ] && PYTHON=".venv/Scripts/python"

# ---------- 生产模式 ----------
if [ "$1" = "--prod" ]; then
    echo "=========================================="
    echo " ${APP_NAME} - 生产模式启动"
    echo " 地址: http://localhost:8000"
    echo "=========================================="
    cd backend
    exec "$PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
fi

if [ ! -d frontend/node_modules ]; then
    echo "[提示] 未找到前端依赖，自动运行 ./install.sh ..."
    bash install.sh
    if [ ! -d frontend/node_modules ]; then
        echo "[错误] 依赖安装失败，请检查上方输出后手动运行 ./install.sh"
        exit 1
    fi
fi

QUIET=false
[ "$1" = "--quiet" ] && QUIET=true

# ---------- 清理函数 ----------
kill_port() {
    # 按端口兜底清理（Git Bash/Windows 下 kill 无法杀掉 Windows 子进程）
    if command -v taskkill >/dev/null 2>&1; then
        for pid in $(netstat -ano 2>/dev/null | grep ":$1 " | grep LISTENING | awk '{print $NF}' | sort -u); do
            taskkill //F //PID "$pid" >/dev/null 2>&1 || true
        done
    elif command -v lsof >/dev/null 2>&1; then
        for pid in $(lsof -ti tcp:"$1" 2>/dev/null); do
            kill -9 "$pid" 2>/dev/null || true
        done
    fi
}

cleanup() {
    echo ""
    echo "正在停止服务 ..."
    kill "$BACKEND_PID" 2>/dev/null || true
    kill "$FRONTEND_PID" 2>/dev/null || true
    kill_port 8000
    kill_port 5173
    wait 2>/dev/null || true
    echo "${APP_NAME} 已停止"
}
trap cleanup EXIT INT TERM

# ---------- 启动前清理残留端口 ----------
kill_port 8000
kill_port 5173

# ---------- 启动后端 ----------
echo "=========================================="
echo " ${APP_NAME} 启动中 ..."
echo "   后端 API : http://127.0.0.1:8000 (/docs 接口文档)"
echo "   前端界面 : http://localhost:5173"
echo "   退出     : Ctrl + C"
echo "=========================================="

if $QUIET; then
    ( cd backend && "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > /dev/null 2>&1 ) &
else
    ( cd backend && exec "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 ) &
fi
BACKEND_PID=$!

# 等后端就绪
echo "等待后端启动 ..."
for i in $(seq 1 30); do
    if curl -s http://127.0.0.1:8000/api/health > /dev/null 2>&1; then
        echo "后端就绪 ✓"
        break
    fi
    sleep 1
done

# ---------- 启动前端 ----------
if $QUIET; then
    ( cd frontend && npm run dev > /dev/null 2>&1 ) &
else
    ( cd frontend && exec npm run dev ) &
fi
FRONTEND_PID=$!
sleep 2

# ---------- 打开浏览器 ----------
echo "正在打开浏览器 ..."
if which start > /dev/null 2>&1; then
    # Git Bash on Windows
    start http://localhost:5173
elif which xdg-open > /dev/null 2>&1; then
    xdg-open http://localhost:5173
elif which open > /dev/null 2>&1; then
    open http://localhost:5173
else
    echo "请手动打开浏览器访问 http://localhost:5173"
fi

echo ""
echo "所有服务已启动！按 Ctrl+C 停止。"
echo "=========================================="

wait