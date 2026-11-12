@echo off
chcp 65001 >nul
rem 让 League Soraka 在登录时以最高权限自动启动（选人数据需要管理员）。
rem 首次运行会弹 UAC，点“是”即可；之后每次登录都不用再点。
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-autostart.ps1"
echo.
echo 已设置为“登录时以最高权限启动”。
timeout /t 5 >nul
