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
if "%~1"=="" goto usage
"%TASK_PYTHON%" -X utf8 -B maintenance.py restore "%~f1" --data-dir "%~dp0data"
set "TASK_EXIT=%ERRORLEVEL%"
pause
exit /b %TASK_EXIT%
:usage
echo 请先双击“停止工作台.cmd”，再把工作台生成的数据备份ZIP拖到本文件恢复。
pause
exit /b 1
