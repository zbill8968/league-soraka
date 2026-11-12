@echo off
chcp 65001 >nul
rem League Soraka: SGP self test. Needs elevation to read the client token.
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

set "LOG=%~dp0.cache\sgp-selftest.log"
if not exist "%~dp0.cache" mkdir "%~dp0.cache"
echo ===== League Soraka SGP self test ===== > "%LOG%"
echo date: %date% %time% >> "%LOG%"

set "PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
echo python: "%PY%" >> "%LOG%"
if not exist "%PY%" (
  echo ERROR: python not found >> "%LOG%"
  type "%LOG%"
  echo.
  echo 找不到 Python，请把上面的日志发给我。
  timeout /t 20 >nul
  exit /b 1
)

"%PY%" "%~dp0scripts\sgp-selftest.py" >> "%LOG%" 2>&1
echo exit code: %errorlevel% >> "%LOG%"
type "%LOG%"
echo.
echo 结果：.cache\sgp-selftest.json（日志：.cache\sgp-selftest.log）
timeout /t 20 >nul
