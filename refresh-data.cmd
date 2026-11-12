@echo off
rem Refresh the OP.GG snapshot and rebuild the icon index.
rem Safe to re-run: the fetch resumes where it left off.
setlocal
cd /d "%~dp0"
set ELECTRON_RUN_AS_NODE=
set NODE_EXE=node
if exist "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe" (
  set NODE_EXE=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe
)
"%NODE_EXE%" scripts\fetch-opgg.mjs
if errorlevel 1 goto :fail
"%NODE_EXE%" scripts\fetch-icons.mjs
if errorlevel 1 goto :fail
"%NODE_EXE%" scripts\build-icon-index.mjs
if errorlevel 1 goto :fail
"%NODE_EXE%" scripts\eval-matcher.mjs
pause
exit /b 0
:fail
echo.
echo refresh failed - see the output above.
pause
exit /b 1
