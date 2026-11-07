@echo off
cd /d "%~dp0"
title QuantTrader

echo ==========================================
echo  QuantTrader starting ...
echo    Backend  : http://127.0.0.1:8000
echo    Frontend : http://localhost:5173
echo ==========================================

rem ---- auto detect£¬ ----
if not exist backend\.venv\Scripts\python.exe (
    echo [INFO] backend£¬ ...
    call install.bat --nopause
    if errorlevel 1 (
        echo [ERROR] failed£¬ install.bat
        pause
        exit /b 1
    )
)
if not "%1"=="--prod" (
    if not exist frontend\node_modules (
        echo [INFO] frontend£¬ ...
        call install.bat --nopause
        if errorlevel 1 (
            echo [ERROR] failed£¬ install.bat
            pause
            exit /b 1
        )
    )
)

rem ---- Start backend ----
echo [1/3] Starting backend ...
start "QuantTrader-Backend" /min cmd /c "cd /d %~dp0backend && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

echo       Waiting for backend ...
set tries=0
:wait_backend
set /a tries=tries+1
if %tries% gtr 15 (
    echo [ERROR] Backend not ready after 30s
    pause
    exit /b 1
)
timeout /t 2 >nul
powershell -Command "try { (Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/health' -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 goto wait_backend
echo       Backend ready

rem ---- Start frontend ----
if "%1"=="--prod" goto prod
echo [2/3] Starting frontend ...
pushd frontend
start "QuantTrader-Frontend" /min cmd /c "npm run dev"
popd
timeout /t 3 >nul
echo       Frontend ready

echo [3/3] Opening browser ...
start "" http://localhost:5173

echo.
echo ==========================================
echo  Running. Press any key to STOP all.
echo ==========================================
pause >nul

echo Stopping ...
call :kill_port 8000
call :kill_port 5173
taskkill /f /fi "WINDOWTITLE eq QuantTrader-Backend*" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq QuantTrader-Frontend*" >nul 2>&1
echo QuantTrader stopped.
exit /b 0

:prod
start "" http://localhost:8000
pushd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000

rem ---- kill by port ----
:kill_port
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":%1 " ^| findstr "LISTENING"') do (
    taskkill /f /pid %%p >nul 2>&1
)
exit /b 0