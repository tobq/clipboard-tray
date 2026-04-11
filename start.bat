@echo off
:: Kill any existing instances first
call "%~dp0kill.bat"

:: Start Electron in background
start "" /B cmd /c "cd /d "%~dp0" && npx electron . > nul 2>&1"
echo Clipboard tray started.
