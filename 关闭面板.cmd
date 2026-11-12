@echo off
chcp 65001 >nul
rem 彻底关闭增幅面板（右键只会收起到胶囊，不会退出进程）
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*overlay.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo.
echo 面板进程已结束。
timeout /t 3 >nul
