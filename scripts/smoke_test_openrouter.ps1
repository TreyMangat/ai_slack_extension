# OpenRouter-specific smoke test for the local PRFactory stack.
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\smoke_test_openrouter.ps1

param(
  [string]$BaseUrl = "http://localhost:8000",
  [int]$PollSeconds = 180
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ApiToken = ""
$AuthMode = ""
$AuthHeaderEmail = "X-Forwarded-Email"
$AuthHeaderGroups = "X-Forwarded-Groups"
$AuthEmail = "openrouter-smoke@example.local"
$AuthGroups = "engineering,admins"
$OpenRouterApiKey = [string]$env:OPENROUTER_API_KEY
$RepoSlug = ""

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
  if ([string]::IsNullOrWhiteSpace($OpenRouterApiKey)) {
    $openrouterLine = Select-String -Path ".env" -Pattern '^OPENROUTER_API_KEY=' -ErrorAction SilentlyContinue
    if ($openrouterLine) {
      $OpenRouterApiKey = (($openrouterLine.Line -split '=', 2)[1]).Trim()
    }
  }
  $ownerLine = Select-String -Path ".env" -Pattern '^GITHUB_REPO_OWNER=' -ErrorAction SilentlyContinue
  $repoLine = Select-String -Path ".env" -Pattern '^GITHUB_REPO_NAME=' -ErrorAction SilentlyContinue
  if ($ownerLine -and $repoLine) {
    $owner = (($ownerLine.Line -split '=', 2)[1]).Trim()
    $repo = (($repoLine.Line -split '=', 2)[1]).Trim()
    if (-not [string]::IsNullOrWhiteSpace($owner) -and -not [string]::IsNullOrWhiteSpace($repo)) {
      $RepoSlug = "$owner/$repo"
    }
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
    [ValidateSet("GET", "POST")]
    [string]$Method,
    [string]$Url,
    [object]$Body = $null
  )

  $headers = New-AuthHeaders
  if ($null -eq $Body) {
    if ($headers.Count -gt 0) {
      return Invoke-RestMethod -Method $Method -Uri $Url -Headers $headers -TimeoutSec 30
    }
    return Invoke-RestMethod -Method $Method -Uri $Url -TimeoutSec 30
  }

  $json = $Body | ConvertTo-Json -Depth 8
  if ($headers.Count -gt 0) {
    return Invoke-RestMethod -Method $Method -Uri $Url -Headers $headers -Body $json -ContentType "application/json" -TimeoutSec 30
  }
  return Invoke-RestMethod -Method $Method -Uri $Url -Body $json -ContentType "application/json" -TimeoutSec 30
}

try {
  if ([string]::IsNullOrWhiteSpace($OpenRouterApiKey)) {
    throw "OPENROUTER_API_KEY is not set. Export it in your environment or add it to .env before running this smoke test."
  }

  Write-Host "Checking OpenRouter runtime configuration..." -ForegroundColor Cyan
  $runtime = Invoke-Json -Method GET -Url "$BaseUrl/health/runtime"
  Assert-True ($runtime.ok -eq $true) "/health/runtime did not return ok=true"
  Assert-True ($runtime.openrouter.configured -eq $true) "OpenRouter is not reported as configured"

  $miniModel = [string]$runtime.openrouter.mini_model
  $frontierModel = [string]$runtime.openrouter.frontier_model
  $runtimeMockMode = [bool]$runtime.runtime.mock_mode
  if (-not $runtimeMockMode -and $PollSeconds -lt 300) {
    $PollSeconds = 300
  }
  if (-not $runtimeMockMode -and [string]::IsNullOrWhiteSpace($RepoSlug)) {
    throw "Non-mock OpenRouter smoke test requires GITHUB_REPO_OWNER/GITHUB_REPO_NAME in .env so the build has a target repo."
  }

  $problemText = "I want to add a dark mode toggle to the settings page in the main repo"
  if ([string]::IsNullOrWhiteSpace($RepoSlug)) {
    Write-Warning "GITHUB_REPO_OWNER/GITHUB_REPO_NAME are not configured in .env; using an empty repo slug (works in mock mode only)."
  }

  Write-Host "Creating feature request with natural-language spec text..." -ForegroundColor Cyan
  $createBody = @{
    spec = @{
      title = "OpenRouter smoke: dark mode toggle"
      problem = $problemText
      business_justification = "This validates OpenRouter-backed spec analysis and cost tracking end-to-end."
      implementation_mode = "new_feature"
      source_repos = @()
      proposed_solution = "Add a toggle and persist the user's preference."
      acceptance_criteria = @(
        "Settings page has a dark mode toggle",
        "Preference persists across page reloads"
      )
      non_goals = @("No visual redesign outside theme switching")
      repo = $RepoSlug
      risk_flags = @()
      links = @()
    }
    requester_user_id = "openrouter-smoke"
  }
  $feature = Invoke-Json -Method POST -Url "$BaseUrl/api/feature-requests" -Body $createBody

  Write-Host "Checking LLM spec analysis..." -ForegroundColor Cyan
  $feature = Invoke-Json -Method GET -Url "$BaseUrl/api/feature-requests/$($feature.id)"
  Assert-True ($null -ne $feature.llm_spec_analysis) "llm_spec_analysis was not populated"

  Write-Host "Starting build..." -ForegroundColor Cyan
  $null = Invoke-Json -Method POST -Url "$BaseUrl/api/feature-requests/$($feature.id)/build"

  $statusHistory = New-Object System.Collections.Generic.List[string]
  $statusHistory.Add([string]$feature.status) | Out-Null

  Write-Host "Polling build status..." -ForegroundColor Cyan
  $deadline = (Get-Date).AddSeconds($PollSeconds)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    $feature = Invoke-Json -Method GET -Url "$BaseUrl/api/feature-requests/$($feature.id)"
    $currentStatus = [string]$feature.status
    if ($statusHistory.Count -eq 0 -or $statusHistory[$statusHistory.Count - 1] -ne $currentStatus) {
      $statusHistory.Add($currentStatus) | Out-Null
    }
    if ($currentStatus -ne "BUILDING") {
      break
    }
  }

  Assert-True ($feature.status -ne "BUILDING") "Feature remained BUILDING after $PollSeconds seconds"

  $llmCostEvents = @($feature.events | Where-Object { $_.event_type -eq "llm_cost" })
  Assert-True ($llmCostEvents.Count -ge 1) "Expected at least one llm_cost event"

  $models = New-Object System.Collections.Generic.List[string]
  $totalCost = 0.0
  foreach ($event in $llmCostEvents) {
    $data = $event.data
    if ($null -ne $data) {
      $model = [string]$data.model
      if (-not [string]::IsNullOrWhiteSpace($model) -and -not $models.Contains($model)) {
        $models.Add($model) | Out-Null
      }
      try {
        $totalCost += [double]($data.cost_usd)
      }
      catch {
        $totalCost += 0.0
      }
    }
  }

  Write-Host ""
  Write-Host "OpenRouter smoke test summary" -ForegroundColor Green
  Write-Host "Feature id: $($feature.id)"
  Write-Host "Final status: $($feature.status)"
  Write-Host "Status progression: $([string]::Join(' -> ', $statusHistory))"
  Write-Host "Mini model: $miniModel"
  Write-Host "Frontier model: $frontierModel"
  Write-Host "Models used: $([string]::Join(', ', $models))"
  Write-Host ("Total cost: ${0:N4}" -f $totalCost)
  Write-Host "llm_cost events: $($llmCostEvents.Count)"
}
catch {
  Write-Error $_.Exception.Message
  exit 1
}
