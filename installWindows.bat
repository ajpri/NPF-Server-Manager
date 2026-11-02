@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0installWindows.ps1" %*
set "npfInstallerExit=%errorlevel%"
if not "%NPF_INSTALLER_NO_PAUSE%"=="1" pause
exit /b %npfInstallerExit%
