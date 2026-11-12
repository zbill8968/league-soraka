@echo off
chcp 65001 >nul
cd /d "%~dp0"
where pythonw >nul 2>&1
if %errorlevel%==0 (
  start "" pythonw scripts\tag_admin.py
) else (
  start "" python scripts\tag_admin.py
)
