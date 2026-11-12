# Build the shareable League Soraka bundle (Windows, PyInstaller onedir).
#
#   powershell -ExecutionPolicy Bypass -File scripts\build-package.ps1
#
# Produces dist\LeagueSoraka\ with the exe, data/, scripts/ and the launchers, then
# zips it to dist\LeagueSoraka-<version>.zip.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root 'dist'
$bundle = Join-Path $dist 'LeagueSoraka'
$app = Join-Path $bundle 'LeagueSoraka'

$python = $env:LEAGUE_SORAKA_PYTHON
if (-not $python) {
  $python = Join-Path $env:USERPROFILE '.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
}
if (-not (Test-Path $python)) { throw "python not found: $python" }

Write-Output "[1/5] clean"
if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
New-Item -ItemType Directory -Path $dist | Out-Null

Write-Output "[2/5] pyinstaller"
Push-Location $root
& $python -m PyInstaller --noconfirm --clean --windowed --onedir --name LeagueSoraka `
  --paths (Join-Path $root 'overlay-py') `
  --collect-submodules PIL `
  --distpath $dist --workpath (Join-Path $root 'build') --specpath (Join-Path $root 'build') `
  (Join-Path $root 'overlay-py\overlay.py')
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }
Pop-Location

Write-Output "[3/5] assets"
$exeDir = Join-Path $dist 'LeagueSoraka'
Copy-Item (Join-Path $root 'data') (Join-Path $exeDir 'data') -Recurse
New-Item -ItemType Directory -Path (Join-Path $exeDir 'scripts') | Out-Null
Copy-Item (Join-Path $root 'scripts\ocr-win.ps1') (Join-Path $exeDir 'scripts')
Copy-Item (Join-Path $root 'scripts\ocr-daemon.ps1') (Join-Path $exeDir 'scripts')
Copy-Item (Join-Path $root 'scripts\ocr-daemon-file.ps1') (Join-Path $exeDir 'scripts')
Copy-Item (Join-Path $root 'scripts\install-autostart.ps1') (Join-Path $exeDir 'scripts')

# Drop build artefacts inside the copied data.
Remove-Item (Join-Path $exeDir 'data\tmp') -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $exeDir 'data\avatar\*.png') -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $root 'packaging\*') $exeDir -Recurse

Write-Output "[4/5] zip"
$version = (Get-Content (Join-Path $root 'package.json') -Raw | ConvertFrom-Json).version
$zip = Join-Path $dist ("LeagueSoraka-$version.zip")
Compress-Archive -Path (Join-Path $dist 'LeagueSoraka') -DestinationPath $zip -CompressionLevel Optimal
Write-Output "[5/5] done"
Write-Output ("bundle: " + $dist + '\LeagueSoraka')
Write-Output ("zip:    " + $zip + "  (" + [math]::Round((Get-Item $zip).Length / 1MB, 1) + " MB)")
