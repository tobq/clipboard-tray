@echo off
for /f "tokens=2" %%a in ('wmic process where "commandline like '%%clipboard-tray.py%%'" get processid /format:list 2^>nul ^| find "="') do taskkill /F /PID %%a
echo Clipboard tray stopped.
