@echo off
taskkill /F /FI "IMAGENAME eq pythonw.exe" >nul 2>&1
echo Clipboard tray stopped.
