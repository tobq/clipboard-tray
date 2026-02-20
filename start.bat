@echo off
:: Kill any existing instance first
for /f "tokens=2" %%a in ('wmic process where "commandline like '%%clipboard-tray.py%%'" get processid /format:list 2^>nul ^| find "="') do taskkill /F /PID %%a >nul 2>&1

:: Start in background (no console window)
start "" /B pythonw "%~dp0clipboard-tray.py"
echo Clipboard tray started.
