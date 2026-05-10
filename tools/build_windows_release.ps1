param(
  [string]$Name = "ConnectExporter",
  [string]$OutDir = ".\\release"
)

$ErrorActionPreference = "Stop"

Write-Host "Building Windows release..."

# Ensure deps
python -m pip install -r requirements.txt
python -m pip install pyinstaller

# Clean output
if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# Build exe
pyinstaller `
  --noconfirm `
  --clean `
  --name $Name `
  --onefile `
  --add-data "webui;webui" `
  "replay_web\\launcher.py"

Copy-Item -Force ".\\dist\\$Name.exe" (Join-Path $OutDir "$Name.exe")

Write-Host ""
Write-Host "Next:"
Write-Host "  1) Copy FFmpeg into $OutDir\\bin:"
Write-Host "     .\\tools\\copy_ffmpeg_bin.ps1 -OutDir `"$OutDir\\bin`""
Write-Host "  2) Zip the release folder and upload to GitHub Releases."
Write-Host ""

