@echo off
rem CustomTrader Windows 发行包构建（产物: backend\dist\CustomTrader.exe）
rem 前置：Node.js 18+、Python 3.11+、backend\.venv 已装依赖（含 pywebview）
setlocal
cd /d "%~dp0"

echo [1/2] 构建前端...
pushd frontend && call npm run build || goto :err
popd

echo [2/2] PyInstaller 打包...
pushd backend
if not exist .venv\Scripts\python.exe (
    echo 错误: 未找到 backend\.venv，请先运行 install.bat
    popd & goto :err
)
.venv\Scripts\python.exe -m PyInstaller pack.spec --noconfirm || goto :err
popd

echo 完成: backend\dist\CustomTrader.exe
echo 打包发行: 将该 exe 与 packaging\windows\install.bat 一起压缩分发
goto :eof

:err
popd
echo 构建失败
exit /b 1
