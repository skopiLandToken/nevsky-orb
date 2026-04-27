#!/usr/bin/env bash
# nightly_backup.sh — pg_dump all Nevsky databases, compress, prune after 14 days
set -euo pipefail

BACKUP_DIR="/opt/nevsky-dev/backups/postgres"
DATE=$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP_DIR"

cd /opt/nevsky-dev

for DB in nevsky_dev umami_db nextcloud_db; do
    echo "[$(date -Iseconds)] Backing up $DB..."
    docker compose exec -T postgres pg_dump -U nevsky -d "$DB" \
        --clean --if-exists --no-owner --no-acl \
        | gzip -9 > "$BACKUP_DIR/${DB}-${DATE}.sql.gz"
    echo "  wrote $BACKUP_DIR/${DB}-${DATE}.sql.gz ($(du -h "$BACKUP_DIR/${DB}-${DATE}.sql.gz" | cut -f1))"
done

# Prune backups older than 14 days
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +14 -delete
echo "[$(date -Iseconds)] Backup complete. Retention: 14 days."
