@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "TASK_PYTHON=%~dp0runtime\python\python.exe"
if not exist "%TASK_PYTHON%" (
  echo 缺少随包运行环境。请先完整解压整个压缩包，再从解压后的文件夹启动。
  pause
  exit /b 1
)
"%TASK_PYTHON%" -X utf8 -B stop.py %*
set "TASK_EXIT=%ERRORLEVEL%"
pause
exit /b %TASK_EXIT%
