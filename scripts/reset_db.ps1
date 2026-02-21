# WARNING: Deletes the Postgres volume (all data)
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\reset_db.ps1

docker compose -f docker-compose.yml -f docker-compose.dev.yml down -v
