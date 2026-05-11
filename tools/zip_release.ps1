param(
  [string]$Source = ".\release",
  [string]$Zip = ".\ConnectExporter-win64.zip"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Source)) { throw "Source folder not found: $Source" }
if (Test-Path $Zip) { Remove-Item -Force $Zip }

Compress-Archive -Path (Join-Path $Source "*") -DestinationPath $Zip -Force

$z = Get-Item $Zip
Write-Output ("ZIP: " + $z.FullName)
Write-Output ("Size: " + [Math]::Round($z.Length / 1MB, 1) + " MB")
