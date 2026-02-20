@echo off
call "%~dp0kill.bat"
timeout /t 1 /noexec >nul
call "%~dp0start.bat"
