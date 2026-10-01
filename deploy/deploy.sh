#!/usr/bin/env bash
# iChat Pro server-side deploy script (run on the server as root or via sudo).
# Usage:
#   sudo ./deploy.sh                     # deploy origin/main
#   sudo ./deploy.sh --branch main       # deploy a branch head
#   sudo ./deploy.sh --commit <sha>      # ROLLBACK: deploy an exact commit,
#                                        #   stays pinned (no reset to origin)
# Every deploy appends "<utc> <sha> <target>" to /opt/ichat/DEPLOYMENTS.log.
set -euo pipefail

APP_DIR=/opt/ichat
REPO_DIR=$APP_DIR/repo
VENV_DIR=$APP_DIR/.venv
MEDIA_DIR=/var/lib/ichat/media
LOG_FILE=$APP_DIR/DEPLOYMENTS.log
BRANCH=main
COMMIT=
while [[ $# -gt 0 ]]; do
    case "$1" in
        --branch) BRANCH="${2:?--branch needs a value}"; shift 2 ;;
        --commit) COMMIT="${2:?--commit needs a value}"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

echo "==> Deploying iChat Pro target=${COMMIT:+$BRANCH@$COMMIT}${COMMIT:-$BRANCH}"

if [[ ! -f "$APP_DIR/.env" ]]; then
    echo "ERROR: $APP_DIR/.env missing. Create it from deploy/env.production.example first." >&2
    exit 1
fi

# --- code ---
git -C "$REPO_DIR" fetch --quiet origin "$BRANCH"
if [[ -n "$COMMIT" ]]; then
    git -C "$REPO_DIR" checkout --quiet --force "$COMMIT"
else
    git -C "$REPO_DIR" checkout --quiet --force "$BRANCH"
    git -C "$REPO_DIR" reset --quiet --hard "origin/$BRANCH"
fi
DEPLOYED_SHA=$(git -C "$REPO_DIR" rev-parse HEAD)

# --- python env ---
[[ -d "$VENV_DIR" ]] || python3.13 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$REPO_DIR/requirements.txt"

# --- state dirs ---
install -d -o ichat -g ichat "$MEDIA_DIR" "$MEDIA_DIR/avatars" "$MEDIA_DIR/uploads"

# --- config validation before touching the running service ---
set -a; source "$APP_DIR/.env"; set +a
"$VENV_DIR/bin/python" "$REPO_DIR/manage.py" check
"$VENV_DIR/bin/python" "$REPO_DIR/manage.py" makemigrations --check --dry-run

# --- migrate / collect ---
"$VENV_DIR/bin/python" "$REPO_DIR/manage.py" migrate --noinput
"$VENV_DIR/bin/python" "$REPO_DIR/manage.py" collectstatic --noinput --clear

echo "==> Restarting service"
systemctl restart ichat.service
systemctl enable ichat.service >/dev/null 2>&1 || true

# --- readiness gate ---
# Production rejects unknown Host headers and (without an exemption) redirects
# http->https, so probe with the real production Host, expect the readiness
# JSON body, and never trust a bare connection success (a 301/400 body would
# pass a plain `curl -f`).
ALLOWED_HOST=${DJANGO_ALLOWED_HOSTS%%,*}
BODY=""
for i in $(seq 1 30); do
    BODY=$(curl -fsS --max-time 5 \
        -H "Host: $ALLOWED_HOST" \
        http://127.0.0.1:8000/health/ready/ 2>/dev/null || true)
    if [[ "$BODY" == *'"status": "ok"'* ]]; then
        echo "==> Deploy OK: commit=$DEPLOYED_SHA (readiness: db+cache ok)"
        printf '%s  %s  %s\n' "$(date -u +%FT%TZ)" "$DEPLOYED_SHA" \
            "${COMMIT:+$BRANCH@$COMMIT}" "${COMMIT:-$BRANCH}" >> "$LOG_FILE"
        exit 0
    fi
    sleep 1
done
echo "ERROR: readiness probe failed after restart (last body: ${BODY:-none}); check 'journalctl -u ichat -n 50'" >&2
exit 1
