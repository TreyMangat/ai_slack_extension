# Run the Feature Factory stack locally
# Usage:
# - powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1
# - powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1 -WithSlack

param(
  [switch]$WithSlack
)

if (!(Test-Path .env)) {
  Write-Host "[!] .env not found. Copying .env.example to .env" -ForegroundColor Yellow
  Copy-Item .env.example .env
}

Write-Host "Starting docker compose..." -ForegroundColor Cyan
$composeArgs = @("-f", "docker-compose.yml", "-f", "docker-compose.dev.yml")
if ($WithSlack) {
  $composeArgs += @("--profile", "slack")
}
$composeArgs += @("up", "--build")

docker compose @composeArgs
