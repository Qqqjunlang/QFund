@echo off
"%SystemRoot%\System32\chcp.com" 65001 >nul
setlocal
title QFund - 基金数据工作台
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 未找到项目Python环境。请保留本窗口，按README中的“重建环境”步骤安装后重试。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import fundlab.launcher" >nul 2>nul
if errorlevel 1 (
  echo 项目环境无法运行，可能是Python基础安装已移动或项目尚未安装。
  echo 请按README的“重建环境”步骤修复，不要使用系统默认Python替代。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m fundlab.launcher
set "fundlabLaunchExit=%ERRORLEVEL%"
if not "%fundlabLaunchExit%"=="0" (
  echo.
  echo 启动未完成，请查看上方原因。使用说明见本目录README.md。
  pause
)
exit /b %fundlabLaunchExit%
