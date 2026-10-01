#!/usr/bin/env bash
# Backup PostgreSQL + persisted media. Run as root (e.g. daily via cron).
# Usage: sudo ./backup.sh [backup_dir]
set -euo pipefail

APP_DIR=/opt/ichat
MEDIA_DIR=/var/lib/ichat/media
BACKUP_ROOT=${1:-/var/backups/ichat}
KEEP_DAYS=14
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST=$BACKUP_ROOT/$STAMP

set -a; source "$APP_DIR/.env"; set +a
DB_URL=${DATABASE_URL:?DATABASE_URL not set}
# Only postgres backups are supported by this script.
[[ "$DB_URL" == postgres* ]] || { echo "ERROR: backup expects PostgreSQL" >&2; exit 1; }

install -d -m 700 "$DEST"

echo "==> pg_dump"
pg_dump --no-owner --no-privileges --file "$DEST/db.sql" "$DB_URL"

echo "==> media archive (avatars + encrypted uploads)"
tar -C "$(dirname "$MEDIA_DIR")" -czf "$DEST/media.tar.gz" "$(basename "$MEDIA_DIR")"

# Checksum with RELATIVE names so a copied/moved backup directory can be
# verified on its own (absolute paths would keep validating the original
# files instead of the copy).
(
    cd "$DEST"
    sha256sum db.sql media.tar.gz > SHA256SUMS
)
printf 'commit=%s\ncreated=%s\n' \
    "$(git -C "$APP_DIR/repo" rev-parse HEAD 2>/dev/null || echo unknown)" \
    "$STAMP" > "$DEST/META"

echo "==> pruning backups older than $KEEP_DAYS days"
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime "+$KEEP_DAYS" -exec rm -rf {} +

echo "==> Backup complete: $DEST"
ls -lh "$DEST"
