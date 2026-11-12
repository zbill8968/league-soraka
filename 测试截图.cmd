@echo off
chcp 65001 >nul
title 测试截图
rem 把一张截图文件拖到这个图标上，面板会直接识别这张图（不用进游戏）。
setlocal
cd /d "%~dp0"
set "PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=python"
set "NODE=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
if not exist "%NODE%" set "NODE=node"

if "%~1"=="" (
  echo.
  echo   用法：把一张截图文件拖到本图标上
  echo   截图位置：%~dp0data\screenshots\
  echo.
  pause
  exit /b 1
)

"%PY%" "%~dp0overlay-py\overlay.py" --image "%~1" --node "%NODE%"
echo.
echo 面板已关闭（右键面板可关闭）
pause
endlocal
