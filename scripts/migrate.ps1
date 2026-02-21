# Run Alembic migrations against the configured database.
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\migrate.ps1

param(
  [switch]$BootstrapStamp
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (!(Test-Path .env)) {
  Write-Host "[!] .env not found. Copying .env.example to .env" -ForegroundColor Yellow
  Copy-Item .env.example .env
}

Write-Host "Running migration path (init_db with RUN_MIGRATIONS=true)..." -ForegroundColor Cyan
$envArgs = @("-e", "RUN_MIGRATIONS=true")
if ($BootstrapStamp) {
  $envArgs += @("-e", "MIGRATION_BOOTSTRAP_STAMP=true")
}
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm @envArgs api python -c "from app.db import init_db; init_db()"
if ($LASTEXITCODE -ne 0) {
  throw "Migration command failed with exit code $LASTEXITCODE"
}
