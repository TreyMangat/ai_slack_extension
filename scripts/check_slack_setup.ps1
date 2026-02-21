# Validate Slack tokens/scopes needed for chat-based intake.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\check_slack_setup.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (!(Test-Path .env)) {
  throw ".env not found in repo root."
}

$envMap = @{}
Get-Content .env | ForEach-Object {
  if ($_ -match '^\s*#') { return }
  if ($_ -match '^\s*([^=\s]+)\s*=\s*(.*)\s*$') {
    $envMap[$matches[1]] = $matches[2]
  }
}

$botToken = $envMap["SLACK_BOT_TOKEN"]
$appToken = $envMap["SLACK_APP_TOKEN"]
$channelRaw = $envMap["SLACK_ALLOWED_CHANNELS"]
$channelId = ($channelRaw -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -First 1)

if ([string]::IsNullOrWhiteSpace($botToken)) {
  throw "SLACK_BOT_TOKEN is empty in .env"
}
if ([string]::IsNullOrWhiteSpace($appToken)) {
  throw "SLACK_APP_TOKEN is empty in .env"
}

Write-Host "Checking bot auth..." -ForegroundColor Cyan
$auth = Invoke-RestMethod -Method Post -Uri "https://slack.com/api/auth.test" -Headers @{ Authorization = "Bearer $botToken" }
if (-not $auth.ok) { throw "auth.test failed: $($auth.error)" }
Write-Host "bot auth ok: team=$($auth.team) user=$($auth.user)" -ForegroundColor Green

Write-Host "Checking app-level socket token..." -ForegroundColor Cyan
$conn = Invoke-RestMethod -Method Post -Uri "https://slack.com/api/apps.connections.open" -Headers @{ Authorization = "Bearer $appToken" }
if (-not $conn.ok) { throw "apps.connections.open failed: $($conn.error)" }
Write-Host "app token ok" -ForegroundColor Green

if ([string]::IsNullOrWhiteSpace($channelId)) {
  Write-Warning "SLACK_ALLOWED_CHANNELS is empty; skipping conversations.history scope check."
  exit 0
}

Write-Host "Checking message-history scopes via conversations.history..." -ForegroundColor Cyan
$hist = Invoke-RestMethod -Method Post -Uri "https://slack.com/api/conversations.history" -Headers @{ Authorization = "Bearer $botToken" } -Body @{ channel = $channelId; limit = 1 }
if (-not $hist.ok) {
  Write-Host "conversations.history failed: $($hist.error)" -ForegroundColor Yellow
  if ($hist.error -eq "missing_scope") {
    Write-Host "needed scopes: $($hist.needed)" -ForegroundColor Yellow
    Write-Host "provided scopes: $($hist.provided)" -ForegroundColor Yellow
  }
  elseif ($hist.error -eq "not_in_channel") {
    Write-Host "Bot is not in the target channel. Invite the bot or add channels:join scope." -ForegroundColor Yellow
  }
  exit 1
}
Write-Host "history scopes ok" -ForegroundColor Green
