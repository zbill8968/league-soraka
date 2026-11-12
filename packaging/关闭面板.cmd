@echo off
chcp 65001 >nul
rem 关闭 League Soraka
taskkill /im LeagueSoraka.exe /f >nul 2>&1
echo 已关闭 League Soraka。
timeout /t 3 >nul
