@echo off
rem CustomTrader Windows 安装：创建桌面与开始菜单快捷方式（数据保存在本目录 data\ 下）
setlocal
cd /d "%~dp0"
if not exist CustomTrader.exe (
    echo 错误: 未找到 CustomTrader.exe
    pause & exit /b 1
)

set "LNK_NAME=CustomTrader 量化交易终端"
set "TARGET=%~dp0CustomTrader.exe"
set "WORKDIR=%~dp0"
set "ICON=%~dp0app.ico"

echo [1/2] 创建快捷方式 ...
powershell -NoProfile -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop') + '\%LNK_NAME%.lnk'); $s.TargetPath = '%TARGET%'; $s.WorkingDirectory = '%WORKDIR%'; if (Test-Path '%ICON%') { $s.IconLocation = '%ICON%' }; $s.Save()"
powershell -NoProfile -Command ^
  "$menu = [Environment]::GetFolderPath('StartMenu') + '\Programs'; $s = (New-Object -ComObject WScript.Shell).CreateShortcut($menu + '\%LNK_NAME%.lnk'); $s.TargetPath = '%TARGET%'; $s.WorkingDirectory = '%WORKDIR%'; if (Test-Path '%ICON%') { $s.IconLocation = '%ICON%' }; $s.Save()"

echo [2/2] 完成
echo.
echo ✅ 安装完成 —— 桌面/开始菜单已创建「%LNK_NAME%」快捷方式
echo    数据目录: %WORKDIR%data（首次启动自动创建）
echo    首次登录: admin / admin（请立即在设置页修改密码）
echo    卸载: 直接删除本目录
pause
