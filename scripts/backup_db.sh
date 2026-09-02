#!/bin/bash
# PostgreSQL backup for Build Center.
#
# Why this exists: the database holds things that cannot be rebuilt from code
# -- 670 build-history records, users and roles, audit logs, login records.
# Workspaces and artifacts have a TTL and are cleaned up automatically; rerun a
# build and they come back. These do not. At the time of writing this system
# had no backup mechanism at all.
#
# Usage:
#   bash scripts/backup_db.sh                    # back up to the default dir
#   BACKUP_DIR=/mnt/nas/bc bash scripts/backup_db.sh
#
# Recommended cron (daily at 03:30):
#   30 3 * * * cd /media/disk0/Tony/devops/build_center && bash scripts/backup_db.sh >> /var/log/build_center_backup.log 2>&1

set -euo pipefail

cd "$(dirname "$0")/.."

# Get connection info from the application's own Settings instead of re-parsing
# .env here. .env currently has no DB_* lines at all -- the service runs on the
# defaults in app/config.py, so a script that parses .env itself would get empty
# strings and fail. Any approach with a second source of config drifts like this
# sooner or later.
read -r DB_HOST DB_PORT DB_NAME DB_USER <<<"$(uv run python -c "
from app.config import get_settings
s = get_settings()
print(s.db_host, s.db_port, s.db_name, s.db_user)")"

BACKUP_DIR="${BACKUP_DIR:-/media/disk0/Tony/devops/build_center_backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"   # dumps contain password hashes and the env content users pasted

STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$BACKUP_DIR/${DB_NAME}-${STAMP}.sql.gz"

echo "[$(date '+%F %T')] Backing up ${DB_NAME}@${DB_HOST}:${DB_PORT} → $OUT"

# PGPASSWORD reaches pg_dump through the environment, never through a command
# line — argv is visible to every user on the box via `ps`. Command substitution
# keeps it out of the shell's history and out of this script's own output.
PGPASSWORD="$(uv run python -c "from app.config import get_settings; print(get_settings().db_password)")" \
    pg_dump --host="$DB_HOST" --port="$DB_PORT" --username="$DB_USER" \
            --dbname="$DB_NAME" --format=plain --no-owner --no-privileges \
    | gzip -9 > "$OUT.partial"

# Only take the final name once the dump completed, so a crashed run can never
# leave a truncated file that looks like a good backup.
mv "$OUT.partial" "$OUT"
chmod 600 "$OUT"

SIZE=$(du -h "$OUT" | cut -f1)
echo "[$(date '+%F %T')] OK — $SIZE"

# Rotation. Runs after a successful dump only: if pg_dump failed, set -e already
# aborted, so a broken database can never delete the last good backups.
DELETED=$(find "$BACKUP_DIR" -maxdepth 1 -name "${DB_NAME}-*.sql.gz" -type f \
               -mtime "+${RETENTION_DAYS}" -print -delete | wc -l)
[ "$DELETED" -gt 0 ] && echo "[$(date '+%F %T')] Rotated out $DELETED backup(s) older than ${RETENTION_DAYS}d"

REMAINING=$(find "$BACKUP_DIR" -maxdepth 1 -name "${DB_NAME}-*.sql.gz" -type f | wc -l)
echo "[$(date '+%F %T')] $REMAINING backup(s) retained in $BACKUP_DIR"

# Restore:
#   gunzip -c <file> | psql -h HOST -U USER -d DBNAME
