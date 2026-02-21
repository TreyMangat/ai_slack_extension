# Sync local OpenClaw auth/config into repo-mounted secrets for Docker containers.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\sync_openclaw_auth.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\sync_openclaw_auth.ps1 -SourceDir "C:\Users\me\.openclaw"

param(
  [string]$SourceDir = "$env:USERPROFILE\.openclaw",
  [string]$DestinationDir = ".\secrets\openclaw"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (!(Test-Path -Path $SourceDir)) {
  throw "OpenClaw source directory not found: $SourceDir"
}

if (Test-Path -Path $DestinationDir) {
  Remove-Item -Path $DestinationDir -Recurse -Force
}

New-Item -ItemType Directory -Path $DestinationDir -Force | Out-Null

# Keep auth + model config; skip heavy runtime folders.
$robocopyArgs = @(
  $SourceDir,
  $DestinationDir,
  "/E",
  "/XD", "logs", "workspace", "completions",
  "/NFL", "/NDL", "/NJH", "/NJS", "/NP"
)
robocopy @robocopyArgs | Out-Null
if ($LASTEXITCODE -gt 7) {
  throw "robocopy failed with exit code $LASTEXITCODE"
}

$authFiles = Get-ChildItem -Path $DestinationDir -Recurse -File -Filter "auth*.json" -ErrorAction SilentlyContinue
if (-not $authFiles -or $authFiles.Count -eq 0) {
  throw "No OpenClaw auth files were copied. Expected auth*.json under $DestinationDir\\agents\\*\\agent"
}

Write-Host "Synced OpenClaw auth into $DestinationDir" -ForegroundColor Green
Write-Host "Next: restart containers so worker/api can use this auth mount." -ForegroundColor Cyan
