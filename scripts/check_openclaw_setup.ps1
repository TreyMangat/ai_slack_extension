# Validate local OpenClaw readiness for this repo.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\check_openclaw_setup.ps1

param(
  [string]$ExpectedModel = "openai-codex/gpt-5.3-codex",
  [switch]$CheckContainer
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

if ($CheckContainer) {
  Write-Host "Checking OpenClaw inside worker container..." -ForegroundColor Cyan
  $composeArgs = @("-f", "docker-compose.yml", "-f", "docker-compose.dev.yml")

  docker compose @composeArgs exec -T worker openclaw --version
  if ($LASTEXITCODE -ne 0) {
    throw "OpenClaw CLI is not available in worker container."
  }

  docker compose @composeArgs exec -T worker openclaw agent --local --agent main --message "Reply with READY only." --json | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "OpenClaw auth smoke failed in worker container. Run scripts/sync_openclaw_auth.ps1 and restart compose."
  }

  Write-Host "OpenClaw container auth check passed." -ForegroundColor Green
}
