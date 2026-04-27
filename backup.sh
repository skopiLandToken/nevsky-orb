#!/bin/bash
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/opt/nevsky-dev/backups"
BACKUP_FILE="$BACKUP_DIR/nevsky_dev_$TIMESTAMP.sql.gz"

# Keep only last 7 days of backups
find $BACKUP_DIR -name "*.sql.gz" -mtime +7 -delete

# Run the backup
docker compose -f /opt/nevsky-dev/docker-compose.yml exec -T postgres \
  pg_dump -U nevsky nevsky_dev | gzip > $BACKUP_FILE

if [ $? -eq 0 ]; then
  echo "$(date): Backup successful — $BACKUP_FILE ($(du -h $BACKUP_FILE | cut -f1))"
else
  echo "$(date): Backup FAILED"
fi
