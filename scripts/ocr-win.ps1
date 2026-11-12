param(
  [Parameter(Mandatory = $true)][string]$Path,
  [string]$Language,
  [string]$OutFile
)

# Windows built-in OCR (Windows.Media.Ocr). Works offline, no extra install.
# Reads the PNG through an in-memory stream because StorageFile APIs fail in
# desktop PowerShell sessions without package identity.
#
# Output: one JSON object on stdout: { language, text, lines: [{ text, x, y, w, h }] }

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
  })[0]

function Await($operation, $resultType) {
  $asTask = $asTaskGeneric.MakeGenericMethod($resultType)
  $netTask = $asTask.Invoke($null, @($operation))
  $netTask.Wait(-1) | Out-Null
  $netTask.Result
}

[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.Streams.DataWriter, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null

$bytes = [System.IO.File]::ReadAllBytes($Path)
$stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
$writer = New-Object Windows.Storage.Streams.DataWriter($stream)
$writer.WriteBytes($bytes)
Await ($writer.StoreAsync()) ([uint32]) | Out-Null
$writer.DetachStream() | Out-Null
$stream.Seek(0)

$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

$engine = $null
if ($Language) {
  $languageTag = New-Object -TypeName Windows.Globalization.Language -ArgumentList $Language
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($languageTag)
}
if ($null -eq $engine) {
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
}
if ($null -eq $engine) { throw 'no OCR engine available for this machine' }

$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

$lines = @()
foreach ($line in $result.Lines) {
  $words = @($line.Words)
  if ($words.Count -eq 0) { continue }
  $x = [double]::MaxValue
  $y = [double]::MaxValue
  $right = 0.0
  $bottom = 0.0
  foreach ($word in $words) {
    $r = $word.BoundingRect
    if ($r.X -lt $x) { $x = $r.X }
    if ($r.Y -lt $y) { $y = $r.Y }
    if (($r.X + $r.Width) -gt $right) { $right = $r.X + $r.Width }
    if (($r.Y + $r.Height) -gt $bottom) { $bottom = $r.Y + $r.Height }
  }
  $lines += [pscustomobject]@{
    text = $line.Text
    x = [math]::Round($x, 1)
    y = [math]::Round($y, 1)
    w = [math]::Round($right - $x, 1)
    h = [math]::Round($bottom - $y, 1)
  }
}

$payload = [pscustomobject]@{
  language = $engine.RecognizerLanguage.LanguageTag
  text     = $result.Text
  lines    = $lines
} | ConvertTo-Json -Depth 4 -Compress

if ($OutFile) {
  # Windows PowerShell writes redirected stdout in the console codepage, which
  # corrupts Chinese text; a UTF-8 file is reliable for callers.
  [System.IO.File]::WriteAllText($OutFile, $payload, [System.Text.UTF8Encoding]::new($false))
}
Write-Output $payload
