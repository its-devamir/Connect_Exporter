param(
  [string]$Exe = ".\release\ConnectExporter.exe",
  [int]$Port = 8765,
  [int]$TimeoutSec = 25
)

$ErrorActionPreference = "SilentlyContinue"

$exeFull = (Resolve-Path $Exe -ErrorAction Stop).Path
$logOut = Join-Path $env:TEMP "ConnectExporter.smoke.stdout.log"
$logErr = Join-Path $env:TEMP "ConnectExporter.smoke.stderr.log"

Get-Process ConnectExporter -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Remove-Item -Force $logOut, $logErr -ErrorAction SilentlyContinue

$p = Start-Process -FilePath $exeFull -PassThru -WindowStyle Hidden `
       -RedirectStandardOutput $logOut -RedirectStandardError $logErr

$deadline = (Get-Date).AddSeconds($TimeoutSec)
$ok = $false
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Milliseconds 1000
  try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/api/system" -TimeoutSec 2
    Write-Output ("HTTP " + $r.StatusCode)
    $len = [Math]::Min(220, $r.Content.Length)
    Write-Output $r.Content.Substring(0, $len)
    $ok = $true
    break
  } catch {}
}

if (-not $ok) { Write-Output "HTTP not reachable in $TimeoutSec seconds." }

Write-Output "----- STDOUT -----"
if (Test-Path $logOut) { Get-Content $logOut -Tail 30 } else { Write-Output "(no stdout)" }
Write-Output "----- STDERR -----"
if (Test-Path $logErr) { Get-Content $logErr -Tail 30 } else { Write-Output "(no stderr)" }

Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
Get-Process ConnectExporter -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

if ($ok) { exit 0 } else { exit 1 }
