[CmdletBinding()]
param(
    [switch]$Validate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Read-EnvMap {
    param([string]$Path = ".env")

    $envMap = @{}
    if (-not (Test-Path $Path)) {
        return $envMap
    }

    Get-Content $Path | ForEach-Object {
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

function Set-EnvVar {
    param(
        [string]$Content,
        [string]$Key,
        [string]$Value
    )

    $pattern = "(?m)^$([regex]::Escape($Key))=.*$"
    if ($Content -match $pattern) {
        return [regex]::Replace($Content, $pattern, "${Key}=${Value}")
    }

    $suffix = if ($Content.EndsWith("`n")) { "" } else { "`r`n" }
    return $Content + $suffix + "${Key}=${Value}" + "`r`n"
}

function Show-TokenWarning {
    param(
        [string]$Token,
        [string]$ExpectedPrefix,
        [string]$Label
    )

    if ([string]::IsNullOrWhiteSpace($Token)) {
        Write-Host "  WARNING: $Label was left blank." -ForegroundColor Red
        return
    }

    if (-not $Token.StartsWith($ExpectedPrefix)) {
        $prefix = $Token.Substring(0, [Math]::Min(10, $Token.Length))
        Write-Host "  WARNING: $Label should start with '$ExpectedPrefix'. Got: ${prefix}..." -ForegroundColor Red
    }
}

Write-Host "=== PRFactory Slack App Setup ===" -ForegroundColor Cyan
Write-Host "Free-tier compatible. Takes ~5 minutes." -ForegroundColor DarkGray
Write-Host ""

if (-not $Validate) {
    Write-Host "This script walks you through creating a new Slack app." -ForegroundColor White
    Write-Host "You'll need a Slack workspace where you have admin access." -ForegroundColor White
    Write-Host ""

    Write-Host "STEP 1: Create the Slack app" -ForegroundColor Yellow
    Write-Host "  1. Go to: https://api.slack.com/apps"
    Write-Host "  2. Click 'Create New App' -> 'From a manifest'"
    Write-Host "  3. Select your workspace"
    Write-Host "  4. Paste the manifest from: docs/slack_app_manifest.yaml"
    Write-Host ""

    $manifestPath = "docs/slack_app_manifest.yaml"
    if (Test-Path $manifestPath) {
        Write-Host "--- MANIFEST START ---" -ForegroundColor DarkGray
        Get-Content $manifestPath | ForEach-Object { Write-Host $_ }
        Write-Host "--- MANIFEST END ---" -ForegroundColor DarkGray
    }
    else {
        Write-Host "  WARNING: $manifestPath not found. Check the repo." -ForegroundColor Red
    }
    Write-Host ""
    [void](Read-Host "Press Enter when the app is created")

    Write-Host ""
    Write-Host "STEP 2: Get your Bot Token" -ForegroundColor Yellow
    Write-Host "  1. In your app settings, go to: OAuth & Permissions"
    Write-Host "  2. Click 'Install to Workspace'"
    Write-Host "  3. Copy the Bot User OAuth Token (starts with xoxb-)"
    Write-Host ""
    $botToken = Read-Host "Paste your Bot Token (xoxb-...)"
    Show-TokenWarning -Token $botToken -ExpectedPrefix "xoxb-" -Label "Bot Token"

    Write-Host ""
    Write-Host "STEP 3: Enable Socket Mode" -ForegroundColor Yellow
    Write-Host "  1. Go to: Settings -> Socket Mode"
    Write-Host "  2. Toggle 'Enable Socket Mode' ON"
    Write-Host "  3. Create an App-Level Token named 'prfactory-socket'"
    Write-Host "  4. Add scope: connections:write"
    Write-Host "  5. Copy the token (starts with xapp-)"
    Write-Host ""
    $appToken = Read-Host "Paste your App Token (xapp-...)"
    Show-TokenWarning -Token $appToken -ExpectedPrefix "xapp-" -Label "App Token"

    Write-Host ""
    Write-Host "STEP 4: Updating .env" -ForegroundColor Yellow
    $envPath = ".env"
    if (-not (Test-Path $envPath)) {
        if (-not (Test-Path ".env.example")) {
            throw ".env.example not found in repo root."
        }
        Copy-Item ".env.example" $envPath
        Write-Host "  Created .env from .env.example" -ForegroundColor Green
    }

    $envContent = Get-Content $envPath -Raw
    $envContent = Set-EnvVar -Content $envContent -Key "SLACK_BOT_TOKEN" -Value $botToken
    $envContent = Set-EnvVar -Content $envContent -Key "SLACK_APP_TOKEN" -Value $appToken
    $envContent = Set-EnvVar -Content $envContent -Key "SLACK_MODE" -Value "socket"
    $envContent = Set-EnvVar -Content $envContent -Key "ENABLE_SLACK_BOT" -Value "true"
    Set-Content $envPath $envContent
    Write-Host "  Updated .env with Slack socket-mode settings" -ForegroundColor Green

    Write-Host ""
    Write-Host "STEP 5: Invite the bot to a channel" -ForegroundColor Yellow
    Write-Host "  1. In Slack, create or open a test channel (for example #prfactory-test)"
    Write-Host "  2. Type: /invite @PRFactory"
    Write-Host "  3. Confirm the bot shows up in the member list"
    Write-Host ""
    [void](Read-Host "Press Enter when the bot is invited")
}

Write-Host ""
Write-Host "=== Validating Configuration ===" -ForegroundColor Cyan

if (-not (Test-Path ".env")) {
    Write-Host "  MISS .env" -ForegroundColor Red
    Write-Host ""
    Write-Host "Create .env first by copying .env.example or running this script without -Validate." -ForegroundColor Red
    exit 1
}

$envMap = Read-EnvMap -Path ".env"
$errors = @()

$requiredVars = @(
    @{ Key = "SLACK_BOT_TOKEN"; Expected = "xoxb-" },
    @{ Key = "SLACK_APP_TOKEN"; Expected = "xapp-" },
    @{ Key = "SLACK_MODE"; Expected = "socket" },
    @{ Key = "ENABLE_SLACK_BOT"; Expected = "true" }
)

foreach ($item in $requiredVars) {
    $value = [string]($envMap[$item.Key])
    if ([string]::IsNullOrWhiteSpace($value)) {
        $errors += "$($item.Key) is missing or empty"
        Write-Host "  MISS $($item.Key)" -ForegroundColor Red
        continue
    }

    if ($item.Expected -in @("socket", "true")) {
        if ($value.Trim().ToLowerInvariant() -ne $item.Expected) {
            $errors += "$($item.Key) = '$value' (expected '$($item.Expected)')"
            Write-Host "  WARN $($item.Key)" -ForegroundColor Yellow
            continue
        }
    }
    elseif (-not $value.StartsWith($item.Expected)) {
        $errors += "$($item.Key) = '$value' (expected prefix '$($item.Expected)')"
        Write-Host "  WARN $($item.Key)" -ForegroundColor Yellow
        continue
    }

    Write-Host "  OK   $($item.Key)" -ForegroundColor Green
}

if ((([string]($envMap["SLACK_MODE"])).Trim().ToLowerInvariant()) -eq "http") {
    $errors += "SLACK_MODE is 'http' but local slackbot uses Socket Mode. Change to 'socket'."
    Write-Host "  WARN SLACK_MODE=http (should be socket for local)" -ForegroundColor Red
}

if ($errors.Count -gt 0) {
    Write-Host ""
    Write-Host "Issues found:" -ForegroundColor Red
    $errors | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
}
else {
    Write-Host ""
    Write-Host "All checks passed!" -ForegroundColor Green
}

Write-Host ""
Write-Host "=== Next Steps ===" -ForegroundColor Cyan
Write-Host "1. Prove the local pipeline works without Slack first:"
Write-Host "   powershell -ExecutionPolicy Bypass -File .\\scripts\\test_local_e2e.ps1" -ForegroundColor White
Write-Host ""
Write-Host "2. Start the stack with Slack enabled:"
Write-Host "   powershell -ExecutionPolicy Bypass -File .\\scripts\\run_local.ps1 -WithSlack" -ForegroundColor White
Write-Host ""
Write-Host "3. In Slack, try `/prfactory` or send a thread message like:"
Write-Host "   I want to add dark mode to the settings page" -ForegroundColor White
Write-Host ""
Write-Host "4. If the bot doesn't respond, check logs:"
Write-Host "   docker compose --profile slack logs slackbot --tail 50" -ForegroundColor White
Write-Host ""
Write-Host "Common free-tier issues:" -ForegroundColor Yellow
Write-Host "  - Tokens expire after about 90 days. Re-run this script to refresh your local config."
Write-Host "  - Free workspaces have a limited app count. Delete unused apps at https://api.slack.com/apps."
Write-Host "  - Socket Mode requires the connections:write scope on the App Token."
