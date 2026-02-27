@echo off
call "%~dp0kill.bat"
ping -n 2 127.0.0.1 >nul
call "%~dp0start.bat"
