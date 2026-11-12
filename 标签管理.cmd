@echo off
chcp 65001 >nul
cd /d "%~dp0"
python scripts\tag_admin.py
pause
