# Deploy Feature Factory to Modal using production-safe defaults.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\deploy_modal_prod.ps1 -BaseUrl "https://<your-modal-url>"
#
# Optional:
#   -EnvFile .env
#   -ModalSecretName feature-factory-env
#   -ModalAppPath .\modal_app.py
#   -IndexerBaseUrl https://<repo-indexer-url>
#   -RequireIndexer
#   -SkipSecretSync
#   -SkipDeploy
#   -SkipSlackManifestSync

param(
  [string]$EnvFile = ".env",
  [string]$ModalSecretName = "feature-factory-env",
  [string]$ModalAppPath = ".\modal_app.py",
  [string]$BaseUrl = "",
  [string]$IndexerBaseUrl = "",
  [switch]$RequireIndexer,
  [switch]$SkipSecretSync,
  [switch]$SkipDeploy,
  [switch]$SkipSlackManifestSync
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Read-DotEnv {
  param([Parameter(Mandatory = $true)][string]$Path)

  $map = @{}
  $allLines = Get-Content $Path
  for ($i = 0; $i -lt $allLines.Count; $i++) {
    $lineNumber = $i + 1
    $line = $allLines[$i].Trim()
    if ($line -and -not $line.StartsWith("#")) {
      $eq = $line.IndexOf("=")
      if ($eq -ge 1) {
        $key = $line.Substring(0, $eq).Trim()
        $value = $line.Substring($eq + 1)
        if ($map.ContainsKey($key)) {
          Write-Host "Ignoring duplicate key '$key' at line $lineNumber in $Path (keeping first value)." -ForegroundColor Yellow
          continue
        }
        $map[$key] = $value
      }
    }
  }
  return $map
}

function Set-DotEnvValue {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Key,
    [Parameter(Mandatory = $true)][string]$Value
  )

  if (-not (Is-ValidEnvKey -Key $Key)) {
    throw "Invalid env key for update: $Key"
  }

  $lines = @()
  if (Test-Path $Path) {
    $lines = Get-Content $Path
  }

  $updated = $false
  for ($i = 0; $i -lt $lines.Count; $i++) {
    $line = [string]$lines[$i]
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
      continue
    }
    $eq = $line.IndexOf("=")
    if ($eq -lt 1) {
      continue
    }
    $existingKey = $line.Substring(0, $eq).Trim()
    if ($existingKey -ne $Key) {
      continue
    }
    $lines[$i] = "$Key=$Value"
    $updated = $true
    break
  }

  if (-not $updated) {
    $lines += "$Key=$Value"
  }

  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllLines($Path, $lines, $utf8NoBom)
}

function Is-Truthy {
  param([string]$Value)

  $text = ""
  if ($null -ne $Value) {
    $text = $Value.Trim().ToLowerInvariant()
  }
  return @("1", "true", "yes", "on") -contains $text
}

function New-RandomHexToken {
  param([int]$NumBytes = 32)

  $bytes = New-Object byte[] $NumBytes
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $rng.GetBytes($bytes)
  }
  finally {
    $rng.Dispose()
  }
  return ([System.BitConverter]::ToString($bytes) -replace "-", "").ToLowerInvariant()
}

function Resolve-RepoPath {
  param(
    [Parameter(Mandatory = $true)][string]$PathValue,
    [Parameter(Mandatory = $true)][string]$RepoRoot
  )

  if ([string]::IsNullOrWhiteSpace($PathValue)) {
    return ""
  }
  $candidate = $PathValue.Trim()
  if ([System.IO.Path]::IsPathRooted($candidate)) {
    return [System.IO.Path]::GetFullPath($candidate)
  }
  return [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $candidate))
}

function Get-MapValue {
  param(
    [Parameter(Mandatory = $true)][hashtable]$Map,
    [Parameter(Mandatory = $true)][string]$Key,
    [string]$Default = ""
  )

  if ($Map.ContainsKey($Key)) {
    return [string]$Map[$Key]
  }
  return $Default
}

function Set-MapValueIfMissing {
  param(
    [Parameter(Mandatory = $true)][hashtable]$Map,
    [Parameter(Mandatory = $true)][string]$TargetKey,
    [Parameter(Mandatory = $true)][string]$FallbackKey
  )

  $target = Get-MapValue -Map $Map -Key $TargetKey
  if (-not [string]::IsNullOrWhiteSpace($target)) {
    return
  }
  $fallback = Get-MapValue -Map $Map -Key $FallbackKey
  if (-not [string]::IsNullOrWhiteSpace($fallback)) {
    $Map[$TargetKey] = $fallback
  }
}

function Is-ValidEnvKey {
  param([string]$Key)

  if ([string]::IsNullOrWhiteSpace($Key)) {
    return $false
  }
  if ($Key -cne $Key.ToUpperInvariant()) {
    return $false
  }
  return $Key -cmatch "^[A-Z][A-Z0-9_]*$"
}

function Ensure-SecretName {
  param(
    [Parameter(Mandatory = $true)]$Names,
    [string]$Name
  )

  if ([string]::IsNullOrWhiteSpace($Name)) {
    return
  }
  $candidate = $Name.Trim()
  if (-not $Names.Contains($candidate)) {
    $null = $Names.Add($candidate)
  }
}

function Ensure-NeonSslMode {
  param([Parameter(Mandatory = $true)][string]$Url)

  $text = $Url.Trim()
  if ([string]::IsNullOrWhiteSpace($text)) {
    return $text
  }
  if ($text -notmatch "neon\.tech") {
    return $text
  }
  if ($text -match "(^|[?&])sslmode=") {
    return $text
  }
  if ($text.Contains("?")) {
    return ($text + "&sslmode=require")
  }
  return ($text + "?sslmode=require")
}

function Ensure-UpstashTls {
  param([Parameter(Mandatory = $true)][string]$Url)

  $text = $Url.Trim()
  if ([string]::IsNullOrWhiteSpace($text)) {
    return $text
  }
  if ($text -notmatch "upstash\.io") {
    return $text
  }
  if ($text -match "^redis://") {
    return ($text -replace "^redis://", "rediss://")
  }
  return $text
}

function Require-NonEmptyKeys {
  param(
    [Parameter(Mandatory = $true)][hashtable]$Map,
    [Parameter(Mandatory = $true)][string[]]$Keys
  )

  $missing = @()
  foreach ($k in $Keys) {
    $v = ""
    if ($Map.ContainsKey($k)) {
      $v = [string]$Map[$k]
    }
    if ([string]::IsNullOrWhiteSpace($v)) {
      $missing += $k
    }
  }
  if ($missing.Count -gt 0) {
    throw ("Missing required production values: " + ($missing -join ", "))
  }
}

function Test-SlackBotToken {
  param([Parameter(Mandatory = $true)][string]$Token)

  if ([string]::IsNullOrWhiteSpace($Token)) {
    throw "SLACK_BOT_TOKEN is empty."
  }

  try {
    $resp = Invoke-RestMethod -Method Post -Uri "https://slack.com/api/auth.test" -Body @{ token = $Token } -TimeoutSec 30
  }
  catch {
    throw ("Failed to validate SLACK_BOT_TOKEN against Slack auth.test: " + $_.Exception.Message)
  }

  $ok = $false
  if ($null -ne $resp -and $null -ne $resp.ok) {
    $ok = [bool]$resp.ok
  }
  if (-not $ok) {
    $err = ""
    if ($null -ne $resp -and $null -ne $resp.error) {
      $err = [string]$resp.error
    }
    if ([string]::IsNullOrWhiteSpace($err)) {
      $err = "unknown_error"
    }
    throw "SLACK_BOT_TOKEN failed Slack auth.test: $err"
  }
}

function Invoke-WithRetry {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][scriptblock]$Action,
    [int]$Attempts = 10,
    [int]$DelaySeconds = 8
  )

  for ($i = 1; $i -le $Attempts; $i++) {
    try {
      & $Action | Out-Null
      Write-Host "$Name OK" -ForegroundColor Green
      return
    }
    catch {
      if ($i -ge $Attempts) {
        throw
      }
      Start-Sleep -Seconds $DelaySeconds
    }
  }
}

function Get-StatusCodeFromException {
  param([Parameter(Mandatory = $true)]$ExceptionObject)

  if ($null -eq $ExceptionObject) {
    return -1
  }

  $exceptionProps = $ExceptionObject.PSObject.Properties.Name
  if ($exceptionProps -contains "Response") {
    $response = $ExceptionObject.Response
    if ($null -ne $response) {
      $responseProps = $response.PSObject.Properties.Name
      if ($responseProps -contains "StatusCode") {
        $statusObj = $response.StatusCode
        if ($null -ne $statusObj) {
          if ($statusObj -is [int]) {
            return [int]$statusObj
          }
          $statusProps = $statusObj.PSObject.Properties.Name
          if ($statusProps -contains "value__") {
            return [int]$statusObj.value__
          }
          try {
            return [int]$statusObj
          }
          catch {
            # Fall through to message parsing.
          }
        }
      }
    }
  }

  $message = [string]$ExceptionObject.Message
  if ($message -match "\b([1-5][0-9][0-9])\b") {
    return [int]$matches[1]
  }
  return -1
}

function Get-HttpStatusCode {
  param(
    [Parameter(Mandatory = $true)][string]$Url,
    [bool]$AllowRedirect = $true
  )

  try {
    Add-Type -AssemblyName System.Net.Http -ErrorAction Stop | Out-Null
  }
  catch {
    # Fall back to Invoke-WebRequest path below.
  }

  $httpClientAvailable = $true
  try {
    [void][System.Net.Http.HttpClient]
  }
  catch {
    $httpClientAvailable = $false
  }

  if (-not $httpClientAvailable) {
    try {
      $maxRedirects = if ($AllowRedirect) { 10 } else { 0 }
      $response = Invoke-WebRequest -Uri $Url -Method Get -MaximumRedirection $maxRedirects -TimeoutSec 30 -ErrorAction Stop
      return [int]$response.StatusCode
    }
    catch {
      $status = Get-StatusCodeFromException -ExceptionObject $_.Exception
      if ($status -gt 0) {
        return $status
      }
      $message = [string]$_.Exception.Message
      if ($message.ToLowerInvariant().Contains("maximum redirection count has been exceeded")) {
        return 302
      }
      throw
    }
  }

  $handler = New-Object System.Net.Http.HttpClientHandler
  $handler.AllowAutoRedirect = $AllowRedirect
  $client = New-Object System.Net.Http.HttpClient($handler)
  $request = $null
  $response = $null
  try {
    $client.Timeout = [TimeSpan]::FromSeconds(30)
    $request = New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::Get, $Url)
    $response = $client.SendAsync($request).GetAwaiter().GetResult()
    return [int]$response.StatusCode
  }
  catch {
    $status = Get-StatusCodeFromException -ExceptionObject $_.Exception
    if ($status -gt 0) {
      return $status
    }
    $message = [string]$_.Exception.Message
    if ($message.ToLowerInvariant().Contains("maximum redirection count has been exceeded")) {
      # Some PowerShell builds surface redirect responses this way when redirects are disabled.
      return 302
    }
    throw
  }
  finally {
    if ($null -ne $response) {
      $response.Dispose()
    }
    if ($null -ne $request) {
      $request.Dispose()
    }
    $client.Dispose()
    $handler.Dispose()
  }
}

$repoRoot = (Resolve-Path ".").Path
$resolvedEnvFile = Resolve-RepoPath -PathValue $EnvFile -RepoRoot $repoRoot
if (-not (Test-Path $resolvedEnvFile)) {
  throw "Env file not found: $resolvedEnvFile"
}

$config = Read-DotEnv -Path $resolvedEnvFile
$generatedEnvValues = @{}

# Production posture + cost controls.
$config["APP_ENV"] = "prod"
$config["ENFORCE_PRODUCTION_SECURITY"] = "true"
$config["MOCK_MODE"] = "false"
$config["RUN_MIGRATIONS"] = "false"
if ([string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "ENABLE_SLACK_BOT"))) {
  $config["ENABLE_SLACK_BOT"] = "false"
}
if ([string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "SLACK_MODE"))) {
  $config["SLACK_MODE"] = "http"
}
if ([string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "ENABLE_SLACK_OAUTH"))) {
  $config["ENABLE_SLACK_OAUTH"] = "false"
}
if ([string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "SLACK_OAUTH_INSTALL_PATH"))) {
  $config["SLACK_OAUTH_INSTALL_PATH"] = "/api/slack/install"
}
if ([string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "SLACK_OAUTH_CALLBACK_PATH"))) {
  $config["SLACK_OAUTH_CALLBACK_PATH"] = "/api/slack/oauth/callback"
}
$config["MODAL_API_MIN_CONTAINERS"] = "0"
$config["MODAL_API_MAX_CONTAINERS"] = "1"
$config["MODAL_API_ALLOW_CONCURRENT_INPUTS"] = "8"
$config["MODAL_QUEUE_DRAIN_SECONDS"] = "180"
$config["MODAL_CLEANUP_INTERVAL_MINUTES"] = "120"
$config["MODAL_SKIP_WORKER_WHEN_QUEUE_EMPTY"] = "true"
$config["WORKSPACE_RETENTION_HOURS"] = "12"
$config["WORKSPACE_RETENTION_HOURS_WITH_PR"] = "72"
$config["WORKSPACE_RETENTION_HOURS_WITHOUT_PR"] = "12"
$config["WORKSPACE_RETENTION_HOURS_FAILED"] = "6"
$config["WORKSPACE_CLEANUP_INTERVAL_MINUTES"] = "30"
$config["OPENCODE_KEEP_TEMP_AGENTS"] = "false"
$config["MODAL_ENV_SECRET_NAME"] = $ModalSecretName
$config["DISABLE_AUTOMERGE"] = "true"

Set-MapValueIfMissing -Map $config -TargetKey "DATABASE_URL" -FallbackKey "NEONURL"
Set-MapValueIfMissing -Map $config -TargetKey "REDIS_URL" -FallbackKey "UPSTASH_REDIS_URL"
$resolvedDatabaseUrl = Ensure-NeonSslMode -Url (Get-MapValue -Map $config -Key "DATABASE_URL")
$resolvedRedisUrl = Ensure-UpstashTls -Url (Get-MapValue -Map $config -Key "REDIS_URL")
if (-not [string]::IsNullOrWhiteSpace($resolvedDatabaseUrl)) {
  $config["DATABASE_URL"] = $resolvedDatabaseUrl
  $config["NEONURL"] = $resolvedDatabaseUrl
}
if (-not [string]::IsNullOrWhiteSpace($resolvedRedisUrl)) {
  $config["REDIS_URL"] = $resolvedRedisUrl
}

$extraSecretNames = New-Object 'System.Collections.Generic.List[string]'
$existingExtra = Get-MapValue -Map $config -Key "MODAL_EXTRA_SECRET_NAMES"
foreach ($name in ($existingExtra -split ",")) {
  Ensure-SecretName -Names $extraSecretNames -Name $name
}
if (-not [string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "SLACK_BOT_TOKEN"))) {
  Ensure-SecretName -Names $extraSecretNames -Name "slack-secret"
}
if (-not [string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "REDIS_URL"))) {
  Ensure-SecretName -Names $extraSecretNames -Name "feature-factory-redis"
}
if (-not [string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "DATABASE_URL"))) {
  Ensure-SecretName -Names $extraSecretNames -Name "NeonURL"
}
$config["MODAL_EXTRA_SECRET_NAMES"] = ($extraSecretNames -join ",")

if (-not [string]::IsNullOrWhiteSpace($BaseUrl)) {
  $config["BASE_URL"] = $BaseUrl.Trim()
}
$resolvedBaseUrl = Get-MapValue -Map $config -Key "BASE_URL"
$resolvedBaseUrl = $resolvedBaseUrl.Trim()
if ([string]::IsNullOrWhiteSpace($resolvedBaseUrl)) {
  throw "BASE_URL must be set to the public Modal URL (for example https://<workspace>--feature-factory-api.modal.run)."
}
try {
  $baseUri = [Uri]$resolvedBaseUrl
}
catch {
  throw "BASE_URL is not a valid absolute URL: $resolvedBaseUrl"
}
if (-not $baseUri.IsAbsoluteUri) {
  throw "BASE_URL must be an absolute URL: $resolvedBaseUrl"
}
$normalizedBaseUrl = ("{0}://{1}" -f $baseUri.Scheme, $baseUri.Authority).TrimEnd("/")
if ($baseUri.AbsolutePath -ne "/" -or -not [string]::IsNullOrWhiteSpace($baseUri.Query) -or -not [string]::IsNullOrWhiteSpace($baseUri.Fragment)) {
  Write-Host "BASE_URL should be the origin only. Normalizing '$resolvedBaseUrl' -> '$normalizedBaseUrl'." -ForegroundColor Yellow
}
$resolvedBaseUrl = $normalizedBaseUrl
if ($resolvedBaseUrl -ne (Get-MapValue -Map $config -Key "BASE_URL")) {
  $config["BASE_URL"] = $resolvedBaseUrl
}
if ($resolvedBaseUrl -match "localhost|127\.0\.0\.1") {
  throw "BASE_URL cannot be localhost for production deployment: $resolvedBaseUrl"
}
$config["ORCHESTRATOR_INTERNAL_URL"] = $resolvedBaseUrl

if (-not [string]::IsNullOrWhiteSpace($IndexerBaseUrl)) {
  $config["INDEXER_BASE_URL"] = $IndexerBaseUrl.Trim()
}
$indexerRequired = $RequireIndexer -or (Is-Truthy (Get-MapValue -Map $config -Key "INDEXER_REQUIRED" -Default "false"))
if ($indexerRequired) {
  $config["INDEXER_REQUIRED"] = "true"
}
$resolvedIndexerBaseUrl = (Get-MapValue -Map $config -Key "INDEXER_BASE_URL").Trim().TrimEnd("/")
if ($indexerRequired -and [string]::IsNullOrWhiteSpace($resolvedIndexerBaseUrl)) {
  throw "INDEXER_REQUIRED=true but INDEXER_BASE_URL is missing. Set INDEXER_BASE_URL or pass -IndexerBaseUrl."
}
if (-not [string]::IsNullOrWhiteSpace($resolvedIndexerBaseUrl)) {
  try {
    $indexerUri = [Uri]$resolvedIndexerBaseUrl
  }
  catch {
    throw "INDEXER_BASE_URL is not a valid absolute URL: $resolvedIndexerBaseUrl"
  }
  if (-not $indexerUri.IsAbsoluteUri) {
    throw "INDEXER_BASE_URL must be an absolute URL: $resolvedIndexerBaseUrl"
  }
  $indexerHost = ($indexerUri.Host | ForEach-Object { $_.ToLowerInvariant() })
  if ($indexerHost -in @("localhost", "127.0.0.1", "::1")) {
    throw "INDEXER_BASE_URL cannot be localhost for production deployment: $resolvedIndexerBaseUrl"
  }
  $config["INDEXER_BASE_URL"] = $resolvedIndexerBaseUrl
}

$databaseUrl = Get-MapValue -Map $config -Key "DATABASE_URL"
$redisUrl = Get-MapValue -Map $config -Key "REDIS_URL"
if ($databaseUrl -match "@(db|localhost|127\.0\.0\.1)(:|/)") {
  throw "DATABASE_URL points to a local/docker host ('$databaseUrl'). Use a managed Postgres URL reachable from Modal."
}
if ($redisUrl -match "://(redis|localhost|127\.0\.0\.1)(:|/)") {
  throw "REDIS_URL points to a local/docker host ('$redisUrl'). Use a managed Redis URL reachable from Modal."
}

$authMode = Get-MapValue -Map $config -Key "AUTH_MODE"
$authMode = $authMode.Trim().ToLowerInvariant()
if ($authMode -in @("", "disabled", "none")) {
  $config["AUTH_MODE"] = "api_token"
}
if ([string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "API_AUTH_TOKEN"))) {
  $config["API_AUTH_TOKEN"] = New-RandomHexToken -NumBytes 32
  $generatedEnvValues["API_AUTH_TOKEN"] = $config["API_AUTH_TOKEN"]
  Write-Host "Generated API_AUTH_TOKEN for production service calls." -ForegroundColor Yellow
}
$secretKey = Get-MapValue -Map $config -Key "SECRET_KEY"
if ([string]::IsNullOrWhiteSpace($secretKey) -or @("dev-change-me", "change-me") -contains $secretKey.Trim()) {
  $config["SECRET_KEY"] = New-RandomHexToken -NumBytes 48
  $generatedEnvValues["SECRET_KEY"] = $config["SECRET_KEY"]
  Write-Host "Generated production SECRET_KEY." -ForegroundColor Yellow
}
$webhookSecret = Get-MapValue -Map $config -Key "INTEGRATION_WEBHOOK_SECRET"
if ([string]::IsNullOrWhiteSpace($webhookSecret) -or @("dev-webhook-secret", "change-me") -contains $webhookSecret.Trim()) {
  $config["INTEGRATION_WEBHOOK_SECRET"] = New-RandomHexToken -NumBytes 32
  $generatedEnvValues["INTEGRATION_WEBHOOK_SECRET"] = $config["INTEGRATION_WEBHOOK_SECRET"]
  Write-Host "Generated INTEGRATION_WEBHOOK_SECRET." -ForegroundColor Yellow
}

$githubEnabled = Is-Truthy (Get-MapValue -Map $config -Key "GITHUB_ENABLED" -Default "false")
if ($githubEnabled) {
  $ghMode = Get-MapValue -Map $config -Key "GITHUB_AUTH_MODE"
  $ghMode = $ghMode.Trim().ToLowerInvariant()
  if ($ghMode -in @("", "none")) {
    $ghMode = "app"
    $config["GITHUB_AUTH_MODE"] = $ghMode
  }
  if ($ghMode -eq "app") {
    Require-NonEmptyKeys -Map $config -Keys @("GITHUB_APP_ID")
    $inlineKey = Get-MapValue -Map $config -Key "GITHUB_APP_PRIVATE_KEY"
    $inlineKey = $inlineKey.Trim()
    if ([string]::IsNullOrWhiteSpace($inlineKey)) {
      $keyPathValue = Get-MapValue -Map $config -Key "GITHUB_APP_PRIVATE_KEY_PATH"
      $keyPathValue = $keyPathValue.Trim()
      $resolvedKeyPath = Resolve-RepoPath -PathValue $keyPathValue -RepoRoot $repoRoot
      if (-not [string]::IsNullOrWhiteSpace($resolvedKeyPath) -and -not (Test-Path $resolvedKeyPath)) {
        $resolvedKeyPath = ""
      }
      if ([string]::IsNullOrWhiteSpace($resolvedKeyPath)) {
        $fallbackPem = Get-ChildItem -Path (Join-Path $repoRoot "secrets") -Filter "*.pem" -File -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($fallbackPem) {
          $resolvedKeyPath = $fallbackPem.FullName
        }
      }
      if ([string]::IsNullOrWhiteSpace($resolvedKeyPath)) {
        throw "GitHub App mode requires GITHUB_APP_PRIVATE_KEY or a local PEM file path."
      }
      $pem = Get-Content -Path $resolvedKeyPath -Raw -Encoding UTF8
      $pem = $pem.Replace("`r`n", "`n")
      # Store as literal "\n" sequences so runtime can normalize consistently.
      $inlineKey = $pem.Replace("`n", "\n")
      $config["GITHUB_APP_PRIVATE_KEY"] = $inlineKey
      Write-Host "Loaded GitHub App private key from local PEM for Modal secret sync." -ForegroundColor Green
    }
    $config["GITHUB_APP_PRIVATE_KEY_PATH"] = ""
  }

  if (-not [string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "GITHUB_REPO_OWNER")) -or -not [string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "GITHUB_REPO_NAME"))) {
    Write-Host "Clearing GITHUB_REPO_OWNER/GITHUB_REPO_NAME to avoid hardcoded repo targeting." -ForegroundColor Yellow
  }
  $config["GITHUB_REPO_OWNER"] = ""
  $config["GITHUB_REPO_NAME"] = ""

  if (-not [string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "GITHUB_APP_INSTALLATION_ID"))) {
    Write-Host "Clearing GITHUB_APP_INSTALLATION_ID to enforce dynamic per-repo installation lookup." -ForegroundColor Yellow
  }
  $config["GITHUB_APP_INSTALLATION_ID"] = ""
}

$githubUserOauthEnabled = Is-Truthy (Get-MapValue -Map $config -Key "ENABLE_GITHUB_USER_OAUTH" -Default "false")
$githubOauthClientId = (Get-MapValue -Map $config -Key "GITHUB_OAUTH_CLIENT_ID").Trim()
$githubOauthClientSecret = (Get-MapValue -Map $config -Key "GITHUB_OAUTH_CLIENT_SECRET").Trim()
if (-not $githubUserOauthEnabled -and -not [string]::IsNullOrWhiteSpace($githubOauthClientId) -and -not [string]::IsNullOrWhiteSpace($githubOauthClientSecret)) {
  $githubUserOauthEnabled = $true
  $config["ENABLE_GITHUB_USER_OAUTH"] = "true"
}
if ($githubUserOauthEnabled) {
  Require-NonEmptyKeys -Map $config -Keys @("GITHUB_OAUTH_CLIENT_ID", "GITHUB_OAUTH_CLIENT_SECRET")
  if ([string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "GITHUB_USER_TOKEN_ENCRYPTION_KEY"))) {
    $config["GITHUB_USER_TOKEN_ENCRYPTION_KEY"] = New-RandomHexToken -NumBytes 48
    $generatedEnvValues["GITHUB_USER_TOKEN_ENCRYPTION_KEY"] = $config["GITHUB_USER_TOKEN_ENCRYPTION_KEY"]
    Write-Host "Generated GITHUB_USER_TOKEN_ENCRYPTION_KEY for stable GitHub OAuth token encryption." -ForegroundColor Yellow
  }
}

$coderunnerMode = Get-MapValue -Map $config -Key "CODERUNNER_MODE" -Default "opencode"
$coderunnerMode = $coderunnerMode.Trim().ToLowerInvariant()
if ([string]::IsNullOrWhiteSpace($coderunnerMode)) {
  $coderunnerMode = "opencode"
  $config["CODERUNNER_MODE"] = $coderunnerMode
}

if ([string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "OPENROUTER_MINI_MODEL"))) {
  $config["OPENROUTER_MINI_MODEL"] = "qwen/qwen3.5-9b"
}
if ([string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "OPENROUTER_FRONTIER_MODEL"))) {
  $config["OPENROUTER_FRONTIER_MODEL"] = "anthropic/claude-opus-4-6"
}
if ([string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "OPENROUTER_API_KEY"))) {
  Write-Warning "OPENROUTER_API_KEY not set - LLM routing will use fallback rule-based logic"
}

$deployOpenClawAuth = $false
$deployOpenClawLocalDir = ""
if ($coderunnerMode -eq "opencode") {
  $executionMode = Get-MapValue -Map $config -Key "OPENCODE_EXECUTION_MODE" -Default "local_openclaw"
  $executionMode = $executionMode.Trim().ToLowerInvariant()
  if ([string]::IsNullOrWhiteSpace($executionMode)) {
    $executionMode = "local_openclaw"
    $config["OPENCODE_EXECUTION_MODE"] = $executionMode
  }
  if ($executionMode -eq "local_openclaw") {
    $localAuthDir = Get-MapValue -Map $config -Key "MODAL_OPENCLAW_AUTH_LOCAL_DIR" -Default "secrets/openclaw"
    $localAuthDir = $localAuthDir.Trim()
    if ([string]::IsNullOrWhiteSpace($localAuthDir)) {
      $localAuthDir = "secrets/openclaw"
      $config["MODAL_OPENCLAW_AUTH_LOCAL_DIR"] = $localAuthDir
    }
    $resolvedAuthDir = Resolve-RepoPath -PathValue $localAuthDir -RepoRoot $repoRoot
    if (-not (Test-Path $resolvedAuthDir)) {
      throw "OpenClaw auth directory not found: $resolvedAuthDir"
    }
    # Modal runs containers as root (USER in Dockerfile is ignored), so OpenClaw
    # resolves auth under /root by default.
    $config["OPENCLAW_AUTH_DIR"] = "/root/.openclaw"
    $config["OPENCLAW_AUTH_SEED_DIR"] = "/run/secrets/openclaw"
    $config["MODAL_INCLUDE_OPENCLAW_AUTH"] = "true"
    $deployOpenClawAuth = $true
    $deployOpenClawLocalDir = $resolvedAuthDir
    Write-Host "OpenClaw auth seed will be bundled into Modal image from: $resolvedAuthDir" -ForegroundColor Green
  }
}
elseif ($coderunnerMode -eq "native_llm") {
  Require-NonEmptyKeys -Map $config -Keys @("LLM_API_KEY")
}

$slackEnabled = Is-Truthy (Get-MapValue -Map $config -Key "ENABLE_SLACK_BOT" -Default "false")
$slackMode = (Get-MapValue -Map $config -Key "SLACK_MODE" -Default "http").Trim().ToLowerInvariant()
if ([string]::IsNullOrWhiteSpace($slackMode)) {
  $slackMode = "http"
  $config["SLACK_MODE"] = $slackMode
}
$slackOauthEnabled = Is-Truthy (Get-MapValue -Map $config -Key "ENABLE_SLACK_OAUTH" -Default "false")
$slackClientId = (Get-MapValue -Map $config -Key "SLACK_CLIENT_ID").Trim()
$slackClientSecret = (Get-MapValue -Map $config -Key "SLACK_CLIENT_SECRET").Trim()
if (-not $slackOauthEnabled -and -not [string]::IsNullOrWhiteSpace($slackClientId) -and -not [string]::IsNullOrWhiteSpace($slackClientSecret)) {
  $slackOauthEnabled = $true
  $config["ENABLE_SLACK_OAUTH"] = "true"
}
if ($slackEnabled) {
  if ($slackMode -eq "socket") {
    Write-Host "SLACK_MODE=socket is not supported by this Modal API deployment path; forcing SLACK_MODE=http." -ForegroundColor Yellow
    $slackMode = "http"
    $config["SLACK_MODE"] = "http"
  }
  if ($slackMode -ne "http") {
    throw "SLACK_MODE must be 'http' or 'socket' when ENABLE_SLACK_BOT=true."
  }
  if (-not [string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "SLACK_ALLOWED_CHANNELS")) -or -not [string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "SLACK_ALLOWED_USERS"))) {
    Write-Host "Clearing SLACK_ALLOWED_CHANNELS and SLACK_ALLOWED_USERS for workspace-wide bot access." -ForegroundColor Yellow
  }
  $config["SLACK_ALLOWED_CHANNELS"] = ""
  $config["SLACK_ALLOWED_USERS"] = ""
  if (-not [string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "REVIEWER_ALLOWED_USERS"))) {
    Write-Host "Clearing REVIEWER_ALLOWED_USERS to avoid hardcoded reviewer IDs." -ForegroundColor Yellow
  }
  $config["REVIEWER_ALLOWED_USERS"] = ""
  Require-NonEmptyKeys -Map $config -Keys @("SLACK_SIGNING_SECRET")
  if ($slackOauthEnabled) {
    Require-NonEmptyKeys -Map $config -Keys @("SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET", "SLACK_APP_ID")
    if (-not [string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "SLACK_BOT_TOKEN"))) {
      Test-SlackBotToken -Token (Get-MapValue -Map $config -Key "SLACK_BOT_TOKEN")
      Write-Host "Validated fallback SLACK_BOT_TOKEN via Slack auth.test." -ForegroundColor Green
    }
    else {
      Write-Host "Slack OAuth mode enabled without static SLACK_BOT_TOKEN (expected for multi-workspace)." -ForegroundColor Yellow
    }
  }
  else {
    Require-NonEmptyKeys -Map $config -Keys @("SLACK_BOT_TOKEN")
    Test-SlackBotToken -Token (Get-MapValue -Map $config -Key "SLACK_BOT_TOKEN")
    Write-Host "Validated SLACK_BOT_TOKEN via Slack auth.test." -ForegroundColor Green
  }

  if (-not $SkipSlackManifestSync) {
    Require-NonEmptyKeys -Map $config -Keys @("SLACK_APP_ID", "SLACK_APP_CONFIG_TOKEN")
  }
}

if ($generatedEnvValues.Count -gt 0) {
  foreach ($key in $generatedEnvValues.Keys) {
    Set-DotEnvValue -Path $resolvedEnvFile -Key $key -Value ([string]$generatedEnvValues[$key])
  }
  Write-Host "Persisted generated secret values to $resolvedEnvFile for stable redeploys." -ForegroundColor Green
}

Require-NonEmptyKeys -Map $config -Keys @(
  "APP_ENV",
  "AUTH_MODE",
  "API_AUTH_TOKEN",
  "SECRET_KEY",
  "INTEGRATION_WEBHOOK_SECRET",
  "DATABASE_URL",
  "REDIS_URL",
  "BASE_URL",
  "CODERUNNER_MODE"
)

Write-Host "Validating Modal CLI with Python 3.12..." -ForegroundColor Cyan
& py -3.12 -m modal --version | Out-Null
& py -3.12 -m modal profile current | Out-Null

$tempSecretJson = Join-Path $env:TEMP ("feature-factory-modal-secret-" + [guid]::NewGuid().ToString("N") + ".json")
$tempSlackSecretJson = Join-Path $env:TEMP ("feature-factory-slack-secret-" + [guid]::NewGuid().ToString("N") + ".json")
$tempRedisSecretJson = Join-Path $env:TEMP ("feature-factory-redis-secret-" + [guid]::NewGuid().ToString("N") + ".json")
$tempNeonSecretJson = Join-Path $env:TEMP ("feature-factory-neon-secret-" + [guid]::NewGuid().ToString("N") + ".json")
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
try {
  $payload = @{}
  $excludedRuntimeSecretKeys = @("SLACK_APP_CONFIG_TOKEN")
  foreach ($key in $config.Keys) {
    if ($excludedRuntimeSecretKeys -contains $key) {
      continue
    }
    if (Is-ValidEnvKey -Key $key) {
      $payload[$key] = [string]$config[$key]
    }
  }
  [System.IO.File]::WriteAllText($tempSecretJson, ($payload | ConvertTo-Json -Depth 8), $utf8NoBom)

  if (-not $SkipSecretSync) {
    Write-Host "Syncing Modal secret '$ModalSecretName'..." -ForegroundColor Cyan
    & py -3.12 -m modal secret create $ModalSecretName --force --from-json $tempSecretJson
    if ($LASTEXITCODE -ne 0) {
      throw "Failed to sync Modal secret: $ModalSecretName"
    }

    if (-not [string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "SLACK_BOT_TOKEN"))) {
      [System.IO.File]::WriteAllText(
        $tempSlackSecretJson,
        (@{ SLACK_BOT_TOKEN = [string]$config["SLACK_BOT_TOKEN"] } | ConvertTo-Json -Depth 4),
        $utf8NoBom
      )
      Write-Host "Syncing Modal secret 'slack-secret'..." -ForegroundColor Cyan
      & py -3.12 -m modal secret create slack-secret --force --from-json $tempSlackSecretJson
      if ($LASTEXITCODE -ne 0) {
        throw "Failed to sync Modal secret: slack-secret"
      }
    }

    if (-not [string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "REDIS_URL"))) {
      [System.IO.File]::WriteAllText(
        $tempRedisSecretJson,
        (@{ REDIS_URL = [string]$config["REDIS_URL"] } | ConvertTo-Json -Depth 4),
        $utf8NoBom
      )
      Write-Host "Syncing Modal secret 'feature-factory-redis'..." -ForegroundColor Cyan
      & py -3.12 -m modal secret create feature-factory-redis --force --from-json $tempRedisSecretJson
      if ($LASTEXITCODE -ne 0) {
        throw "Failed to sync Modal secret: feature-factory-redis"
      }
    }

    if (-not [string]::IsNullOrWhiteSpace((Get-MapValue -Map $config -Key "DATABASE_URL"))) {
      [System.IO.File]::WriteAllText(
        $tempNeonSecretJson,
        (@{
            NEONURL = [string]$config["DATABASE_URL"]
            DATABASE_URL = [string]$config["DATABASE_URL"]
          } | ConvertTo-Json -Depth 4),
        $utf8NoBom
      )
      Write-Host "Syncing Modal secret 'NeonURL'..." -ForegroundColor Cyan
      & py -3.12 -m modal secret create NeonURL --force --from-json $tempNeonSecretJson
      if ($LASTEXITCODE -ne 0) {
        throw "Failed to sync Modal secret: NeonURL"
      }
    }
  }

  if (-not $SkipDeploy) {
    $deployEnvKeys = @(
      "APP_ENV",
      "MODAL_ENV_SECRET_NAME",
      "MODAL_EXTRA_SECRET_NAMES",
      "MODAL_API_MIN_CONTAINERS",
      "MODAL_API_MAX_CONTAINERS",
      "MODAL_API_ALLOW_CONCURRENT_INPUTS",
      "MODAL_QUEUE_DRAIN_SECONDS",
      "MODAL_CLEANUP_INTERVAL_MINUTES",
      "MODAL_SKIP_WORKER_WHEN_QUEUE_EMPTY"
    )
    foreach ($key in $deployEnvKeys) {
      if ($config.ContainsKey($key)) {
        Set-Item -Path ("Env:" + $key) -Value ([string]$config[$key])
      }
    }
    if ($deployOpenClawAuth) {
      Set-Item -Path "Env:MODAL_INCLUDE_OPENCLAW_AUTH" -Value "true"
      Set-Item -Path "Env:MODAL_OPENCLAW_AUTH_LOCAL_DIR" -Value $deployOpenClawLocalDir
    }

    Write-Host "Deploying Modal app from $ModalAppPath ..." -ForegroundColor Cyan
    & py -3.12 -m modal deploy $ModalAppPath
    if ($LASTEXITCODE -ne 0) {
      throw "Modal deployment failed."
    }

    Write-Host "Running post-deploy health checks..." -ForegroundColor Cyan
    Invoke-WithRetry -Name "health" -Action { Invoke-RestMethod -Method Get -Uri ($resolvedBaseUrl.TrimEnd("/") + "/health") -TimeoutSec 45 }
    Invoke-WithRetry -Name "health/ready" -Action { Invoke-RestMethod -Method Get -Uri ($resolvedBaseUrl.TrimEnd("/") + "/health/ready") -TimeoutSec 45 }
    Invoke-WithRetry -Name "health/runtime" -Action { Invoke-RestMethod -Method Get -Uri ($resolvedBaseUrl.TrimEnd("/") + "/health/runtime") -TimeoutSec 45 }
    if (-not [string]::IsNullOrWhiteSpace($resolvedIndexerBaseUrl)) {
      Invoke-WithRetry -Name "indexer/health" -Action {
        Invoke-RestMethod -Method Get -Uri ($resolvedIndexerBaseUrl.TrimEnd("/") + "/health") -TimeoutSec 45
      }
      Invoke-WithRetry -Name "indexer/search" -Action {
        $headers = @{ "Content-Type" = "application/json" }
        $indexerAuthToken = (Get-MapValue -Map $config -Key "INDEXER_AUTH_TOKEN").Trim()
        if (-not [string]::IsNullOrWhiteSpace($indexerAuthToken)) {
          $headers["Authorization"] = "Bearer $indexerAuthToken"
          $headers["X-FF-Token"] = $indexerAuthToken
        }
        $body = @{
          query = "production indexer health probe"
          top_k_repos = 1
          top_k_chunks = 1
        } | ConvertTo-Json -Depth 4 -Compress
        $response = Invoke-RestMethod -Method Post -Uri ($resolvedIndexerBaseUrl.TrimEnd("/") + "/api/indexer/search") -Headers $headers -Body $body -TimeoutSec 45
        if ($null -eq $response) {
          throw "Indexer search probe returned empty response."
        }
      }
    }
    if ($slackEnabled -and $slackMode -eq "http") {
      Invoke-WithRetry -Name "slack/events route" -Action {
        $probeUrl = $resolvedBaseUrl.TrimEnd("/") + "/api/slack/events"
        $status = -1
        try {
          $status = Get-HttpStatusCode -Url $probeUrl -AllowRedirect $false
        }
        catch {
          $status = Get-StatusCodeFromException -ExceptionObject $_.Exception
          if ($status -lt 0) {
            throw
          }
        }
        if ($status -ne 405) {
          throw "Expected GET $probeUrl to return 405 when Slack events route exists, got status $status."
        }
      }
      if ($slackOauthEnabled) {
        Invoke-WithRetry -Name "slack/oauth install route" -Action {
          $installPath = (Get-MapValue -Map $config -Key "SLACK_OAUTH_INSTALL_PATH" -Default "/api/slack/install").Trim()
          if ([string]::IsNullOrWhiteSpace($installPath)) {
            $installPath = "/api/slack/install"
          }
          if (-not $installPath.StartsWith("/")) {
            $installPath = "/" + $installPath
          }
          $probeUrl = $resolvedBaseUrl.TrimEnd("/") + $installPath
          $status = -1
          try {
            $status = Get-HttpStatusCode -Url $probeUrl -AllowRedirect $false
          }
          catch {
            $status = Get-StatusCodeFromException -ExceptionObject $_.Exception
            if ($status -lt 0) {
              throw
            }
          }
          if ($status -ne 200 -and $status -ne 302 -and $status -ne 303) {
            throw "Expected GET $probeUrl to return 200/302/303, got status $status."
          }
        }
      }
    }
    if ($githubUserOauthEnabled) {
      Invoke-WithRetry -Name "github/oauth install route" -Action {
        $installPath = (Get-MapValue -Map $config -Key "GITHUB_OAUTH_INSTALL_PATH" -Default "/api/github/install").Trim()
        if ([string]::IsNullOrWhiteSpace($installPath)) {
          $installPath = "/api/github/install"
        }
        if (-not $installPath.StartsWith("/")) {
          $installPath = "/" + $installPath
        }
        $probeUrl = $resolvedBaseUrl.TrimEnd("/") + $installPath + "?slack_user_id=healthcheck&slack_team_id=healthcheck"
        $status = -1
        try {
          $status = Get-HttpStatusCode -Url $probeUrl -AllowRedirect $false
        }
        catch {
          $status = Get-StatusCodeFromException -ExceptionObject $_.Exception
          if ($status -lt 0) {
            throw
          }
        }
        if ($status -ne 200 -and $status -ne 302 -and $status -ne 303 -and $status -ne 307 -and $status -ne 308) {
          throw "Expected GET $probeUrl to return 200/302/303/307/308, got status $status."
        }
      }
    }
    if ($slackEnabled -and $slackMode -eq "http" -and -not $SkipSlackManifestSync) {
      Write-Host "Syncing Slack manifest URLs/events/commands..." -ForegroundColor Cyan
      & py -3.12 .\scripts\sync_slack_manifest.py --env-file $resolvedEnvFile --base-url $resolvedBaseUrl --write-rotated-token-to-env
      if ($LASTEXITCODE -ne 0) {
        throw "Slack manifest sync failed. Re-run with -SkipSlackManifestSync for manual Slack configuration."
      }
    }
  }
}
finally {
  Remove-Item -Path $tempSecretJson -ErrorAction SilentlyContinue
  Remove-Item -Path $tempSlackSecretJson -ErrorAction SilentlyContinue
  Remove-Item -Path $tempRedisSecretJson -ErrorAction SilentlyContinue
  Remove-Item -Path $tempNeonSecretJson -ErrorAction SilentlyContinue
}

Write-Host "Modal production deployment flow completed." -ForegroundColor Green
