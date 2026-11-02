@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0installWindows.ps1" %*
exit /b %errorlevel%
