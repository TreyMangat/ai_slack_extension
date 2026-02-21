# Validate GitHub App installation and permissions using current .env settings.
# Runs inside the API container so no local Python setup is required.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\check_github_app.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (!(Test-Path .env)) {
  Write-Error ".env not found in repo root. Copy .env.example to .env first."
}

$composeArgs = @("-f", "docker-compose.yml", "-f", "docker-compose.dev.yml")
$composeArgs += @("run", "--rm", "--no-deps", "api", "python", "-m", "app.tools.github_app_doctor")

docker compose @composeArgs
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

