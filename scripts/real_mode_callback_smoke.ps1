# Verify the "real mode" integration flow:
# 1) build stops at PR_OPENED
# 2) signed callback promotes to PREVIEW_READY
#
# Preconditions:
# - Stack is running
# - MOCK_MODE=false in `.env`
# - INTEGRATION_WEBHOOK_SECRET set in `.env`
#
# Usage:
# powershell -ExecutionPolicy Bypass -File .\scripts\real_mode_callback_smoke.ps1 -Secret "dev-webhook-secret"

param(
  [Parameter(Mandatory = $true)]
  [string]$Secret,
  [string]$BaseUrl = "http://localhost:8000",
  [int]$PollSeconds = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ApiToken = ""
$AuthMode = ""
$AuthHeaderEmail = "X-Forwarded-Email"
$AuthHeaderGroups = "X-Forwarded-Groups"
$AuthEmail = "real-mode-smoke@example.local"
$AuthGroups = "engineering,admins"

if (Test-Path ".env") {
  $tokenLine = Select-String -Path ".env" -Pattern '^API_AUTH_TOKEN=' -ErrorAction SilentlyContinue
  if ($tokenLine) {
    $ApiToken = (($tokenLine.Line -split '=', 2)[1]).Trim()
  }
  $authModeLine = Select-String -Path ".env" -Pattern '^AUTH_MODE=' -ErrorAction SilentlyContinue
  if ($authModeLine) {
    $AuthMode = (($authModeLine.Line -split '=', 2)[1]).Trim().ToLowerInvariant()
  }
  $emailHeaderLine = Select-String -Path ".env" -Pattern '^AUTH_HEADER_EMAIL=' -ErrorAction SilentlyContinue
  if ($emailHeaderLine) {
    $AuthHeaderEmail = (($emailHeaderLine.Line -split '=', 2)[1]).Trim()
  }
  $groupHeaderLine = Select-String -Path ".env" -Pattern '^AUTH_HEADER_GROUPS=' -ErrorAction SilentlyContinue
  if ($groupHeaderLine) {
    $AuthHeaderGroups = (($groupHeaderLine.Line -split '=', 2)[1]).Trim()
  }
}

function New-AuthHeaders {
  $headers = @{}
  if ($ApiToken) {
    $headers["X-FF-Token"] = $ApiToken
  }
  if ($AuthMode -eq "edge_sso") {
    $headers[$AuthHeaderEmail] = $AuthEmail
    $headers[$AuthHeaderGroups] = $AuthGroups
  }
  return $headers
}

function Assert-True {
  param([bool]$Condition, [string]$Message)
  if (-not $Condition) {
    throw "Assertion failed: $Message"
  }
}

function Invoke-Json {
  param(
    [ValidateSet("GET", "POST")]
    [string]$Method,
    [string]$Url,
    [object]$Body = $null
  )
  if ($null -eq $Body) {
    $headers = New-AuthHeaders
    if ($headers.Count -gt 0) {
      return Invoke-RestMethod -Method $Method -Uri $Url -Headers $headers -TimeoutSec 30
    }
    return Invoke-RestMethod -Method $Method -Uri $Url -TimeoutSec 30
  }
  $json = $Body | ConvertTo-Json -Depth 8
  $headers = New-AuthHeaders
  if ($headers.Count -gt 0) {
    return Invoke-RestMethod -Method $Method -Uri $Url -Body $json -ContentType "application/json" -Headers $headers -TimeoutSec 30
  }
  return Invoke-RestMethod -Method $Method -Uri $Url -Body $json -ContentType "application/json" -TimeoutSec 30
}

function Send-SignedCallback {
  param(
    [string]$FeatureId,
    [string]$Event,
    [string]$PreviewUrl,
    [string]$GithubPrUrl,
    [string]$EventId = ""
  )

  if (-not $EventId) {
    $EventId = [Guid]::NewGuid().ToString()
  }

  $payload = @{
    feature_id = $FeatureId
    event = $Event
    preview_url = $PreviewUrl
    github_pr_url = $GithubPrUrl
    message = "Callback from real_mode_callback_smoke.ps1"
    actor_id = "real-mode-smoke"
    event_id = $EventId
    metadata = @{ source = "real-mode-smoke" }
  }

  $json = $payload | ConvertTo-Json -Depth 8 -Compress
  $timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds().ToString()
  $toSign = "$timestamp.$json"

  $hmac = [System.Security.Cryptography.HMACSHA256]::new([Text.Encoding]::UTF8.GetBytes($Secret))
  $hash = $hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($toSign))
  $hex = [System.BitConverter]::ToString($hash).Replace("-", "").ToLowerInvariant()

  return Invoke-RestMethod `
    -Method Post `
    -Uri "$BaseUrl/api/integrations/execution-callback" `
    -Headers @{
      "X-Feature-Factory-Timestamp" = $timestamp
      "X-Feature-Factory-Signature" = "sha256=$hex"
      "X-Feature-Factory-Event-Id" = $EventId
    } `
    -ContentType "application/json" `
    -Body $json `
    -TimeoutSec 30
}

Write-Host "Checking health..." -ForegroundColor Cyan
$health = Invoke-Json -Method GET -Url "$BaseUrl/health"
Assert-True ($health.ok -eq $true) "health endpoint must return ok=true"

Write-Host "Creating feature request..." -ForegroundColor Cyan
$createBody = @{
  spec = @{
    title = "Real mode callback smoke"
    problem = "Need to validate non-mock callback path"
    business_justification = "Confirms org-grade integration where external runner reports completion."
    implementation_mode = "new_feature"
    source_repos = @()
    proposed_solution = "Trigger via signed callback"
    acceptance_criteria = @("Build reaches PR_OPENED", "Callback promotes to PREVIEW_READY")
    non_goals = @()
    repo = ""
    risk_flags = @()
    links = @()
  }
  requester_user_id = "real-mode-smoke"
}

$feature = Invoke-Json -Method POST -Url "$BaseUrl/api/feature-requests" -Body $createBody
Assert-True ($feature.status -eq "READY_FOR_BUILD") "new feature should be READY_FOR_BUILD"

Write-Host "Starting build..." -ForegroundColor Cyan
$build = Invoke-Json -Method POST -Url "$BaseUrl/api/feature-requests/$($feature.id)/build"
Assert-True ($build.enqueued -eq $true) "build should enqueue"

Write-Host "Waiting for PR_OPENED..." -ForegroundColor Cyan
$deadline = (Get-Date).AddSeconds($PollSeconds)
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Seconds 2
  $feature = Invoke-Json -Method GET -Url "$BaseUrl/api/feature-requests/$($feature.id)"
  if ($feature.status -eq "PR_OPENED") { break }
  if ($feature.status -eq "PREVIEW_READY") {
    throw "Feature reached PREVIEW_READY before callback. This usually means MOCK_MODE=true."
  }
  if ($feature.status -eq "FAILED_BUILD") {
    throw "Build failed: $($feature.last_error)"
  }
}
Assert-True ($feature.status -eq "PR_OPENED") "feature did not reach PR_OPENED in time"

$previewUrl = "http://localhost:8000/preview/real-$($feature.id)"
Write-Host "Sending signed preview_ready callback..." -ForegroundColor Cyan
$feature = Send-SignedCallback `
  -FeatureId $feature.id `
  -Event "preview_ready" `
  -PreviewUrl $previewUrl `
  -GithubPrUrl $feature.github_pr_url

Assert-True ($feature.status -eq "PREVIEW_READY") "callback should move status to PREVIEW_READY"
Assert-True ($feature.preview_url -eq $previewUrl) "preview_url should be updated from callback"

Write-Host "Real-mode callback smoke test passed." -ForegroundColor Green
$feature | ConvertTo-Json -Depth 8
