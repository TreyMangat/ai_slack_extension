# Run the Feature Factory stack locally
# Usage:
# - powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1
# - powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1 -WithSlack
# - powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1 -WithSlack -WithIndexer

param(
  [switch]$WithSlack,
  [switch]$WithIndexer
)

if (!(Test-Path .env)) {
  Write-Host "[!] .env not found. Copying .env.example to .env" -ForegroundColor Yellow
  Copy-Item .env.example .env
}

Write-Host "Starting docker compose..." -ForegroundColor Cyan
$composeArgs = @("-f", "docker-compose.yml", "-f", "docker-compose.dev.yml")
if ($WithIndexer) {
  $composeArgs += @("-f", "docker-compose.indexer.yml")
  Write-Host "Including Repo_Indexer services. Set INDEXER_BASE_URL=http://indexer-api:8080 in .env for container-to-container calls." -ForegroundColor Yellow
}
if ($WithSlack) {
  $composeArgs += @("--profile", "slack")
}
$composeArgs += @("up", "--build")

docker compose @composeArgs
