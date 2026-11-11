#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "backups/${stamp}"
docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-docs}" "${POSTGRES_DB:-docs}" > "backups/${stamp}/database.sql"
docker compose exec -T api tar -C /data -cf - storage > "backups/${stamp}/storage.tar"
echo "Backup written to backups/${stamp}"
