@echo off
:: Kill Electron clipboard-tray processes (matches by exe path containing clipboard-tray)
powershell -NoProfile -Command "Get-Process electron -EA 0 | Where-Object { $_.Path -like '*clipboard-tray*' } | Stop-Process -Force" 2>nul
echo Clipboard tray stopped.
