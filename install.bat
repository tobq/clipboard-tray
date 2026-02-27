@echo off
echo Installing clipboard-tray dependencies...
pip install pystray pyperclip pillow keyboard pywebview
echo.
echo Creating startup shortcut...
powershell -ExecutionPolicy Bypass -File "%~dp0update-shortcut.ps1"
echo.
echo Done! Run start.bat or restart your PC to auto-start.
