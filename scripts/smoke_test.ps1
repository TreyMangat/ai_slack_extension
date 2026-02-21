# End-to-end smoke test for the local Feature Factory stack.
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\smoke_test.ps1

param(
  [string]$BaseUrl = "http://localhost:8000",
  [int]$PollSeconds = 45,
  [string]$Approver = "smoke-test"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ApiToken = ""
$AuthMode = ""
$MockMode = $true
$AuthHeaderEmail = "X-Forwarded-Email"
$AuthHeaderGroups = "X-Forwarded-Groups"
$AuthEmail = "smoke-test@example.local"
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
  $mockModeLine = Select-String -Path ".env" -Pattern '^MOCK_MODE=' -ErrorAction SilentlyContinue
  if ($mockModeLine) {
    $mockValue = (($mockModeLine.Line -split '=', 2)[1]).Trim().ToLowerInvariant()
    $MockMode = ($mockValue -eq "true" -or $mockValue -eq "1" -or $mockValue -eq "yes")
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
  param(
    [bool]$Condition,
    [string]$Message
  )

  if (-not $Condition) {
    throw "Assertion failed: $Message"
  }
}

function Invoke-Json {
  param(
    [ValidateSet("GET", "POST", "PATCH")]
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

Write-Host "Checking API health..." -ForegroundColor Cyan
$health = Invoke-Json -Method GET -Url "$BaseUrl/health"
Assert-True ($health.ok -eq $true) "Health endpoint did not return ok=true"

# If reviewer allowlist is configured locally, prefer the first reviewer user ID.
if ($Approver -eq "smoke-test" -and (Test-Path ".env")) {
  $reviewerLine = Select-String -Path ".env" -Pattern '^REVIEWER_ALLOWED_USERS=' -ErrorAction SilentlyContinue
  if ($reviewerLine) {
    $raw = ($reviewerLine.Line -split '=', 2)[1].Trim()
    if ($raw) {
      $firstReviewer = ($raw -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -First 1)
      if ($firstReviewer) {
        $Approver = $firstReviewer
      }
    }
  }
}

Write-Host "Creating valid feature request..." -ForegroundColor Cyan
$validReq = @{
  spec = @{
    title = "Smoke test export invoices"
    problem = "Need an export"
    business_justification = "Finance team needs weekly reconciliation export to close books faster."
    implementation_mode = "new_feature"
    source_repos = @()
    proposed_solution = "Add export"
    acceptance_criteria = @("Can export invoices", "Export respects filters")
    non_goals = @("No redesign")
    repo = ""
    risk_flags = @()
    links = @()
  }
  requester_user_id = "smoke-test"
}
$validFeature = Invoke-Json -Method POST -Url "$BaseUrl/api/feature-requests" -Body $validReq

Assert-True ($validFeature.status -eq "READY_FOR_BUILD") "New valid request should be READY_FOR_BUILD"
Assert-True ($validFeature.spec._validation.is_valid -eq $true) "Validation metadata missing or invalid"

Write-Host "Revalidating valid feature..." -ForegroundColor Cyan
$validFeature = Invoke-Json -Method POST -Url "$BaseUrl/api/feature-requests/$($validFeature.id)/revalidate"
Assert-True ($validFeature.status -eq "READY_FOR_BUILD") "Revalidation should keep READY_FOR_BUILD"

Write-Host "Starting build..." -ForegroundColor Cyan
$null = Invoke-Json -Method POST -Url "$BaseUrl/api/feature-requests/$($validFeature.id)/build"

if ($MockMode) {
  Write-Host "Polling for PREVIEW_READY..." -ForegroundColor Cyan
}
else {
  Write-Host "Polling for PR_OPENED (or PREVIEW_READY if callback arrives)..." -ForegroundColor Cyan
}
$deadline = (Get-Date).AddSeconds($PollSeconds)
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Seconds 2
  $validFeature = Invoke-Json -Method GET -Url "$BaseUrl/api/feature-requests/$($validFeature.id)"
  if ($MockMode) {
    if ($validFeature.status -eq "PREVIEW_READY") {
      break
    }
  }
  else {
    if ($validFeature.status -eq "PR_OPENED" -or $validFeature.status -eq "PREVIEW_READY") {
      break
    }
  }
  if ($validFeature.status -eq "FAILED_BUILD") {
    throw "Build failed unexpectedly: $($validFeature.last_error)"
  }
}

if ($MockMode) {
  Assert-True ($validFeature.status -eq "PREVIEW_READY") "Feature did not reach PREVIEW_READY in time"
}
else {
  Assert-True (($validFeature.status -eq "PR_OPENED") -or ($validFeature.status -eq "PREVIEW_READY")) "Feature did not reach PR_OPENED/PREVIEW_READY in time"
}
Assert-True (-not [string]::IsNullOrWhiteSpace($validFeature.github_issue_url)) "github_issue_url missing"
if ($MockMode) {
  Assert-True (-not [string]::IsNullOrWhiteSpace($validFeature.github_pr_url)) "github_pr_url missing"
  Assert-True (-not [string]::IsNullOrWhiteSpace($validFeature.preview_url)) "preview_url missing"

  Write-Host "Approving feature..." -ForegroundColor Cyan
  $approved = Invoke-Json -Method POST -Url "$BaseUrl/api/feature-requests/$($validFeature.id)/approve?approver=$Approver"
  Assert-True ($approved.status -eq "PRODUCT_APPROVED") "Approve should move to PRODUCT_APPROVED"
}
else {
  Write-Host "Skipping approval assertion in non-mock mode (requires preview_ready callback)." -ForegroundColor Yellow
}

Write-Host "Creating draft feature for clarification flow..." -ForegroundColor Cyan
$draftReq = @{
  spec = @{
    title = "Draft request for clarifications"
    problem = "Need a rough draft flow"
    business_justification = ""
    implementation_mode = "new_feature"
    source_repos = @()
    proposed_solution = ""
    acceptance_criteria = @()
    non_goals = @()
    repo = ""
    risk_flags = @()
    links = @()
  }
  requester_user_id = "smoke-test"
}
$draftFeature = Invoke-Json -Method POST -Url "$BaseUrl/api/feature-requests" -Body $draftReq
Assert-True ($draftFeature.status -eq "NEEDS_INFO") "Draft feature should start in NEEDS_INFO"
Assert-True ($draftFeature.spec._validation.missing -contains "business_justification") "Draft should flag missing why"
Assert-True ($draftFeature.spec._validation.missing -contains "acceptance_criteria") "Draft should flag missing acceptance criteria"

Write-Host "Updating draft via PATCH /spec..." -ForegroundColor Cyan
$patchReq = @{
  spec = @{
    business_justification = "Operations team needs this before quarter-end planning."
    acceptance_criteria = @("Draft can be promoted to READY_FOR_BUILD after clarification")
  }
  actor_type = "smoke"
  actor_id = "smoke-test"
  message = "Clarifications added in smoke test"
}
$draftFeature = Invoke-Json -Method PATCH -Url "$BaseUrl/api/feature-requests/$($draftFeature.id)/spec" -Body $patchReq
Assert-True ($draftFeature.status -eq "READY_FOR_BUILD") "Patch update should move draft to READY_FOR_BUILD"

Write-Host "Creating reuse-mode feature with local source snapshot..." -ForegroundColor Cyan
$reuseReq = @{
  spec = @{
    title = "Reuse mode local snapshot"
    problem = "Need to verify local reference copying"
    business_justification = "Teams need safe repo reuse without modifying source repos."
    implementation_mode = "reuse_existing"
    source_repos = @("/app/app/samples/reuse_seed")
    proposed_solution = "Copy local fixture repo into isolated workspace"
    acceptance_criteria = @("Workspace prepares at least one local copy reference")
    non_goals = @()
    repo = ""
    risk_flags = @()
    links = @()
  }
  requester_user_id = "smoke-test"
}
$reuseFeature = Invoke-Json -Method POST -Url "$BaseUrl/api/feature-requests" -Body $reuseReq
Assert-True ($reuseFeature.status -eq "READY_FOR_BUILD") "Reuse request should be READY_FOR_BUILD"

Write-Host "Starting reuse build..." -ForegroundColor Cyan
$null = Invoke-Json -Method POST -Url "$BaseUrl/api/feature-requests/$($reuseFeature.id)/build"

if ($MockMode) {
  Write-Host "Polling reuse feature for PREVIEW_READY..." -ForegroundColor Cyan
}
else {
  Write-Host "Polling reuse feature for PR_OPENED/PREVIEW_READY..." -ForegroundColor Cyan
}
$reuseDeadline = (Get-Date).AddSeconds($PollSeconds)
while ((Get-Date) -lt $reuseDeadline) {
  Start-Sleep -Seconds 2
  $reuseFeature = Invoke-Json -Method GET -Url "$BaseUrl/api/feature-requests/$($reuseFeature.id)"
  if ($MockMode) {
    if ($reuseFeature.status -eq "PREVIEW_READY") {
      break
    }
  }
  else {
    if ($reuseFeature.status -eq "PR_OPENED" -or $reuseFeature.status -eq "PREVIEW_READY") {
      break
    }
  }
  if ($reuseFeature.status -eq "FAILED_BUILD") {
    throw "Reuse build failed unexpectedly: $($reuseFeature.last_error)"
  }
}
if ($MockMode) {
  Assert-True ($reuseFeature.status -eq "PREVIEW_READY") "Reuse feature did not reach PREVIEW_READY in time"
}
else {
  Assert-True (($reuseFeature.status -eq "PR_OPENED") -or ($reuseFeature.status -eq "PREVIEW_READY")) "Reuse feature did not reach PR_OPENED/PREVIEW_READY in time"
}
$workspaceEvent = ($reuseFeature.events | Where-Object { $_.event_type -eq "workspace_prepared" } | Select-Object -Last 1)
Assert-True ($null -ne $workspaceEvent) "workspace_prepared event should exist"
$preparedRefs = @($workspaceEvent.data.prepared_references)
Assert-True ($preparedRefs.Count -ge 1) "workspace_prepared must include at least one reference entry"
Assert-True ($preparedRefs[0].status -eq "prepared") "first prepared reference should have status=prepared"
Assert-True ($preparedRefs[0].method -eq "local_copy") "first prepared reference should use local_copy method"

Write-Host "Creating invalid feature request..." -ForegroundColor Cyan
$invalidReq = @{
  spec = @{
    title = "Smoke test invalid request"
    problem = "Missing acceptance criteria"
    business_justification = "Need this eventually but criteria are intentionally omitted for validation."
    implementation_mode = "new_feature"
    source_repos = @()
    proposed_solution = ""
    acceptance_criteria = @()
    non_goals = @()
    repo = ""
    risk_flags = @()
    links = @()
  }
  requester_user_id = "smoke-test"
}
$invalidFeature = Invoke-Json -Method POST -Url "$BaseUrl/api/feature-requests" -Body $invalidReq

Assert-True ($invalidFeature.status -eq "NEEDS_INFO") "Invalid request should be NEEDS_INFO"
Assert-True ($invalidFeature.spec._validation.missing -contains "acceptance_criteria") "Missing acceptance criteria not detected"

Write-Host "Creating reuse-mode request without source repos..." -ForegroundColor Cyan
$reuseInvalidReq = @{
  spec = @{
    title = "Reuse mode missing repos"
    problem = "Need reuse mode validation"
    business_justification = "Teams need guardrails when reusing existing repos."
    implementation_mode = "reuse_existing"
    source_repos = @()
    proposed_solution = ""
    acceptance_criteria = @("Should fail validation without source repos")
    non_goals = @()
    repo = ""
    risk_flags = @()
    links = @()
  }
  requester_user_id = "smoke-test"
}
$reuseInvalidFeature = Invoke-Json -Method POST -Url "$BaseUrl/api/feature-requests" -Body $reuseInvalidReq
Assert-True ($reuseInvalidFeature.status -eq "NEEDS_INFO") "Reuse request without source_repos should be NEEDS_INFO"
Assert-True ($reuseInvalidFeature.spec._validation.missing -contains "source_repos") "Missing source_repos not detected"

Write-Host "Verifying build rejects invalid state..." -ForegroundColor Cyan
$buildRejected = $false
try {
  $null = Invoke-Json -Method POST -Url "$BaseUrl/api/feature-requests/$($invalidFeature.id)/build"
}
catch {
  $buildRejected = $true
}
Assert-True $buildRejected "Build should fail for NEEDS_INFO status"

Write-Host "Smoke test passed." -ForegroundColor Green
