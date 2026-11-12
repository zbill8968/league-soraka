@echo off
chcp 65001 >nul
rem 设置 League Soraka 开机自动启动（管理员权限，登录后自动出现头像胶囊）
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
schtasks /create /tn "LeagueSoraka" /tr "\"%~dp0LeagueSoraka.exe\"" /sc onlogon /rl highest /f >nul
if %errorlevel% equ 0 (
  echo 已设置：登录 Windows 后自动启动。
) else (
  echo 设置失败，请右键本文件选择“以管理员身份运行”。
)
timeout /t 5 >nul
