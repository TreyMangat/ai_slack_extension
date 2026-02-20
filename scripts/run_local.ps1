# Run the Feature Factory stack locally
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\run_local.ps1

if (!(Test-Path .env)) {
  Write-Host "[!] .env not found. Copying .env.example to .env" -ForegroundColor Yellow
  Copy-Item .env.example .env
}

Write-Host "Starting docker compose..." -ForegroundColor Cyan
docker compose up --build
