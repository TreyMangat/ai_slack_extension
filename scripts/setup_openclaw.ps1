# Install/update OpenClaw locally and configure Codex model defaults.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup_openclaw.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup_openclaw.ps1 -Upgrade
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup_openclaw.ps1 -RunAuth

param(
  [switch]$Upgrade,
  [switch]$RunAuth
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Require-Command {
  param([Parameter(Mandatory = $true)][string]$Name)
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "Required command '$Name' was not found in PATH."
  }
}

Require-Command -Name "node"
Require-Command -Name "npm"

$openclawCmd = Get-Command openclaw -ErrorAction SilentlyContinue
if (-not $openclawCmd) {
  Write-Host "OpenClaw not found; installing with npm..." -ForegroundColor Cyan
  npm install -g openclaw@latest
}
elseif ($Upgrade) {
  Write-Host "Upgrading OpenClaw..." -ForegroundColor Cyan
  npm install -g openclaw@latest
}

Require-Command -Name "openclaw"

Write-Host "OpenClaw version:" -ForegroundColor Cyan
openclaw --version

Write-Host "Configuring default coding model..." -ForegroundColor Cyan
openclaw config set agents.defaults.model.primary openai-codex/gpt-5.3-codex | Out-Null
$model = (openclaw config get agents.defaults.model.primary).Trim()
Write-Host "Configured model: $model" -ForegroundColor Green

Write-Host ""
Write-Host "Next step (required once per machine): complete OpenAI Codex OAuth in an interactive terminal." -ForegroundColor Yellow
Write-Host "  openclaw onboard --auth-choice openai-codex" -ForegroundColor Yellow
Write-Host "  # or" -ForegroundColor Yellow
Write-Host "  openclaw models auth login --provider openai-codex" -ForegroundColor Yellow

if ($RunAuth) {
  Write-Host ""
  Write-Host "Starting interactive OAuth flow now..." -ForegroundColor Cyan
  openclaw onboard --auth-choice openai-codex
}

Write-Host ""
Write-Host "Run this after auth to verify readiness:" -ForegroundColor Cyan
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\check_openclaw_setup.ps1" -ForegroundColor Cyan
