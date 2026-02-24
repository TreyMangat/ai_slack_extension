# Validate Slack tokens/scopes needed for PRFactory chat intake.
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
    $key = $matches[1]
    if (-not $envMap.ContainsKey($key)) {
      $envMap[$key] = $matches[2]
    }
  }
}

function Get-EnvValue {
  param([string]$Key, [string]$Default = "")
  if ($envMap.ContainsKey($Key)) {
    return [string]$envMap[$Key]
  }
  return $Default
}

$slackMode = (Get-EnvValue -Key "SLACK_MODE" -Default "socket").Trim().ToLowerInvariant()
$botToken = (Get-EnvValue -Key "SLACK_BOT_TOKEN").Trim()
$appToken = (Get-EnvValue -Key "SLACK_APP_TOKEN").Trim()
$signingSecret = (Get-EnvValue -Key "SLACK_SIGNING_SECRET").Trim()
$appConfigToken = (Get-EnvValue -Key "SLACK_APP_CONFIG_TOKEN").Trim()
$appId = (Get-EnvValue -Key "SLACK_APP_ID").Trim()
$channelRaw = (Get-EnvValue -Key "SLACK_ALLOWED_CHANNELS").Trim()
$channelId = ($channelRaw -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -First 1)

if ([string]::IsNullOrWhiteSpace($botToken)) {
  throw "SLACK_BOT_TOKEN is empty in .env"
}

Write-Host "Checking bot auth..." -ForegroundColor Cyan
$auth = Invoke-RestMethod -Method Post -Uri "https://slack.com/api/auth.test" -Headers @{ Authorization = "Bearer $botToken" }
if (-not $auth.ok) { throw "auth.test failed: $($auth.error)" }
Write-Host "bot auth ok: team=$($auth.team) user=$($auth.user)" -ForegroundColor Green

if ($slackMode -eq "socket") {
  if ([string]::IsNullOrWhiteSpace($appToken)) {
    throw "SLACK_APP_TOKEN is empty in .env (required for SLACK_MODE=socket)"
  }
  Write-Host "Checking app-level socket token..." -ForegroundColor Cyan
  $conn = Invoke-RestMethod -Method Post -Uri "https://slack.com/api/apps.connections.open" -Headers @{ Authorization = "Bearer $appToken" }
  if (-not $conn.ok) { throw "apps.connections.open failed: $($conn.error)" }
  Write-Host "app token ok" -ForegroundColor Green
}
elseif ($slackMode -eq "http") {
  if ([string]::IsNullOrWhiteSpace($signingSecret)) {
    throw "SLACK_SIGNING_SECRET is empty in .env (required for SLACK_MODE=http)"
  }
  Write-Host "HTTP mode detected. Signing secret present." -ForegroundColor Green

  if (-not [string]::IsNullOrWhiteSpace($appConfigToken) -and -not [string]::IsNullOrWhiteSpace($appId)) {
    Write-Host "Checking Slack manifest config token..." -ForegroundColor Cyan
    $manifestBody = @{ app_id = $appId } | ConvertTo-Json -Compress
    $manifest = Invoke-RestMethod -Method Post -Uri "https://slack.com/api/apps.manifest.export" -Headers @{
      Authorization = "Bearer $appConfigToken"
      "Content-Type" = "application/json; charset=utf-8"
    } -Body $manifestBody
    if (-not $manifest.ok) {
      throw "apps.manifest.export failed: $($manifest.error)"
    }
    Write-Host "app config token ok (manifest export succeeded)." -ForegroundColor Green
  }
  else {
    Write-Warning "SLACK_APP_ID/SLACK_APP_CONFIG_TOKEN missing. Auto manifest sync checks skipped."
  }
}
else {
  throw "Unsupported SLACK_MODE '$slackMode'. Expected 'socket' or 'http'."
}

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
