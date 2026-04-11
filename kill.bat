@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:\=\\%"

:: Kill Electron processes whose command line contains this install directory.
:: Using wmic for path-based matching — this avoids false positives from other
:: Electron apps (VS Code, Discord, etc.) running concurrently.
wmic process where "name='electron.exe' and commandline like '%%!SCRIPT_DIR!node_modules%%'" call terminate >nul 2>&1

:: Also kill any legacy Python instance from the pre-Electron version
taskkill /F /FI "IMAGENAME eq pythonw.exe" >nul 2>&1
wmic process where "name='python.exe' and commandline like '%%clipboard-tray.py%%'" call terminate >nul 2>&1

:: Verify nothing left, retry once if it is
ping -n 2 127.0.0.1 >nul 2>&1
wmic process where "name='electron.exe' and commandline like '%%!SCRIPT_DIR!node_modules%%'" get processid 2>nul | findstr /r "[0-9]" >nul
if not errorlevel 1 (
  echo Warning: some processes still running, force killing...
  wmic process where "name='electron.exe' and commandline like '%%!SCRIPT_DIR!node_modules%%'" call terminate >nul 2>&1
  ping -n 2 127.0.0.1 >nul 2>&1
)

echo Clipboard tray stopped.
endlocal
