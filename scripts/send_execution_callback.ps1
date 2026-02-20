# Send a signed execution callback to the orchestrator API.
# Usage example:
# powershell -ExecutionPolicy Bypass -File .\scripts\send_execution_callback.ps1 `
#   -FeatureId "<feature-id>" -Event preview_ready -Secret "dev-webhook-secret" `
#   -PreviewUrl "https://preview.example.com/123" -GithubPrUrl "https://github.com/org/repo/pull/123"

param(
  [Parameter(Mandatory = $true)]
  [string]$FeatureId,

  [Parameter(Mandatory = $true)]
  [ValidateSet("pr_opened", "preview_ready", "build_failed", "preview_failed")]
  [string]$Event,

  [Parameter(Mandatory = $true)]
  [string]$Secret,

  [string]$BaseUrl = "http://localhost:8000",
  [string]$GithubPrUrl = "",
  [string]$PreviewUrl = "",
  [string]$Message = "",
  [string]$ActorId = "local-integration"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$payload = @{
  feature_id = $FeatureId
  event = $Event
  github_pr_url = $GithubPrUrl
  preview_url = $PreviewUrl
  message = $Message
  actor_id = $ActorId
  metadata = @{
    source = "powershell-script"
  }
}

$json = $payload | ConvertTo-Json -Depth 8 -Compress
$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds().ToString()

$toSign = "$timestamp.$json"
$hmac = [System.Security.Cryptography.HMACSHA256]::new([Text.Encoding]::UTF8.GetBytes($Secret))
$hash = $hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($toSign))
$hex = [System.BitConverter]::ToString($hash).Replace("-", "").ToLowerInvariant()
$signature = "sha256=$hex"

$headers = @{
  "X-Feature-Factory-Timestamp" = $timestamp
  "X-Feature-Factory-Signature" = $signature
}

$response = Invoke-RestMethod `
  -Method Post `
  -Uri "$BaseUrl/api/integrations/execution-callback" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $json

$response | ConvertTo-Json -Depth 8
