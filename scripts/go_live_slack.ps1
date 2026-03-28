[CmdletBinding()]
param(
    [switch]$SkipBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-EnvValueFromContent {
    param(
        [string]$Content,
        [string]$Key
    )

    $pattern = "(?m)^$([regex]::Escape($Key))=(.*)$"
    $match = [regex]::Match($Content, $pattern)
    if (-not $match.Success) {
        return ""
    }
    return [string]$match.Groups[1].Value.Trim()
}

function Set-EnvVar {
    param(
        [string]$Content,
        [string]$Key,
        [string]$Value
    )

    $pattern = "(?m)^$([regex]::Escape($Key))=.*$"
    $commentPattern = "(?m)^#\s*$([regex]::Escape($Key))=.*$"
    $replacement = "${Key}=${Value}"

    if ($Content -match $pattern) {
        return [regex]::Replace($Content, $pattern, $replacement)
    }
    if ($Content -match $commentPattern) {
        return [regex]::Replace($Content, $commentPattern, $replacement)
    }

    $suffix = if ($Content.EndsWith("`r`n")) { "" } elseif ($Content.EndsWith("`n")) { "" } else { "`r`n" }
    return $Content + $suffix + $replacement + "`r`n"
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

function Invoke-HealthGet {
    param(
        [string]$Url,
        [int]$TimeoutSec = 5
    )

    return Invoke-RestMethod -Uri $Url -TimeoutSec $TimeoutSec
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is not installed or not on PATH."
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  PRFactory -> Slack in 5 minutes" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

$repoRoot = (Get-Location).Path
$envPath = Join-Path $repoRoot ".env"
$envExamplePath = Join-Path $repoRoot ".env.example"
$manifestPath = Join-Path $repoRoot "docs\slack_app_manifest.yaml"
$apiBaseUrl = "http://localhost:8000"
$composeBaseArgs = @("-f", "docker-compose.yml", "-f", "docker-compose.dev.yml", "--profile", "slack")

$existingOpenRouterKey = ""
$existingSlackBotToken = ""
$existingSlackAppToken = ""
$preserved = @{}
$hasGithubApp = $false
$hasGithubPat = $false
$hasGithub = $false

Write-Host "[0/7] Reading existing .env..." -ForegroundColor Yellow
if (Test-Path $envPath) {
    $existingEnvContent = Get-Content $envPath -Raw
    $existingOpenRouterKey = Get-EnvValueFromContent -Content $existingEnvContent -Key "OPENROUTER_API_KEY"
    $existingSlackBotToken = Get-EnvValueFromContent -Content $existingEnvContent -Key "SLACK_BOT_TOKEN"
    $existingSlackAppToken = Get-EnvValueFromContent -Content $existingEnvContent -Key "SLACK_APP_TOKEN"

    $githubVars = @(
        "GITHUB_APP_ID",
        "GITHUB_APP_PRIVATE_KEY",
        "GITHUB_APP_PRIVATE_KEY_PATH",
        "GITHUB_PAT",
        "GITHUB_TOKEN",
        "GITHUB_ENABLED",
        "GITHUB_DEFAULT_ORG",
        "GITHUB_DEFAULT_REPO",
        "GITHUB_OAUTH_CLIENT_ID",
        "GITHUB_OAUTH_CLIENT_SECRET",
        "CODERUNNER_MODE",
        "LLM_API_KEY",
        "INTEGRATION_WEBHOOK_SECRET",
        "OPENCODE_TIMEOUT_SECONDS"
    )

    foreach ($var in $githubVars) {
        $value = Get-EnvValueFromContent -Content $existingEnvContent -Key $var
        if ($value) {
            $preserved[$var] = $value
        }
    }

    $backupName = ".env.backup.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item $envPath (Join-Path $repoRoot $backupName)
    Write-Host "  Backed up current .env -> $backupName" -ForegroundColor DarkGray
}
else {
    Write-Host "  No existing .env found. Starting from .env.example." -ForegroundColor DarkGray
}

if ($existingOpenRouterKey) {
    Write-Host "  Found OPENROUTER_API_KEY" -ForegroundColor Green
}
else {
    Write-Host "  No OPENROUTER_API_KEY found (Slack will use fallback rule-based intake)" -ForegroundColor Yellow
}

$hasSlackTokens = $existingSlackBotToken.StartsWith("xoxb-") -and $existingSlackAppToken.StartsWith("xapp-")
if ($hasSlackTokens) {
    Write-Host "  Found existing Slack tokens" -ForegroundColor Green
}

$hasGithubApp = $preserved.ContainsKey("GITHUB_APP_ID") -and [string]::IsNullOrWhiteSpace([string]$preserved["GITHUB_APP_ID"]) -eq $false
$hasGithubPat = (
    ($preserved.ContainsKey("GITHUB_PAT") -and [string]::IsNullOrWhiteSpace([string]$preserved["GITHUB_PAT"]) -eq $false) -or
    ($preserved.ContainsKey("GITHUB_TOKEN") -and [string]::IsNullOrWhiteSpace([string]$preserved["GITHUB_TOKEN"]) -eq $false)
)
$hasGithub = $hasGithubApp -or $hasGithubPat
if ($hasGithubApp) {
    Write-Host "  Found GitHub App credentials" -ForegroundColor Green
}
elseif ($hasGithubPat) {
    Write-Host "  Found GitHub personal access token" -ForegroundColor Green
}

Write-Host ""
Write-Host "[1/7] Configuring .env for local Slack..." -ForegroundColor Yellow
if (-not (Test-Path $envExamplePath)) {
    throw ".env.example not found. Run this from the repo root."
}

$baseEnv = Get-Content $envExamplePath -Raw
$baseEnv = Set-EnvVar -Content $baseEnv -Key "AUTH_MODE" -Value "disabled"
$baseEnv = Set-EnvVar -Content $baseEnv -Key "DATABASE_URL" -Value "postgresql+psycopg2://feature:feature@db:5432/feature_factory"
$baseEnv = Set-EnvVar -Content $baseEnv -Key "REDIS_URL" -Value "redis://redis:6379/0"
$baseEnv = Set-EnvVar -Content $baseEnv -Key "SECRET_KEY" -Value "local-dev-secret-$(Get-Random)"
$baseEnv = Set-EnvVar -Content $baseEnv -Key "SLACK_MODE" -Value "socket"
$baseEnv = Set-EnvVar -Content $baseEnv -Key "ENABLE_SLACK_BOT" -Value "true"
$baseEnv = Set-EnvVar -Content $baseEnv -Key "BASE_URL" -Value $apiBaseUrl
$baseEnv = Set-EnvVar -Content $baseEnv -Key "ORCHESTRATOR_INTERNAL_URL" -Value "http://api:8000"

if ($hasGithub) {
    $baseEnv = Set-EnvVar -Content $baseEnv -Key "MOCK_MODE" -Value "false"
    $baseEnv = Set-EnvVar -Content $baseEnv -Key "GITHUB_ENABLED" -Value "true"
    Write-Host "  GitHub credentials found - running in REAL mode" -ForegroundColor Green
}
else {
    $baseEnv = Set-EnvVar -Content $baseEnv -Key "MOCK_MODE" -Value "true"
    $baseEnv = Set-EnvVar -Content $baseEnv -Key "GITHUB_ENABLED" -Value "false"
    Write-Host "  No GitHub credentials - running in MOCK mode" -ForegroundColor Yellow
    Write-Host "  (PRs will be simulated, not real)" -ForegroundColor Yellow
}

if ($existingOpenRouterKey) {
    $baseEnv = Set-EnvVar -Content $baseEnv -Key "OPENROUTER_API_KEY" -Value $existingOpenRouterKey
}

foreach ($entry in $preserved.GetEnumerator()) {
    $baseEnv = Set-EnvVar -Content $baseEnv -Key ([string]$entry.Key) -Value ([string]$entry.Value)
}

if ($hasSlackTokens) {
    $baseEnv = Set-EnvVar -Content $baseEnv -Key "SLACK_BOT_TOKEN" -Value $existingSlackBotToken
    $baseEnv = Set-EnvVar -Content $baseEnv -Key "SLACK_APP_TOKEN" -Value $existingSlackAppToken
}

if ($hasGithub) {
    $baseEnv = Set-EnvVar -Content $baseEnv -Key "MOCK_MODE" -Value "false"
    $baseEnv = Set-EnvVar -Content $baseEnv -Key "GITHUB_ENABLED" -Value "true"
}
else {
    $baseEnv = Set-EnvVar -Content $baseEnv -Key "MOCK_MODE" -Value "true"
    $baseEnv = Set-EnvVar -Content $baseEnv -Key "GITHUB_ENABLED" -Value "false"
}

Set-Content $envPath $baseEnv
Write-Host "  .env written (local Docker, Slack socket mode)" -ForegroundColor Green
Write-Host "  Local DB/Redis targets now point at docker-compose services, not hosted infra." -ForegroundColor DarkGray

if ($hasGithubApp -and -not (Test-Path ".\secrets")) {
    Write-Host "  Creating .\secrets directory..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Path ".\secrets" -Force | Out-Null
}

Write-Host ""
if ($hasSlackTokens) {
    Write-Host "[2/7] Slack tokens already configured - skipping setup" -ForegroundColor Green
    Write-Host "  Bot token: $($existingSlackBotToken.Substring(0, [Math]::Min(10, $existingSlackBotToken.Length)))..." -ForegroundColor DarkGray
    Write-Host "  App token: $($existingSlackAppToken.Substring(0, [Math]::Min(10, $existingSlackAppToken.Length)))..." -ForegroundColor DarkGray
}
else {
    Write-Host "[2/7] Slack app setup (browser required)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  -----------------------------------------------------" -ForegroundColor White
    Write-Host "    MANUAL STEP 1 of 3: Create the Slack app" -ForegroundColor White
    Write-Host "    1. Open: https://api.slack.com/apps" -ForegroundColor White
    Write-Host "    2. Click 'Create New App' -> 'From a manifest'" -ForegroundColor White
    Write-Host "    3. Pick your workspace" -ForegroundColor White
    Write-Host "    4. Paste the manifest below, click Create" -ForegroundColor White
    Write-Host "  -----------------------------------------------------" -ForegroundColor White
    Write-Host ""

    if (Test-Path $manifestPath) {
        Write-Host "  -- Manifest (copy everything between the lines) --" -ForegroundColor DarkGray
        Write-Host ""
        Get-Content $manifestPath | ForEach-Object { Write-Host "  $_" }
        Write-Host ""
        Write-Host "  -- End of manifest --" -ForegroundColor DarkGray
    }
    else {
        Write-Host "  WARNING: docs/slack_app_manifest.yaml not found. You may need to configure the app manually." -ForegroundColor Red
    }
    Write-Host ""
    [void](Read-Host "  Press ENTER when the app is created")

    Write-Host ""
    Write-Host "  -----------------------------------------------------" -ForegroundColor White
    Write-Host "    MANUAL STEP 2 of 3: Get tokens" -ForegroundColor White
    Write-Host "    A) OAuth and Permissions -> Install to Workspace" -ForegroundColor White
    Write-Host "       -> Copy the Bot User OAuth Token (xoxb-...)" -ForegroundColor White
    Write-Host "    B) Settings -> Socket Mode -> Enable Socket Mode" -ForegroundColor White
    Write-Host "       -> Create token 'prfactory-socket'" -ForegroundColor White
    Write-Host "       -> Scope: connections:write" -ForegroundColor White
    Write-Host "       -> Copy the App-Level Token (xapp-...)" -ForegroundColor White
    Write-Host "  -----------------------------------------------------" -ForegroundColor White
    Write-Host ""

    $botToken = (Read-Host "  Paste Bot Token (xoxb-...)").Trim()
    if (-not $botToken.StartsWith("xoxb-")) {
        Write-Host "  WARNING: Expected Bot Token to start with xoxb-" -ForegroundColor Yellow
    }

    $appToken = (Read-Host "  Paste App Token (xapp-...)").Trim()
    if (-not $appToken.StartsWith("xapp-")) {
        Write-Host "  WARNING: Expected App Token to start with xapp-" -ForegroundColor Yellow
    }

    $envContent = Get-Content $envPath -Raw
    $envContent = Set-EnvVar -Content $envContent -Key "SLACK_BOT_TOKEN" -Value $botToken
    $envContent = Set-EnvVar -Content $envContent -Key "SLACK_APP_TOKEN" -Value $appToken
    Set-Content $envPath $envContent
    Write-Host "  Tokens saved to .env" -ForegroundColor Green
}

Write-Host ""
Write-Host "[3/7] Validating configuration..." -ForegroundColor Yellow
$envContent = Get-Content $envPath -Raw
$errors = @()

$checks = @(
    @{ Key = "SLACK_BOT_TOKEN"; Prefix = "xoxb-"; Required = $true },
    @{ Key = "SLACK_APP_TOKEN"; Prefix = "xapp-"; Required = $true },
    @{ Key = "SLACK_MODE"; Exact = "socket"; Required = $true },
    @{ Key = "ENABLE_SLACK_BOT"; Exact = "true"; Required = $true },
    @{ Key = "DATABASE_URL"; Prefix = "postgresql+psycopg2://feature:feature@db:5432/feature_factory"; Required = $true },
    @{ Key = "REDIS_URL"; Prefix = "redis://redis:6379/0"; Required = $true }
)

foreach ($check in $checks) {
    $key = [string]$check.Key
    $value = Get-EnvValueFromContent -Content $envContent -Key $key
    if (-not $value) {
        if ($check.Required) {
            Write-Host "  MISS $key" -ForegroundColor Red
            $errors += "$key is missing"
        }
        continue
    }

    $ok = $true
    if ($check.ContainsKey("Prefix") -and $check.Prefix) {
        if (-not $value.StartsWith([string]$check.Prefix)) {
            $ok = $false
        }
    }
    if ($check.ContainsKey("Exact") -and $check.Exact) {
        if ($value -ne [string]$check.Exact) {
            $ok = $false
        }
    }

    if ($ok) {
        Write-Host "  OK   $key" -ForegroundColor Green
    }
    else {
        Write-Host "  WARN $key = $value" -ForegroundColor Yellow
        $errors += "$key has unexpected value: $value"
    }
}

$openRouterValue = Get-EnvValueFromContent -Content $envContent -Key "OPENROUTER_API_KEY"
if ($openRouterValue) {
    Write-Host "  OK   OPENROUTER_API_KEY (model-assisted intake active)" -ForegroundColor Green
}
else {
    Write-Host "  SKIP OPENROUTER_API_KEY (fallback rule-based intake)" -ForegroundColor Yellow
}

$expectedMockMode = if ($hasGithub) { "false" } else { "true" }
$mockModeValue = Get-EnvValueFromContent -Content $envContent -Key "MOCK_MODE"
if ($mockModeValue -eq $expectedMockMode) {
    Write-Host "  OK   MOCK_MODE = $mockModeValue" -ForegroundColor Green
}
else {
    Write-Host "  WARN MOCK_MODE = $mockModeValue" -ForegroundColor Yellow
    $errors += "MOCK_MODE has unexpected value: $mockModeValue"
}

$expectedGithubEnabled = if ($hasGithub) { "true" } else { "false" }
$githubEnabledValue = Get-EnvValueFromContent -Content $envContent -Key "GITHUB_ENABLED"
if ($githubEnabledValue -eq $expectedGithubEnabled) {
    Write-Host "  OK   GITHUB_ENABLED = $githubEnabledValue" -ForegroundColor Green
}
else {
    Write-Host "  WARN GITHUB_ENABLED = $githubEnabledValue" -ForegroundColor Yellow
    $errors += "GITHUB_ENABLED has unexpected value: $githubEnabledValue"
}

Write-Host ""
Write-Host "  GitHub configuration:" -ForegroundColor White

if ($hasGithubApp) {
    Write-Host "  OK   GITHUB_APP_ID = $($preserved['GITHUB_APP_ID'])" -ForegroundColor Green
    if ($preserved.ContainsKey("GITHUB_APP_PRIVATE_KEY_PATH") -and $preserved["GITHUB_APP_PRIVATE_KEY_PATH"]) {
        $keyPath = [string]$preserved["GITHUB_APP_PRIVATE_KEY_PATH"]
        if (Test-Path $keyPath) {
            Write-Host "  OK   Private key file exists: $keyPath" -ForegroundColor Green
        }
        else {
            Write-Host "  WARN Private key file not found: $keyPath" -ForegroundColor Yellow
            Write-Host "       Check if it's at a different path or in ./secrets/" -ForegroundColor Yellow
        }
    }
    elseif ($preserved.ContainsKey("GITHUB_APP_PRIVATE_KEY") -and $preserved["GITHUB_APP_PRIVATE_KEY"]) {
        Write-Host "  OK   GITHUB_APP_PRIVATE_KEY is set (inline)" -ForegroundColor Green
    }
    else {
        Write-Host "  WARN GITHUB_APP_ID is set but no private key found" -ForegroundColor Red
        $errors += "GitHub App ID set but no private key"
    }
}
elseif ($hasGithubPat) {
    if ($preserved.ContainsKey("GITHUB_PAT") -and $preserved["GITHUB_PAT"]) {
        Write-Host "  OK   GITHUB_PAT is set" -ForegroundColor Green
    }
    else {
        Write-Host "  OK   GITHUB_TOKEN is set" -ForegroundColor Green
    }
}
else {
    Write-Host "  SKIP No GitHub credentials (mock mode)" -ForegroundColor Yellow
    Write-Host "       To enable real PRs, add GITHUB_APP_ID + private key" -ForegroundColor Yellow
    Write-Host "       or GITHUB_PAT/GITHUB_TOKEN to your .env" -ForegroundColor Yellow
}

if ($preserved.ContainsKey("GITHUB_DEFAULT_ORG") -and $preserved["GITHUB_DEFAULT_ORG"]) {
    Write-Host "  OK   Default org: $($preserved['GITHUB_DEFAULT_ORG'])" -ForegroundColor Green
}
if ($preserved.ContainsKey("GITHUB_DEFAULT_REPO") -and $preserved["GITHUB_DEFAULT_REPO"]) {
    Write-Host "  OK   Default repo: $($preserved['GITHUB_DEFAULT_REPO'])" -ForegroundColor Green
}
if ($preserved.ContainsKey("CODERUNNER_MODE") -and $preserved["CODERUNNER_MODE"]) {
    Write-Host "  OK   Code runner: $($preserved['CODERUNNER_MODE'])" -ForegroundColor Green
}

if ($errors.Count -gt 0) {
    Write-Host ""
    Write-Host "  Config issues:" -ForegroundColor Red
    $errors | ForEach-Object { Write-Host "    - $_" -ForegroundColor Red }
    throw "Configuration validation failed. Fix .env values or re-run this script with fresh Slack tokens."
}

Write-Host ""
Write-Host "[4/7] Stopping any running containers..." -ForegroundColor Yellow
try {
    & docker compose @composeBaseArgs down --remove-orphans 2>$null | Out-Null
}
catch {
    # Ignore cleanup failures and continue to startup.
}
Write-Host "  Done" -ForegroundColor Green

Write-Host ""
Write-Host "[5/7] Starting PRFactory + Slack..." -ForegroundColor Yellow
Write-Host "  Validating docker compose config..." -ForegroundColor DarkGray
& docker compose @composeBaseArgs config | Out-Null

$upArgs = @()
$upArgs += $composeBaseArgs
$upArgs += @("up", "-d")
if (-not $SkipBuild) {
    $upArgs += "--build"
}

$upCommandText = "docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile slack up -d"
if (-not $SkipBuild) {
    $upCommandText += " --build"
}
Write-Host "  Running: $upCommandText" -ForegroundColor DarkGray
& docker compose @upArgs
if ($LASTEXITCODE -ne 0) {
    throw "docker compose failed. Make sure Docker Desktop is running."
}

Write-Host ""
Write-Host "[6/7] Waiting for services to come up..." -ForegroundColor Yellow
$maxWaitSeconds = 60
$waited = 0
$healthy = $false

while ($waited -lt $maxWaitSeconds) {
    Start-Sleep -Seconds 2
    $waited += 2
    Write-Host "." -NoNewline
    try {
        $health = Invoke-HealthGet -Url "$apiBaseUrl/health" -TimeoutSec 3
        if ($health.ok -eq $true) {
            $healthy = $true
            break
        }
    }
    catch {
        # still starting
    }
}
Write-Host ""

if (-not $healthy) {
    Write-Host "  API did not come up after ${maxWaitSeconds}s" -ForegroundColor Red
    Write-Host "  Check logs: docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile slack logs api --tail 30" -ForegroundColor Yellow
    exit 1
}
Write-Host "  API is healthy" -ForegroundColor Green

Start-Sleep -Seconds 3
$slackbotLogs = (& docker compose @composeBaseArgs logs slackbot --tail 20 2>&1) | Out-String
$botRunning = $slackbotLogs -match "Starting Slack Socket Mode handler|SocketMode|connected|Bolt"
$botErrored = $slackbotLogs -match "Traceback|error|Error|FAILED|cannot start|exited with code|missing"

if ($botRunning -and -not $botErrored) {
    Write-Host "  Slackbot connected" -ForegroundColor Green
}
elseif ($botErrored) {
    Write-Host "  Slackbot has errors:" -ForegroundColor Red
    & docker compose @composeBaseArgs logs slackbot --tail 10
    Write-Host ""
    Write-Host "  Common fixes:" -ForegroundColor Yellow
    Write-Host "    - Verify Socket Mode is enabled at https://api.slack.com/apps" -ForegroundColor Yellow
    Write-Host "    - Verify the xapp- token has the connections:write scope" -ForegroundColor Yellow
    Write-Host "    - Re-run this script to enter fresh tokens" -ForegroundColor Yellow
}
else {
    Write-Host "  Slackbot is starting (it may need a few more seconds)..." -ForegroundColor Yellow
}

try {
    $runtime = Invoke-HealthGet -Url "$apiBaseUrl/health/runtime" -TimeoutSec 3
    if ($runtime.openrouter -and $runtime.openrouter.configured) {
        Write-Host "  OpenRouter active: mini=$($runtime.openrouter.mini_model), frontier=$($runtime.openrouter.frontier_model)" -ForegroundColor Green
    }
    else {
        Write-Host "  OpenRouter: fallback mode (no key configured)" -ForegroundColor Yellow
    }
}
catch {
    Write-Host "  Could not check runtime config" -ForegroundColor Yellow
}

Write-Host ""
if (-not $hasSlackTokens) {
    Write-Host "[7/7] Last manual step" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  -----------------------------------------------------" -ForegroundColor White
    Write-Host "    MANUAL STEP 3 of 3: Invite the bot" -ForegroundColor White
    Write-Host "    In Slack:" -ForegroundColor White
    Write-Host "    1. Go to #prfactory-test (or create it)" -ForegroundColor White
    Write-Host "    2. Type: /invite @PRFactory" -ForegroundColor White
    Write-Host "  -----------------------------------------------------" -ForegroundColor White
    Write-Host ""
    [void](Read-Host "  Press ENTER when the bot is invited")
}
else {
    Write-Host "[7/7] Final Slack check" -ForegroundColor Green
    Write-Host "  If the bot is not in your test channel yet, invite it now with: /invite @PRFactory" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  PRFactory is running with Slack!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
if ($hasGithub) {
    $runnerSummary = if ($preserved.ContainsKey("CODERUNNER_MODE") -and $preserved["CODERUNNER_MODE"]) {
        [string]$preserved["CODERUNNER_MODE"]
    }
    else {
        "default"
    }
    Write-Host "  Mode: REAL" -ForegroundColor Green
    Write-Host "  - Mini model intake: OpenRouter (qwen3.5-9b)" -ForegroundColor White
    Write-Host "  - Spec validation: OpenRouter (claude-opus-4-6)" -ForegroundColor White
    Write-Host "  - Code generation: $runnerSummary" -ForegroundColor White
    Write-Host "  - GitHub PRs: REAL (will create actual PRs)" -ForegroundColor White
    Write-Host ""
    Write-Host "  WARNING: Builds will create REAL PRs in your repos." -ForegroundColor Yellow
    Write-Host "  Use a test repo or branch for initial testing." -ForegroundColor Yellow
}
else {
    Write-Host "  Mode: MOCK" -ForegroundColor Yellow
    Write-Host "  - Mini model intake: OpenRouter (real)" -ForegroundColor White
    Write-Host "  - Spec validation: OpenRouter (real)" -ForegroundColor White
    Write-Host "  - Code generation: simulated" -ForegroundColor White
    Write-Host "  - GitHub PRs: simulated" -ForegroundColor White
}
Write-Host ""
Write-Host "  Slack commands to try:" -ForegroundColor White
Write-Host "  /prfactory" -ForegroundColor Cyan
Write-Host "    -> Interactive intake form" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Message in a thread:" -ForegroundColor Cyan
Write-Host "    I want to add dark mode to the settings page" -ForegroundColor Cyan
Write-Host "    -> Mini-tier intake asks follow-up questions" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  /prfactory-github" -ForegroundColor Cyan
Write-Host "    -> Connect GitHub for repo/branch dropdowns" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Web UI: $apiBaseUrl" -ForegroundColor Cyan
Write-Host "  Health: $apiBaseUrl/health/runtime" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Logs:" -ForegroundColor White
Write-Host "    docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile slack logs slackbot --tail 50 -f" -ForegroundColor DarkGray
Write-Host "    docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile slack logs api --tail 50 -f" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  To stop:" -ForegroundColor White
Write-Host "    docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile slack down" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Tokens expired? Re-run this script." -ForegroundColor Yellow
Write-Host ""
