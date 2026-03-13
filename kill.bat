@echo off
taskkill /F /FI "IMAGENAME eq pythonw.exe" >nul 2>&1
wmic process where "name='python.exe' and commandline like '%%clipboard-tray.py%%'" call terminate >nul 2>&1
echo Clipboard tray stopped.
