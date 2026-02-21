# Validate local OpenClaw readiness for this repo.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\check_openclaw_setup.ps1

param(
  [string]$ExpectedModel = "openai-codex/gpt-5.3-codex"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Require-Command {
  param([Parameter(Mandatory = $true)][string]$Name)
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "Required command '$Name' was not found in PATH."
  }
}

Require-Command -Name "openclaw"

Write-Host "Checking OpenClaw version..." -ForegroundColor Cyan
openclaw --version

Write-Host "Checking configured model..." -ForegroundColor Cyan
$configured = ""
try {
  $configured = (openclaw config get agents.defaults.model.primary).Trim()
}
catch {
  throw "Could not read agents.defaults.model.primary. Run scripts/setup_openclaw.ps1 first."
}

if ($configured -ne $ExpectedModel) {
  Write-Warning "Configured model is '$configured' (expected '$ExpectedModel')."
}
else {
  Write-Host "Model configuration OK: $configured" -ForegroundColor Green
}

Write-Host "Checking provider availability..." -ForegroundColor Cyan
openclaw models list --provider openai-codex --all | Out-Null

Write-Host "Running local auth smoke check..." -ForegroundColor Cyan
$probeOutput = cmd /c "openclaw agent --local --agent main --message ""Reply with READY only."" --json 2>&1"
$probeExit = $LASTEXITCODE
$probeText = ($probeOutput | Out-String)

if ($probeExit -ne 0) {
  if ($probeText -match 'No API key found for provider "openai-codex"' -or $probeText -match "requires an interactive TTY") {
    Write-Host "OpenClaw auth is not complete for provider openai-codex." -ForegroundColor Yellow
    Write-Host "Run one of these in an interactive terminal:" -ForegroundColor Yellow
    Write-Host "  openclaw onboard --auth-choice openai-codex" -ForegroundColor Yellow
    Write-Host "  openclaw models auth login --provider openai-codex" -ForegroundColor Yellow
    exit 1
  }

  Write-Error "OpenClaw smoke check failed with unexpected error:`n$probeText"
  exit 1
}

Write-Host "OpenClaw local auth check passed." -ForegroundColor Green
