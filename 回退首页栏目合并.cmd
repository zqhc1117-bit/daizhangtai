@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-backups\home-overview-20261117-100551\restore.ps1"
if errorlevel 1 (
  echo Rollback failed. Keep this window open and report the error above.
) else (
  echo Rollback completed. Refresh the browser with Ctrl+F5.
)
pause
