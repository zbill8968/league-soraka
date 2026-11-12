$ErrorActionPreference = 'Stop'

# Start the overlay elevated. Champion-select bench data lives in the League client's
# command line, which only an elevated process may read; without that the panel falls
# back to reading just the champion name in the middle of the screen.
$base = Split-Path -Parent $PSScriptRoot
$vbs = Get-ChildItem $base -Filter '*.vbs' | Select-Object -First 1
if ($null -eq $vbs) { throw "no launcher .vbs found in $base" }

Start-Process -FilePath 'wscript.exe' -ArgumentList ('"' + $vbs.FullName + '"') -Verb RunAs
Write-Output "started elevated: $($vbs.Name)"
