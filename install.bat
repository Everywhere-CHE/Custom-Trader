@echo off
rem ============================================================
rem  Quant Trader - dependency install (Windows)
rem  auto detect Python  Node.js £¬config PATH
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==========================================
echo  Quant Trader dependency install
echo ==========================================

rem ---------- auto find Python ----------
set PYTHON=
where python.exe >nul 2>nul
if !errorlevel! equ 0 (
    for /f "delims=" %%p in ('where python.exe') do (
        set PYTHON=%%p
        goto python_found
    )
)
where python3.exe >nul 2>nul
if !errorlevel! equ 0 (
    for /f "delims=" %%p in ('where python3.exe') do (
        set PYTHON=%%p
        goto python_found
    )
)
rem common install paths
for %%d in (
    "%LocalAppData%\Python\pythoncore-3.14-64\python.exe"
    "%LocalAppData%\Python\pythoncore-3.13-64\python.exe"
    "%LocalAppData%\Python\pythoncore-3.12-64\python.exe"
    "%LocalAppData%\Python\pythoncore-3.11-64\python.exe"
    "%ProgramFiles%\Python\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
) do (
    if exist %%d (
        set PYTHON=%%d
        goto python_found
    )
)
echo [] not found Python£¬please install manually Python 3.11+
echo         download: https://www.python.org/downloads/
echo.
echo         check after install "Add Python to PATH"
pause
exit /b 1

:python_found
echo [OK] Python: !PYTHON!
for /f "tokens=2" %%v in ('"!PYTHON!" --version 2^>^&1') do echo       version: %%v

rem ---------- auto find Node.js ----------
set NODE=
where node.exe >nul 2>nul
if !errorlevel! equ 0 (
    for /f "delims=" %%p in ('where node.exe') do (
        set NODE=%%p
        goto node_found
    )
)
for %%d in (
    "%ProgramFiles%\nodejs\node.exe"
    "C:\Program Files\nodejs\node.exe"
) do (
    if exist %%d (
        set NODE=%%d
        goto node_found
    )
)
echo [] not found Node.js£¬please install manually Node.js 18+
echo         download: https://nodejs.org/
pause
exit /b 1

:node_found
echo [OK] Node.js: !NODE!
for /f %%v in ('"!NODE!" --version') do echo        version: %%v

rem ---------- backend venv +  ----------
echo.
echo [1/3] create backend venvinstall dependencies ...
cd backend
if not exist .venv (
    "!PYTHON!" -m venv .venv
    if !errorlevel! neq 0 (
        echo [] venv creation failed
        pause
        exit /b 1
    )
)
.venv\Scripts\python.exe -m pip install --upgrade pip -q
.venv\Scripts\python.exe -m pip install -r requirements.txt
if !errorlevel! neq 0 (
    echo [] backend deps install failed
    echo         try mirror:
    echo         .venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    pause
    exit /b 1
)

rem ---------- .env init ----------
if not exist .env (
    copy .env.example .env >nul
    echo [OK] generated backend\.env
) else (
    echo [OK] backend\.env exists
)
cd ..

rem ---------- frontend ----------
echo.
echo [2/3] install frontend deps ...
cd frontend
call "!NODE!\..\npm" install 2>nul
if !errorlevel! neq 0 (
    call npm install 2>nul
    if !errorlevel! neq 0 (
        "!NODE!\..\npm.cmd" install
        if !errorlevel! neq 0 (
            echo [] frontend deps install failed
            echo         : npm install --registry=https://registry.npmmirror.com
            pause
            exit /b 1
        )
    )
)

rem ---------- frontend ----------
echo.
echo [3/3] build frontend ...
"!NODE!\..\npm.cmd" run build 2>nul
if !errorlevel! neq 0 (
    npm run build 2>nul
)
cd ..

echo.
echo ==========================================
echo  install complete£¡ start.bat one-click start¡£
echo ==========================================
if not "%1"=="--nopause" pause