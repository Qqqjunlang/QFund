@echo off
"%SystemRoot%\System32\chcp.com" 65001 >nul
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo 未找到项目环境，请先按 README 安装。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m fundlab.cli update
set "fundlabExit=%ERRORLEVEL%"
echo.
echo 操作已结束，请查看上面的保存及导出结果。
pause
exit /b %fundlabExit%
