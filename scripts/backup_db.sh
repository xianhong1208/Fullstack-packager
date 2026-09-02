#!/bin/bash
# PostgreSQL backup for Build Center.
#
# 為什麼需要這個:資料庫裡是「無法從程式碼重建」的東西 —— 670 筆建置歷史、
# 使用者與角色、稽核日誌、登入紀錄。工作區與產出都有 TTL 會自動清掉,重跑
# 建置就能重生;這些不行。掃描時這個系統完全沒有任何備份機制。
#
# 用法:
#   bash scripts/backup_db.sh                    # 備份到預設目錄
#   BACKUP_DIR=/mnt/nas/bc bash scripts/backup_db.sh
#
# 建議掛 cron(每天 03:30):
#   30 3 * * * cd /media/disk0/Tony/devops/build_center && bash scripts/backup_db.sh >> /var/log/build_center_backup.log 2>&1

set -euo pipefail

cd "$(dirname "$0")/.."

# 連線資訊向應用程式自己的 Settings 拿,而不是在這裡重新 parse .env。
# .env 目前根本沒有 DB_* 那幾行 —— 服務跑的是 app/config.py 的預設值,所以
# 自行 parse .env 的腳本會拿到空字串然後失敗。任何「設定有第二個來源」的
# 做法遲早都會這樣漂移。
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

# 還原:
#   gunzip -c <檔案> | psql -h HOST -U USER -d DBNAME
