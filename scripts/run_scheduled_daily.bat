@echo off
setlocal enabledelayedexpansion

set PROJECT_ROOT=c:\Users\spart\OneDrive\Documents\psm-reports-automation-main
cd /d "%PROJECT_ROOT%"

powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%PROJECT_ROOT%\scripts\run_scheduled_daily.ps1" %*
set EXIT_CODE=%ERRORLEVEL%

exit /b %EXIT_CODE%

