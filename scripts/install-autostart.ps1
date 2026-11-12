param([switch]$Disable)

# Register the overlay as a logon task with highest privileges, so champion-select
# data (which needs the League client's command line) works without a UAC prompt
# every time you log in.
$task = 'league-soraka'
$startupLnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'league-soraka.lnk'
if (Test-Path $startupLnk) { Remove-Item $startupLnk -Force -ErrorAction SilentlyContinue }

if ($Disable) {
  schtasks /delete /tn $task /f 2>$null | Out-Null
  Write-Output "removed task: $task"
  exit 0
}

$base = Split-Path -Parent $PSScriptRoot
$vbs = Get-ChildItem $base -Filter '*.vbs' | Select-Object -First 1
if ($null -eq $vbs) { throw "no launcher .vbs found in $base" }

$tr = 'wscript.exe "' + $vbs.FullName + '"'
$result = schtasks /create /tn $task /tr $tr /sc onlogon /rl highest /f 2>&1
Write-Output $result
