@echo off
"%SystemRoot%\System32\chcp.com" 65001 >nul
setlocal
title QFund - 安装环境
cd /d "%~dp0"
if not exist "scripts\install_environment.py" goto incomplete
if exist ".venv\Scripts\python.exe" goto existing
if defined QFUND_PYTHON goto configured
py -3.12 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 goto py312
py -3.11 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 goto py311
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 goto py3
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 goto python
 echo 未找到可用的 Python 3.11 或更新版本。建议安装 Python 3.12 后重试。
 echo 安装说明：docs\installation.md。不会修改系统默认 Python。
set "qfundSetupExit=1"
goto finish
:configured
"%QFUND_PYTHON%" -X utf8 "scripts\install_environment.py" %*
goto result
:existing
".venv\Scripts\python.exe" -X utf8 "scripts\install_environment.py" %*
goto result
:py312
py -3.12 -X utf8 "scripts\install_environment.py" %*
goto result
:py311
py -3.11 -X utf8 "scripts\install_environment.py" %*
goto result
:py3
py -3 -X utf8 "scripts\install_environment.py" %*
goto result
:python
python -X utf8 "scripts\install_environment.py" %*
goto result
:incomplete
echo 项目文件不完整，请解压完整源码后重试。
set "qfundSetupExit=1"
goto finish
:result
set "qfundSetupExit=%ERRORLEVEL%"
if not "%qfundSetupExit%"=="0" echo 安装未完成，已有文件保留。请查看上方提示及 docs\installation.md。
:finish
echo.
if not defined QFUND_SETUP_NO_PAUSE pause
exit /b %qfundSetupExit%
