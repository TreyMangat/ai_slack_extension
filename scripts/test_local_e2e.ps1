[CmdletBinding()]
param(
    [string]$BaseUrl = "http://localhost:8000"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Read-EnvMap {
    $envMap = @{}
    if (-not (Test-Path ".env")) {
        return $envMap
    }

    Get-Content ".env" | ForEach-Object {
        if ($_ -match '^\s*#') {
            return
        }
        if ($_ -match '^\s*([^=\s]+)\s*=\s*(.*)\s*$') {
            $key = $matches[1]
            $value = $matches[2]
            if (-not $envMap.ContainsKey($key)) {
                $envMap[$key] = $value
            }
        }
    }
    return $envMap
}

function Get-EnvValue {
    param(
        [hashtable]$EnvMap,
        [string]$Key,
        [string]$Default = ""
    )

    if ($EnvMap.ContainsKey($Key)) {
        return [string]$EnvMap[$Key]
    }
    return $Default
}

function New-AuthHeaders {
    param([hashtable]$EnvMap)

    $headers = @{}
    $apiToken = (Get-EnvValue -EnvMap $EnvMap -Key "API_AUTH_TOKEN").Trim()
    if ($apiToken) {
        $headers["X-FF-Token"] = $apiToken
    }

    $authMode = (Get-EnvValue -EnvMap $EnvMap -Key "AUTH_MODE").Trim().ToLowerInvariant()
    if ($authMode -eq "edge_sso") {
        $emailHeader = (Get-EnvValue -EnvMap $EnvMap -Key "AUTH_HEADER_EMAIL" -Default "X-Forwarded-Email").Trim()
        $groupsHeader = (Get-EnvValue -EnvMap $EnvMap -Key "AUTH_HEADER_GROUPS" -Default "X-Forwarded-Groups").Trim()
        $headers[$emailHeader] = "local-e2e@example.local"
        $headers[$groupsHeader] = "engineering,admins"
    }

    return $headers
}

function Get-ErrorResponseText {
    param([System.Management.Automation.ErrorRecord]$ErrorRecord)

    try {
        $response = $ErrorRecord.Exception.Response
        if ($null -eq $response) {
            return ""
        }
        $stream = $response.GetResponseStream()
        if ($null -eq $stream) {
            return ""
        }
        $reader = [System.IO.StreamReader]::new($stream)
        return $reader.ReadToEnd()
    }
    catch {
        return ""
    }
}

function Invoke-Api {
    param(
        [ValidateSet("GET", "POST", "PATCH")]
        [string]$Method,
        [string]$Url,
        [hashtable]$Headers,
        [object]$Body = $null,
        [int]$TimeoutSec = 30
    )

    Write-Verbose "$Method $Url"
    if ($null -eq $Body) {
        if ($Headers.Count -gt 0) {
            return Invoke-RestMethod -Method $Method -Uri $Url -Headers $Headers -TimeoutSec $TimeoutSec
        }
        return Invoke-RestMethod -Method $Method -Uri $Url -TimeoutSec $TimeoutSec
    }

    $json = $Body | ConvertTo-Json -Depth 8
    if ($Headers.Count -gt 0) {
        return Invoke-RestMethod -Method $Method -Uri $Url -Headers $Headers -Body $json -ContentType "application/json" -TimeoutSec $TimeoutSec
    }
    return Invoke-RestMethod -Method $Method -Uri $Url -Body $json -ContentType "application/json" -TimeoutSec $TimeoutSec
}

function Add-StatusProgression {
    param(
        [System.Collections.Generic.List[string]]$Statuses,
        [string]$Status
    )

    $normalized = [string]$Status
    if (-not $normalized) {
        return
    }
    if ($Statuses.Count -eq 0 -or $Statuses[$Statuses.Count - 1] -ne $normalized) {
        [void]$Statuses.Add($normalized)
    }
}

$envMap = Read-EnvMap
$headers = New-AuthHeaders -EnvMap $envMap
$statusProgression = [System.Collections.Generic.List[string]]::new()

Write-Host "=== PRFactory Local E2E Test ===" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/8] Health check..." -NoNewline
try {
    $health = Invoke-Api -Method GET -Url "$BaseUrl/health" -Headers $headers -TimeoutSec 5
    if (-not $health.ok) {
        throw "Health response did not include ok=true"
    }
    Write-Host " OK" -ForegroundColor Green
}
catch {
    Write-Host " FAILED - Is the stack running? Try: docker compose up" -ForegroundColor Red
    Write-Host "    Error: $($_.Exception.Message)"
    $details = Get-ErrorResponseText -ErrorRecord $_
    if ($details) {
        Write-Host "    Response: $details"
    }
    exit 1
}

Write-Host "[2/8] Runtime config..." -NoNewline
try {
    $runtime = Invoke-Api -Method GET -Url "$BaseUrl/health/runtime" -Headers $headers -TimeoutSec 5
    $runtimeMockMode = [bool]($runtime.runtime.mock_mode)
    if (-not $runtimeMockMode) {
        Write-Host " FAILED" -ForegroundColor Red
        Write-Host "    This harness is for local mock mode only. Set MOCK_MODE=true and restart the stack."
        exit 1
    }

    if ($runtime.openrouter) {
        $configured = [bool]$runtime.openrouter.configured
        Write-Host " OpenRouter configured=$configured | mock_mode=$runtimeMockMode" -ForegroundColor $(if ($configured) { "Green" } else { "Yellow" })
        if ($configured) {
            Write-Host "       Mini: $($runtime.openrouter.mini_model)"
            Write-Host "       Frontier: $($runtime.openrouter.frontier_model)"
        }
    }
    else {
        Write-Host " No OpenRouter block in runtime response" -ForegroundColor Yellow
    }
}
catch {
    Write-Host " FAILED" -ForegroundColor Red
    Write-Host "    Error: $($_.Exception.Message)"
    $details = Get-ErrorResponseText -ErrorRecord $_
    if ($details) {
        Write-Host "    Response: $details"
    }
    exit 1
}

$featureTitle = "Local E2E Dark Mode $([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"
$createPayload = @{
    spec = @{
        title = $featureTitle
        problem = "I want to add a dark mode toggle to the settings page in the main repo."
        business_justification = "Users need a low-light experience during longer sessions."
        implementation_mode = "new_feature"
        source_repos = @()
        proposed_solution = "Add a persisted settings toggle and apply the selected theme across the app."
        acceptance_criteria = @(
            "A dark mode toggle appears in settings",
            "The preference persists across reloads"
        )
        non_goals = @("No full visual redesign")
        repo = ""
        base_branch = ""
        risk_flags = @()
        links = @()
    }
    requester_user_id = "local-e2e"
}

Write-Host "[3/8] Creating feature request..." -NoNewline
try {
    $feature = Invoke-Api -Method POST -Url "$BaseUrl/api/feature-requests" -Headers $headers -Body $createPayload -TimeoutSec 10
    $featureId = [string]$feature.id
    Add-StatusProgression -Statuses $statusProgression -Status ([string]$feature.status)
    Write-Host " Created: $featureId (status: $($feature.status))" -ForegroundColor Green
}
catch {
    Write-Host " FAILED" -ForegroundColor Red
    Write-Host "    Error: $($_.Exception.Message)"
    $details = Get-ErrorResponseText -ErrorRecord $_
    if ($details) {
        Write-Host "    Response: $details"
    }
    exit 1
}

Write-Host "[4/8] Revalidating spec..." -NoNewline
try {
    $revalidated = Invoke-Api -Method POST -Url "$BaseUrl/api/feature-requests/$featureId/revalidate" -Headers $headers -TimeoutSec 30
    Add-StatusProgression -Statuses $statusProgression -Status ([string]$revalidated.status)
    Write-Host " Status: $($revalidated.status)" -ForegroundColor Green
    if ($revalidated.llm_spec_analysis) {
        $analysis = $revalidated.llm_spec_analysis
        $analysisModel = [string]($analysis.model)
        $analysisConfidence = $analysis.confidence
        Write-Host "       LLM analysis: confidence=$analysisConfidence, model=$analysisModel"
    }
    else {
        Write-Host "       (No LLM analysis - rule-based validation used)" -ForegroundColor Yellow
    }
}
catch {
    Write-Host " FAILED" -ForegroundColor Red
    Write-Host "    Error: $($_.Exception.Message)"
    $details = Get-ErrorResponseText -ErrorRecord $_
    if ($details) {
        Write-Host "    Response: $details"
    }
    exit 1
}

Write-Host "[5/8] Triggering build..." -NoNewline
try {
    $build = Invoke-Api -Method POST -Url "$BaseUrl/api/feature-requests/$featureId/build" -Headers $headers -TimeoutSec 10
    $buildStatus = [string]($build.status)
    if ($buildStatus) {
        Add-StatusProgression -Statuses $statusProgression -Status $buildStatus
    }
    if ($build.already_in_progress) {
        Write-Host " Reused existing job" -ForegroundColor Yellow
    }
    else {
        Write-Host " Enqueued" -ForegroundColor Green
    }
}
catch {
    Write-Host " FAILED" -ForegroundColor Red
    Write-Host "    Error: $($_.Exception.Message)"
    $details = Get-ErrorResponseText -ErrorRecord $_
    if ($details) {
        Write-Host "    Response: $details"
    }
    exit 1
}

Write-Host "[6/8] Polling build status..." -NoNewline
$status = [string]$revalidated.status
$detail = $null
$maxPolls = 30
$pollInterval = 2
for ($i = 0; $i -lt $maxPolls; $i++) {
    Start-Sleep -Seconds $pollInterval
    try {
        $detail = Invoke-Api -Method GET -Url "$BaseUrl/api/feature-requests/$featureId" -Headers $headers -TimeoutSec 5
        $status = [string]$detail.status
        Add-StatusProgression -Statuses $statusProgression -Status $status
        if ($status -eq "FAILED_BUILD") {
            Write-Host " FAILED_BUILD" -ForegroundColor Red
            break
        }
        if ($status -notin @("BUILDING", "READY_FOR_BUILD")) {
            Write-Host " Reached: $status" -ForegroundColor Green
            break
        }
        Write-Host "." -NoNewline
    }
    catch {
        Write-Host " FAILED" -ForegroundColor Red
        Write-Host "    Error while polling: $($_.Exception.Message)"
        $details = Get-ErrorResponseText -ErrorRecord $_
        if ($details) {
            Write-Host "    Response: $details"
        }
        exit 1
    }
}
if ($status -in @("BUILDING", "READY_FOR_BUILD")) {
    Write-Host " Timed out (status=$status after $($maxPolls * $pollInterval)s)" -ForegroundColor Yellow
}

Write-Host "[7/8] Checking cost tracking..." -NoNewline
if ($null -eq $detail) {
    $detail = Invoke-Api -Method GET -Url "$BaseUrl/api/feature-requests/$featureId" -Headers $headers -TimeoutSec 5
}
$events = @($detail.events | Where-Object { $_.event_type -eq "llm_cost" })
$totalCost = 0.0
$modelsUsed = @()
if ($events.Count -gt 0) {
    $totalCost = (($events | ForEach-Object {
        try {
            [double]$_.data.cost_usd
        }
        catch {
            0.0
        }
    } | Measure-Object -Sum).Sum)
    $modelsUsed = @(
        $events |
            ForEach-Object { [string]$_.data.model } |
            Where-Object { $_ } |
            Select-Object -Unique
    )
    Write-Host " $($events.Count) cost event(s) recorded" -ForegroundColor Green
    Write-Host "       Total LLM cost: `$$([math]::Round($totalCost, 4))"
}
else {
    Write-Host " No cost events (expected if OpenRouter not configured)" -ForegroundColor Yellow
}

Write-Host "[8/8] Testing approve..." -NoNewline
if ($status -in @("PREVIEW_READY", "PR_OPENED")) {
    try {
        $approved = Invoke-Api -Method POST -Url "$BaseUrl/api/feature-requests/$featureId/approve?approver=local-e2e" -Headers $headers -TimeoutSec 5
        Add-StatusProgression -Statuses $statusProgression -Status ([string]$approved.status)
        $status = [string]$approved.status
        Write-Host " Status: $status" -ForegroundColor Green
    }
    catch {
        Write-Host " FAILED" -ForegroundColor Red
        Write-Host "    Error: $($_.Exception.Message)"
        $details = Get-ErrorResponseText -ErrorRecord $_
        if ($details) {
            Write-Host "    Response: $details"
        }
        exit 1
    }
}
else {
    Write-Host " Skipped (status=$status, not approvable)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== RESULTS ===" -ForegroundColor Cyan
Write-Host "Feature ID:        $featureId"
Write-Host "Status flow:       $($statusProgression -join ' -> ')"
Write-Host "Final status:      $status"
Write-Host "OpenRouter:        $(if ($runtime.openrouter -and $runtime.openrouter.configured) { 'Active' } else { 'Fallback mode' })"
Write-Host "LLM cost events:   $(if ($events.Count -gt 0) { $events.Count } else { 'None' })"
Write-Host "Total LLM cost:    $(if ($events.Count -gt 0) { '$' + ([math]::Round($totalCost, 4)) } else { 'None' })"
Write-Host "Models used:       $(if ($modelsUsed.Count -gt 0) { $modelsUsed -join ', ' } else { 'None recorded' })"
Write-Host ""
Write-Host "To test with Slack: .\\scripts\\setup_slack_app.ps1" -ForegroundColor Yellow
