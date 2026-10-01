#!/usr/bin/env bash
# Re-runnable restore drill (T13 / Test T23 evidence).
#
#   sudo bash deploy/restore_drill.sh
#
# Seeds realistic business data (users, contacts, private/group conversations,
# messages with per-viewer copies, an encrypted file with real ciphertext bytes
# and per-holder wrapped keys), takes a fresh backup, then runs the ACTUAL
# deploy/restore.sh tool — via its drill overrides — into a scratch database
# and scratch media directory. Verifies row parity, relational integrity,
# ciphertext byte parity and the encrypted-file digest. The live service and
# live media are never touched; drill artifacts are cleaned up.
set -euo pipefail

APP_DIR=/opt/ichat
REPO_DIR=$APP_DIR/repo
SCRATCH_DB=ichat_restore
SCRATCH_MEDIA=/tmp/ichat-drill-media
DRILL_ENV=/tmp/ichat-drill.env
FAIL=0
note() { printf '  %s\n' "$*"; }
check() { if [ "$2" = "$3" ]; then note "OK   $1 ($2)"; else note "FAIL $1 (got=$2 want=$3)"; FAIL=1; fi; }

set -a; source $APP_DIR/.env; set +a
VENV=/opt/ichat/.venv/bin
RUNAS="sudo -u ichat -E env PATH=$VENV:/usr/bin:/bin DJANGO_SETTINGS_MODULE=ichat_pro.settings"

echo "== 1. seed business data (live db) =="
cd "$REPO_DIR"
$RUNAS $VENV/python manage.py shell < deploy/seed_drill_data.py 2>/dev/null | grep SEEDED || {
    echo "ERROR: seeding failed" >&2; exit 1; }

echo "== 2. fresh backup =="
"$REPO_DIR/deploy/backup.sh" >/dev/null
BK=/var/backups/ichat/$(ls -1t /var/backups/ichat | head -1)
(cd "$BK" && sha256sum -c SHA256SUMS >/dev/null) && note "checksums OK ($BK)"

echo "== 3. run actual restore.sh into scratch (drill overrides) =="
sudo -u postgres psql -c "DROP DATABASE IF EXISTS $SCRATCH_DB" >/dev/null
sudo -u postgres psql -c "CREATE DATABASE $SCRATCH_DB OWNER ichat" >/dev/null
rm -rf "$SCRATCH_MEDIA"
printf 'DATABASE_URL=%s/%s\nDJANGO_ALLOWED_HOSTS=%s\n' \
    "${DATABASE_URL%/ichat}" "$SCRATCH_DB" "$DJANGO_ALLOWED_HOSTS" > "$DRILL_ENV"
RESTORE_ENV_FILE=$DRILL_ENV ICHAT_MEDIA_DIR=$SCRATCH_MEDIA ICHAT_SKIP_SERVICE=1 ICHAT_ASSUME_YES=1 \
    "$REPO_DIR/deploy/restore.sh" "$BK"

echo "== 4. verification =="
psql_live() { sudo -u postgres psql -d ichat -tAc "$1" | tr -d '[:space:]'; }
psql_rest() { sudo -u postgres psql -d "$SCRATCH_DB" -tAc "$1" | tr -d '[:space:]'; }

declare -A WANT=(
    [auth_user]=3 [accounts_contact]=2
    [chat_conversation]=2 [chat_conversationmember]=5
    [chat_encryptedmessage]=3 [chat_groupmessage]=1 [chat_groupmessagerecipient]=3
    [chat_encryptedfile]=1 [chat_encryptedfilekey]=2
)
for T in auth_user accounts_contact chat_conversation chat_conversationmember \
         chat_encryptedmessage chat_groupmessage chat_groupmessagerecipient \
         chat_encryptedfile chat_encryptedfilekey; do
    A=$(psql_live "SELECT count(*) FROM $T")
    B=$(psql_rest "SELECT count(*) FROM $T")
    check "row parity $T (live/restored)" "$A/$B" "${WANT[$T]}/${WANT[$T]}"
done

note "relational integrity (each count must be 0):"
check "  private msgs with missing member row" \
    "$(psql_rest "SELECT count(*) FROM chat_encryptedmessage m WHERE NOT EXISTS
        (SELECT 1 FROM chat_conversationmember cm WHERE cm.conversation_id=m.conversation_id
         AND cm.user_id=m.sender_id AND cm.status='active')
        OR NOT EXISTS (SELECT 1 FROM chat_conversationmember cm
        WHERE cm.conversation_id=m.conversation_id AND cm.user_id=m.receiver_id AND cm.status='active')")" 0
check "  group msgs whose recipients != active members" \
    "$(psql_rest "SELECT count(*) FROM chat_groupmessage gm WHERE
        (SELECT count(*) FROM chat_conversationmember cm WHERE cm.conversation_id=gm.conversation_id AND cm.status='active')
        <> (SELECT count(*) FROM chat_groupmessagerecipient r WHERE r.group_message_id=gm.id)")" 0
check "  files whose wrapped keys != active members" \
    "$(psql_rest "SELECT count(*) FROM chat_encryptedfile f WHERE
        (SELECT count(*) FROM chat_conversationmember cm WHERE cm.conversation_id=f.conversation_id AND cm.status='active')
        <> (SELECT count(*) FROM chat_encryptedfilekey k WHERE k.file_id=f.id)")" 0
check "  orphan messages (no conversation)" \
    "$(psql_rest "SELECT count(*) FROM chat_encryptedmessage m WHERE NOT EXISTS
        (SELECT 1 FROM chat_conversation c WHERE c.id=m.conversation_id)")" 0

note "ciphertext byte parity (md5 over column, ordered by id):"
check "  private ciphertext" \
    "$(psql_rest "SELECT md5(string_agg(ciphertext, '' ORDER BY id)) FROM chat_encryptedmessage WHERE ciphertext IS NOT NULL")" \
    "$(psql_live "SELECT md5(string_agg(ciphertext, '' ORDER BY id)) FROM chat_encryptedmessage WHERE ciphertext IS NOT NULL")"
check "  private sender_copy" \
    "$(psql_rest "SELECT md5(string_agg(sender_copy_ciphertext, '' ORDER BY id)) FROM chat_encryptedmessage WHERE sender_copy_ciphertext IS NOT NULL")" \
    "$(psql_live "SELECT md5(string_agg(sender_copy_ciphertext, '' ORDER BY id)) FROM chat_encryptedmessage WHERE sender_copy_ciphertext IS NOT NULL")"
check "  group recipient ciphertext" \
    "$(psql_rest "SELECT md5(string_agg(ciphertext, '' ORDER BY group_message_id, receiver_id)) FROM chat_groupmessagerecipient")" \
    "$(psql_live "SELECT md5(string_agg(ciphertext, '' ORDER BY group_message_id, receiver_id)) FROM chat_groupmessagerecipient")"

DIG=$(psql_rest "SELECT ciphertext_sha256 FROM chat_encryptedfile WHERE client_file_id='p4drill-file-001'")
REAL=$(sha256sum "$SCRATCH_MEDIA/media/uploads/files/p4drill.enc" | cut -d' ' -f1)
check "encrypted-file digest (restored bytes vs stored ciphertext_sha256)" "$REAL" "$DIG"

echo "== 5. cleanup =="
sudo -u postgres psql -c "DROP DATABASE $SCRATCH_DB" >/dev/null
rm -rf "$SCRATCH_MEDIA" "$DRILL_ENV"
$RUNAS $VENV/python manage.py shell <<'PYEOF' >/dev/null 2>&1
import os
from django.conf import settings
from django.contrib.auth import get_user_model
from chat.models import Conversation, ConversationMember
names = ["p4_drill_a", "p4_drill_b", "p4_drill_c"]
prev = get_user_model().objects.filter(username__in=names)
conv_ids = list(ConversationMember.objects.filter(user__in=prev).values_list("conversation_id", flat=True))
Conversation.objects.filter(id__in=conv_ids).delete()
prev.delete()
p = os.path.join(settings.MEDIA_ROOT, "uploads", "files", "p4drill.enc")
if os.path.exists(p):
    os.remove(p)
print("cleaned")
PYEOF
note "scratch database/media removed; drill data deleted from live db"

if [ "$FAIL" = 0 ]; then echo "RESTORE-DRILL-PASSED"; else echo "RESTORE-DRILL-FAILED" >&2; exit 1; fi
