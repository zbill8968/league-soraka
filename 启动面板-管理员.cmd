@echo off
chcp 65001 >nul
rem League Soraka：以管理员身份启动（选人阶段读取"可用"英雄需要该权限）
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-elevated.ps1"
timeout /t 4 >nul
