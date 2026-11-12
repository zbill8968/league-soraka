@echo off
chcp 65001 >nul
rem 取消 League Soraka 的登录自启（不会关闭正在运行的实例）
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-autostart.ps1" -Disable
echo.
echo 已取消登录自启。
timeout /t 5 >nul
