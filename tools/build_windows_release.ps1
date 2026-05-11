param(
  [string]$Name = "ConnectExporter",
  [string]$OutDir = ".\release",
  [string]$Python = "python",
  [string]$FfmpegExe = "",
  [string]$FfprobeExe = "",
  [switch]$NoZip
)

$ErrorActionPreference = "Stop"
Write-Host "Building Windows release..." -ForegroundColor Cyan

# 1) Ensure deps (in the chosen Python).
& $Python -m pip install --upgrade pip pyinstaller | Out-Null
& $Python -m pip install -r requirements.txt | Out-Null

# 2) Clean previous build artifacts and the output folder.
foreach ($d in @(".\build", ".\dist", $OutDir)) {
  if (Test-Path $d) { Remove-Item -Recurse -Force $d }
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# 3) Build the one-file exe. We exclude heavy unused modules so the binary stays small.
$pyArgs = @(
  "-m","PyInstaller",
  "--noconfirm","--clean",
  "--name",$Name,
  "--onefile",
  "--add-data","webui;webui",
  "--hidden-import","exporter.export",
  "--hidden-import","exporter.ffmpeg_render",
  "--hidden-import","exporter.edl",
  "--hidden-import","exporter.chat_ass",
  "--hidden-import","exporter.chapters",
  "--hidden-import","exporter.probe",
  "--hidden-import","exporter.timewarp",
  "--hidden-import","replay_core.chat",
  "--hidden-import","replay_core.connect_xml",
  "--hidden-import","replay_core.events",
  "--hidden-import","replay_core.session_model",
  "--hidden-import","replay_core.types",
  "--hidden-import","replay_web.connect_download",
  "--hidden-import","replay_web.server",
  "--hidden-import","replay_web.materials",
  "--hidden-import","exporter.pdf_pages",
  "--hidden-import","pypdfium2",
  "--collect-binaries","pypdfium2_raw"
)

$excludes = @(
  "matplotlib","numpy","pandas","scipy","tkinter","PIL","pytest","IPython","jupyter",
  "notebook","nbconvert","nbformat","ipykernel","sympy","lxml","sphinx","psutil",
  "pygments","pyzmq","zmq","pywt","skimage","sklearn","wx","tornado","pydoc_data"
)
foreach ($m in $excludes) { $pyArgs += @("--exclude-module", $m) }
$pyArgs += "replay_web\launcher.py"

& $Python @pyArgs

if (-not (Test-Path ".\dist\$Name.exe")) {
  throw "PyInstaller did not produce dist\$Name.exe"
}
Copy-Item -Force ".\dist\$Name.exe" (Join-Path $OutDir "$Name.exe")

# 4) Copy FFmpeg / FFprobe into <release>\bin so end users don't need them installed.
$binDir = Join-Path $OutDir "bin"
& "$PSScriptRoot\copy_ffmpeg_bin.ps1" -OutDir $binDir -FfmpegPath $FfmpegExe -FfprobePath $FfprobeExe

# 5) Friendly Windows launcher (double-click runs the exe).
$bat = @"
@echo off
cd /d %~dp0
start "" "%~dp0$Name.exe"
"@
$bat | Set-Content -Encoding ASCII -Path (Join-Path $OutDir "START_HERE.bat")

# 6) Ship a short README for non-developer users.
$readme = @"
Connect Exporter (Windows)

How to run
1. Make sure both files are next to each other:
     $Name.exe
     bin\ffmpeg.exe   (and bin\ffprobe.exe)
2. Double-click START_HERE.bat (or $Name.exe).
3. A browser tab opens at http://127.0.0.1:8765 - use the 4-step wizard:
     Download URL -> Upload -> Settings -> Export.
4. Final MP4 lands in your Videos\ConnectExports folder by default.

Notes
- First launch may take a few seconds; Windows Defender may scan the exe once.
- If you ever see "ffmpeg not found", make sure the bin\ folder is still next to the exe.
"@
$readme | Set-Content -Encoding UTF8 -Path (Join-Path $OutDir "README-WIN.txt")

# 7) Zip the release folder (skip with -NoZip).
if (-not $NoZip) {
  $zipPath = ".\$Name-win64.zip"
  if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
  Compress-Archive -Path (Join-Path $OutDir "*") -DestinationPath $zipPath -Force
  Write-Host ""
  Write-Host ("ZIP: " + (Resolve-Path $zipPath).Path) -ForegroundColor Green
  Write-Host ("Size: " + [Math]::Round(((Get-Item $zipPath).Length / 1MB), 1) + " MB")
}

Write-Host ""
Write-Host "Done. Upload the ZIP to GitHub Releases." -ForegroundColor Green
