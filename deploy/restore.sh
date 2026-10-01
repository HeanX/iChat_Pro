#!/usr/bin/env bash
# Restore PostgreSQL + media from a backup made by backup.sh.
# Restores into the CURRENT database named by .env — verify the target first.
# Usage: sudo ./restore.sh /var/backups/ichat/<STAMP>
set -euo pipefail

APP_DIR=/opt/ichat
MEDIA_DIR=/var/lib/ichat/media
STAMP_DIR=${1:?usage: restore.sh /var/backups/ichat/<STAMP>}

[[ -d "$STAMP_DIR" ]] || { echo "ERROR: backup dir not found: $STAMP_DIR" >&2; exit 1; }

echo "==> verifying checksums"
(cd "$STAMP_DIR" && sha256sum -c SHA256SUMS)

set -a; source "$APP_DIR/.env"; set +a
DB_URL=${DATABASE_URL:?DATABASE_URL not set}
DB_NAME=$(python3 -c "import sys,urllib.parse as u; p=u.urlparse('$DB_URL'); print(u.unquote(p.path.lstrip('/')))")

echo "==> target database: $DB_NAME (from .env)"
read -r -p "Type the database name to confirm restore: " confirm
[[ "$confirm" == "$DB_NAME" ]] || { echo "aborted"; exit 1; }

echo "==> stopping app service"
systemctl stop ichat.service

echo "==> restoring database"
psql "$DB_URL" -c 'SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid();'
psql "$DB_URL" -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
psql --set ON_ERROR_STOP=1 --single-transaction --file "$STAMP_DIR/db.sql" "$DB_URL"

echo "==> restoring media (existing media/ is moved aside)"
if [[ -d "$MEDIA_DIR" ]]; then
    mv "$MEDIA_DIR" "${MEDIA_DIR}.pre-restore-$(date -u +%Y%m%dT%H%M%SZ)"
fi
tar -C "$(dirname "$MEDIA_DIR")" -xzf "$STAMP_DIR/media.tar.gz"

echo "==> starting app service and probing"
systemctl start ichat.service
ALLOWED_HOST=${DJANGO_ALLOWED_HOSTS%%,*}
BODY=""
for i in $(seq 1 30); do
    BODY=$(curl -fsS --max-time 5 \
        -H "Host: $ALLOWED_HOST" \
        http://127.0.0.1:8000/health/ready/ 2>/dev/null || true)
    if [[ "$BODY" == *'"status": "ok"'* ]]; then
        echo "==> Restore OK (readiness: db+cache ok)"
        exit 0
    fi
    sleep 1
done
echo "ERROR: readiness probe failed after restore (last body: ${BODY:-none})" >&2
exit 1
