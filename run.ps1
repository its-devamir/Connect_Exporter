param(
  [Parameter(Position=0)]
  [string]$Folder = "."
)

$ErrorActionPreference = "Stop"

Write-Host "Adobe Connect Replay Engine (Web UI)"
Write-Host "Folder: $Folder"
Write-Host ""

# Run the local server from inside the export folder so relative paths like "." work.
$here = Get-Location
try {
  Set-Location $Folder
  Write-Host "Starting local server..."
  Write-Host "When it starts, open the printed URL in your browser."
  Write-Host ""
  python -m replay_web.run_server
} finally {
  Set-Location $here
}

