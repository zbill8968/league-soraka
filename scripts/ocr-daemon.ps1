param(
  [string]$Language = 'zh-Hans-CN'
)

# Long-lived OCR worker: reads "imagePath|outFile" lines from stdin, writes the
# recognition result as UTF-8 JSON to outFile, then prints "done".
# Keeping one PowerShell process alive removes ~400 ms of startup per hotkey press.

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

$engine = $null
try {
  $languageTag = New-Object -TypeName Windows.Globalization.Language -ArgumentList $Language
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($languageTag)
} catch {
  $engine = $null
}
if ($null -eq $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if ($null -eq $engine) { throw 'no OCR engine available' }

[Console]::Out.WriteLine('ready')
[Console]::Out.Flush()

while ($true) {
  $line = [Console]::In.ReadLine()
  if ($null -eq $line) { break }
  if ($line.Trim().Length -eq 0) { continue }
  $parts = $line.Split('|')
  $path = $parts[0]
  $out = $parts[1]
  try {
    $bytes = [System.IO.File]::ReadAllBytes($path)
    $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
    $writer = New-Object Windows.Storage.Streams.DataWriter($stream)
    $writer.WriteBytes($bytes)
    Await ($writer.StoreAsync()) ([uint32]) | Out-Null
    $writer.DetachStream() | Out-Null
    $stream.Seek(0)

    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

    $lines = @()
    foreach ($ocrLine in $result.Lines) {
      $words = @($ocrLine.Words)
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
        text = $ocrLine.Text
        x = [math]::Round($x, 1)
        y = [math]::Round($y, 1)
        w = [math]::Round($right - $x, 1)
        h = [math]::Round($bottom - $y, 1)
      }
    }
    $payload = [pscustomobject]@{ language = $engine.RecognizerLanguage.LanguageTag; text = $result.Text; lines = $lines } |
      ConvertTo-Json -Depth 4 -Compress
    [System.IO.File]::WriteAllText($out, $payload, [System.Text.UTF8Encoding]::new($false))
  } catch {
    [System.IO.File]::WriteAllText($out, '{"lines":[]}', [System.Text.UTF8Encoding]::new($false))
  }
  [Console]::Out.WriteLine('done')
  [Console]::Out.Flush()
}
