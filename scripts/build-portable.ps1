# Build a self-contained portable bundle without PyInstaller.
#
#   powershell -ExecutionPolicy Bypass -File scripts\build-portable.ps1
#
# The bundle contains a trimmed copy of the bundled Python runtime, the Python
# overlay sources, data/ and scripts/, and a VBS launcher that requests admin.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root 'dist'
$bundle = Join-Path $dist 'LeagueSoraka'
$app = Join-Path $bundle 'app'

$python = $env:LEAGUE_SORAKA_PYTHON
if (-not $python) {
  $python = Join-Path $env:USERPROFILE '.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python'
}
if (-not (Test-Path (Join-Path $python 'python.exe'))) { throw "python runtime not found: $python" }

Write-Output "[1/6] clean"
if (Test-Path $bundle) { Remove-Item $bundle -Recurse -Force }
New-Item -ItemType Directory -Path $bundle | Out-Null

Write-Output "[2/6] python runtime"
$pyDest = Join-Path $bundle 'python'
Copy-Item $python $pyDest -Recurse -Force
foreach ($drop in @('include', 'libs', 'Scripts')) {
  Remove-Item (Join-Path $pyDest $drop) -Recurse -Force -ErrorAction SilentlyContinue
}
# Keep only Pillow + its typing helper; the overlay does not need numpy/pandas/docx.
$site = Join-Path $pyDest 'Lib\site-packages'
Get-ChildItem $site -Force | Where-Object {
  $_.Name -notin @('PIL', 'pillow-12.3.0.dist-info', 'typing_extensions.py')
} | Remove-Item -Recurse -Force
foreach ($drop in @('idlelib', 'turtledemo', 'ensurepip', 'lib2to3', 'venv', 'pydoc_data', 'msilib', 'curses', 'dbm')) {
  Remove-Item (Join-Path $pyDest "Lib\$drop") -Recurse -Force -ErrorAction SilentlyContinue
}
Get-ChildItem $pyDest -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem (Join-Path $pyDest 'DLLs') -Filter '_test*.pyd' -ErrorAction SilentlyContinue |
  Remove-Item -Force -ErrorAction SilentlyContinue

Write-Output "[3/6] app + data"
New-Item -ItemType Directory -Path $app | Out-Null
Copy-Item (Join-Path $root 'overlay-py') (Join-Path $app 'overlay-py') -Recurse -Force
Get-ChildItem (Join-Path $app 'overlay-py') -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $root 'data') (Join-Path $app 'data') -Recurse -Force
Remove-Item (Join-Path $app 'data\tmp') -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $app 'data\screenshots') -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path (Join-Path $app 'data\tmp') | Out-Null
New-Item -ItemType Directory -Path (Join-Path $app 'data\screenshots') | Out-Null
# Never ship the developer's personal rule overrides; every user creates their own
# through the bundled 标签管理.cmd.
Remove-Item (Join-Path $app 'data\tag-overrides.json') -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $app 'data\tag-overrides.json.bak') -Force -ErrorAction SilentlyContinue
# Ship the small item-icon cache so the first build panel does not need to
# download ~20 images before showing icons. Build data/cache is still omitted.
$iconSrc = Join-Path $root 'data\tmp\item-icons'
if (Test-Path $iconSrc) {
  $iconDest = Join-Path $app 'data\tmp\item-icons'
  New-Item -ItemType Directory -Path $iconDest | Out-Null
  Copy-Item (Join-Path $iconSrc '*') $iconDest -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Path (Join-Path $app 'scripts') | Out-Null
foreach ($script in @('ocr-win.ps1', 'ocr-daemon.ps1', 'ocr-daemon-file.ps1', 'tag_admin.py', 'tag-admin.html')) {
  Copy-Item (Join-Path $root "scripts\$script") (Join-Path $app 'scripts')
}

Write-Output "[4/6] launchers"
Copy-Item (Join-Path $root 'packaging\使用说明.txt') $bundle

$launcher = @'
' League Soraka portable launcher. Starts the overlay with Python's
' console-free interpreter, elevated (admin is needed for LCU data).
Option Explicit
Dim shell, fso, base, py, script, elevated, arg

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
py = base & "\python\pythonw.exe"
script = base & "\app\overlay-py\overlay.py"

If Not fso.FileExists(py) Then
  MsgBox "python\pythonw.exe not found. Please extract the whole zip again.", 16, "League Soraka"
  WScript.Quit 1
End If
If Not fso.FileExists(script) Then
  MsgBox "app\overlay-py\overlay.py not found. Please extract the whole zip again.", 16, "League Soraka"
  WScript.Quit 1
End If

elevated = False
For Each arg In WScript.Arguments
  If LCase(arg) = "/elevated" Then elevated = True
Next

If Not elevated Then
  CreateObject("Shell.Application").ShellExecute "wscript.exe", """" & WScript.ScriptFullName & """ /elevated", base, "runas", 1
  WScript.Quit 0
End If

shell.CurrentDirectory = base
shell.Run """" & py & """ """ & script & """", 0, False
'@
$launcher = $launcher -replace "`r?`n", "`r`n"
[IO.File]::WriteAllText((Join-Path $bundle '启动LeagueSoraka.vbs'), $launcher, [Text.Encoding]::ASCII)

$closer = @'
@echo off
chcp 65001 >nul
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
set "BASE=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$base=[regex]::Escape('%BASE%'); Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -and $_.CommandLine -match $base -and $_.CommandLine -like '*overlay.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo League Soraka closed.
timeout /t 2 >nul
'@
$closer = $closer -replace "`r?`n", "`r`n"
[IO.File]::WriteAllText((Join-Path $bundle '关闭面板.cmd'), $closer, [Text.Encoding]::ASCII)

$tagAdmin = @'
@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "python\pythonw.exe" (
  echo python runtime not found. Please extract the whole zip again.
  pause
  exit /b 1
)
if not exist "app\scripts\tag_admin.py" (
  echo tag_admin.py not found. Please extract the whole zip again.
  pause
  exit /b 1
)
start "" "python\pythonw.exe" "app\scripts\tag_admin.py"
'@
$tagAdmin = $tagAdmin -replace "`r?`n", "`r`n"
[IO.File]::WriteAllText((Join-Path $bundle '标签管理.cmd'), $tagAdmin, [Text.Encoding]::ASCII)

Write-Output "[5/6] zip"
$version = (Get-Content (Join-Path $root 'package.json') -Raw | ConvertFrom-Json).version
$zip = Join-Path $dist ("LeagueSoraka-$version-portable.zip")
if (Test-Path $zip) { Remove-Item $zip -Force }
& (Join-Path $python 'python.exe') (Join-Path $root 'scripts\zip-bundle.py') $bundle $zip
if ($LASTEXITCODE -ne 0) { throw "zip failed" }
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash
Set-Content -LiteralPath ($zip + '.sha256') -Value $hash -Encoding ASCII

Write-Output "[6/6] done"
Write-Output ("bundle: " + $bundle)
Write-Output ("zip:    " + $zip + "  (" + [math]::Round((Get-Item $zip).Length / 1MB, 1) + " MB)")
Write-Output ("sha256: " + $hash)
