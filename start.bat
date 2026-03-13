@echo off
:: Kill any existing instances first
call "%~dp0kill.bat"

:: Start in background (no console window)
start "" /B "C:\Users\Tobi\AppData\Local\Programs\Python\Python39\pythonw.exe" "%~dp0clipboard-tray.py"
echo Clipboard tray started.
