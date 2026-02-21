# Generate a deterministic UI evidence manifest for CI.
# Usage:
#   pwsh -File ./scripts/ui_evidence.ps1 -BaseUrl http://localhost:8000 -OutputPath artifacts/ui-evidence-manifest.json

param(
  [string]$BaseUrl = "http://localhost:8000",
  [string]$OutputPath = "artifacts/ui-evidence-manifest.json",
  [string]$HeadSha = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ApiToken = ""
$AuthMode = ""
$AuthHeaderEmail = "X-Forwarded-Email"
$AuthHeaderGroups = "X-Forwarded-Groups"
$AuthEmail = "ui-evidence@example.local"
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

function Assert-True {
  param(
    [bool]$Condition,
    [string]$Message
  )
  if (-not $Condition) {
    throw "Assertion failed: $Message"
  }
}

if (-not $HeadSha) {
  if ($env:GITHUB_SHA) {
    $HeadSha = $env:GITHUB_SHA
  } else {
    try {
      $HeadSha = (git rev-parse HEAD).Trim()
    } catch {
      $HeadSha = "unknown"
    }
  }
}

$flows = @()

Write-Host "Validating home page render..." -ForegroundColor Cyan
$homeHeaders = New-AuthHeaders
if ($homeHeaders.Count -gt 0) {
  $homePage = Invoke-WebRequest -Uri "$BaseUrl/" -Headers $homeHeaders -TimeoutSec 30 -UseBasicParsing
} else {
  $homePage = Invoke-WebRequest -Uri "$BaseUrl/" -TimeoutSec 30 -UseBasicParsing
}
Assert-True ($homePage.StatusCode -eq 200) "Home page must return 200"
Assert-True ($homePage.Content -match "Feature Factory") "Home page content should include 'Feature Factory'"
$flows += @{
  id = "home_page_renders"
  status = "passed"
  assertions = @("GET / returned 200", "Home page contains product title")
}

Write-Host "Creating feature request through API..." -ForegroundColor Cyan
$spec = @{
  spec = @{
    title = "UI evidence flow request"
    problem = "Need deterministic UI evidence"
    business_justification = "CI should prove web UI routes render."
    implementation_mode = "new_feature"
    source_repos = @()
    proposed_solution = "Render feature detail page for created request"
    acceptance_criteria = @("Feature detail page loads")
    non_goals = @()
    repo = ""
    risk_flags = @()
    links = @()
  }
  requester_user_id = "ui-evidence"
}
$feature = Invoke-Json -Method POST -Url "$BaseUrl/api/feature-requests" -Body $spec
Assert-True (-not [string]::IsNullOrWhiteSpace($feature.id)) "Feature creation should return an id"

Write-Host "Validating feature detail UI render..." -ForegroundColor Cyan
$detailHeaders = New-AuthHeaders
if ($detailHeaders.Count -gt 0) {
  $detail = Invoke-WebRequest -Uri "$BaseUrl/features/$($feature.id)" -Headers $detailHeaders -TimeoutSec 30 -UseBasicParsing
} else {
  $detail = Invoke-WebRequest -Uri "$BaseUrl/features/$($feature.id)" -TimeoutSec 30 -UseBasicParsing
}
Assert-True ($detail.StatusCode -eq 200) "Feature detail page must return 200"
Assert-True ($detail.Content -match "UI evidence flow request") "Feature detail page should contain feature title"
$flows += @{
  id = "feature_detail_renders"
  status = "passed"
  assertions = @("GET /features/{id} returned 200", "Feature title rendered in detail page")
}

$flows += @{
  id = "create_request_then_open_feature_ui"
  status = "passed"
  assertions = @("POST /api/feature-requests succeeded", "Detail page for created request rendered")
}

$manifest = @{
  schema_version = 1
  generated_at = (Get-Date).ToUniversalTime().ToString("o")
  status = "ok"
  head_sha = $HeadSha
  entrypoint = $BaseUrl
  identity_context = @{
    mode = "anonymous"
  }
  flows = $flows
}

$outDir = Split-Path -Parent $OutputPath
if ($outDir -and -not (Test-Path $outDir)) {
  New-Item -Path $outDir -ItemType Directory -Force | Out-Null
}

$manifest | ConvertTo-Json -Depth 8 | Set-Content -Path $OutputPath -Encoding UTF8
Write-Host "UI evidence manifest written to $OutputPath" -ForegroundColor Green
