@echo off
echo Installing clipboard-tray (Electron)...
cd /d "%~dp0"
call npm install
echo.
echo Setting up auto-start...
:: Remove old Python startup shortcut if it exists
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\clipboard-tray.lnk" >nul 2>&1
echo.
echo Done! Run start.bat to launch.
echo Auto-start can be toggled in Settings within the app.
