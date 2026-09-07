@echo off
setlocal

set "ELECTRON_EXE=%~dp0node_modules\electron\dist\electron.exe"

:: Kill only the Electron processes that belong to this checkout (never the
:: MCP helper, which an AI client owns). The logic lives in scripts\kill-app.ps1:
:: exact pid-file kill first (no WMI), then a WMI sweep capped at 8 s so a wedged
:: winmgmt can no longer hang this script, start.bat or update.bat forever.
:: The dir goes through an env var: "%~dp0" ends in a backslash that escapes the
:: closing quote on the PowerShell command line and mangles the argument.
set "BOARDCLIP_APP_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\kill-app.ps1"
if errorlevel 1 (
  echo ERROR: Failed to stop BoardClip.
  exit /b 1
)

echo BoardClip stopped.
