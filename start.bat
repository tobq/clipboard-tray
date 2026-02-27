@echo off
:: Kill any existing instance first
taskkill /F /FI "IMAGENAME eq pythonw.exe" >nul 2>&1

:: Start in background (no console window)
start "" /B "C:\Users\Tobi\AppData\Local\Programs\Python\Python39\pythonw.exe" "%~dp0clipboard-tray.py"
echo Clipboard tray started.
