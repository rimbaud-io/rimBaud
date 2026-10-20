@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0risk-confirm.ps1"
if errorlevel 1 (
  echo The risk notice was not accepted. Hunters were not started.
  exit /b 1
)
set "RIMBAUD_RISK_FILE=%USERPROFILE%\.config\rimbaud\risk-notice.json"
set "RIMBAUD_RISK_FILE=%RIMBAUD_RISK_FILE:\=/%"
if not exist "%USERPROFILE%\.config\rimbaud\risk-notice.json" (
  echo The risk notice was not saved.
  exit /b 1
)
docker compose up --build %*
exit /b %ERRORLEVEL%
