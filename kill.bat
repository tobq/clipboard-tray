@echo off
:: Kill Electron clipboard-tray processes only
wmic process where "name='electron.exe' and commandline like '%%clipboard-tray%%'" call terminate >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Clipboard" /FI "IMAGENAME eq electron.exe" >nul 2>&1
:: Also kill old Python version if still running
taskkill /F /FI "IMAGENAME eq pythonw.exe" >nul 2>&1
wmic process where "name='python.exe' and commandline like '%%clipboard-tray.py%%'" call terminate >nul 2>&1
echo Clipboard tray stopped.
