@echo off
:: Kill any existing instance first
for /f "tokens=2" %%a in ('wmic process where "commandline like '%%clipboard-tray.py%%'" get processid /format:list 2^>nul ^| find "="') do taskkill /F /PID %%a >nul 2>&1

:: Start in background (no console window)
start "" /B "C:\Users\Tobi\AppData\Local\Programs\Python\Python39\pythonw.exe" "%~dp0clipboard-tray.py"
echo Clipboard tray started.
