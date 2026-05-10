param(
  [string]$OutDir = ".\\release\\bin",
  [string]$FfmpegPath = "",
  [string]$FfprobePath = ""
)

$ErrorActionPreference = "Stop"

function Resolve-Exe([string]$name, [string]$explicitPath) {
  if ($explicitPath) {
    if (Test-Path $explicitPath) { return (Resolve-Path $explicitPath).Path }
    throw "File not found: $explicitPath"
  }
  $cmd = Get-Command $name -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source) { return $cmd.Source }
  return ""
}

$ffmpeg = Resolve-Exe "ffmpeg" $FfmpegPath
$ffprobe = Resolve-Exe "ffprobe" $FfprobePath

if (-not $ffmpeg -or -not $ffprobe) {
  Write-Host ""
  Write-Host "Could not locate ffmpeg/ffprobe on PATH."
  Write-Host "Either install FFmpeg and reopen PowerShell, or pass explicit paths:"
  Write-Host "  .\\tools\\copy_ffmpeg_bin.ps1 -FfmpegPath C:\\path\\ffmpeg.exe -FfprobePath C:\\path\\ffprobe.exe"
  Write-Host ""
  exit 1
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Copy-Item -Force $ffmpeg (Join-Path $OutDir "ffmpeg.exe")
Copy-Item -Force $ffprobe (Join-Path $OutDir "ffprobe.exe")

Write-Host "Copied:"
Write-Host "  ffmpeg:  $ffmpeg"
Write-Host "  ffprobe: $ffprobe"
Write-Host "To: $OutDir"

