@echo off
chcp 65001 >nul
rem 取消 League Soraka 开机自动启动（不会关闭正在运行的胶囊）
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
schtasks /delete /tn "LeagueSoraka" /f >nul 2>&1
echo 已取消开机自启。
timeout /t 4 >nul
