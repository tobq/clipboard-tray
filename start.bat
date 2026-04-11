@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR_ESC=%SCRIPT_DIR:\=\\%"

:: Kill any existing instance first
call "%~dp0kill.bat"

:: Verify nothing left
wmic process where "name='electron.exe' and commandline like '%%!SCRIPT_DIR_ESC!node_modules%%'" get processid 2>nul | findstr /r "[0-9]" >nul
if not errorlevel 1 (
  echo ERROR: Failed to kill existing instance. Aborting.
  endlocal
  exit /b 1
)

:: Start Electron in background (no console window)
start "" /B cmd /c "cd /d "%~dp0" && npx electron . > nul 2>&1"
echo Clipboard tray started.
endlocal
