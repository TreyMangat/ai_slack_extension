# Stop the Feature Factory stack
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\stop_local.ps1

docker compose -f docker-compose.yml -f docker-compose.dev.yml down
