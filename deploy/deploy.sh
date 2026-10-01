#!/usr/bin/env bash
# iChat Pro server-side deploy script (run on the server as root or via sudo).
# Usage: sudo ./deploy.sh [--branch main]
set -euo pipefail

APP_DIR=/opt/ichat
VENV_DIR=$APP_DIR/.venv
MEDIA_DIR=/var/lib/ichat/media
BRANCH=main
[[ "${1:-}" == "--branch" ]] && BRANCH="${2:-main}"

echo "==> Deploying iChat Pro branch=$BRANCH"

if [[ ! -f "$APP_DIR/.env" ]]; then
    echo "ERROR: $APP_DIR/.env missing. Create it from deploy/env.production.example first." >&2
    exit 1
fi

# --- code ---
if [[ -d "$APP_DIR/repo/.git" ]]; then
    git -C "$APP_DIR/repo" fetch --quiet origin "$BRANCH"
    git -C "$APP_DIR/repo" checkout --quiet "$BRANCH"
    git -C "$APP_DIR/repo" reset --quiet --hard "origin/$BRANCH"
else
    git clone --branch "$BRANCH" https://github.com/HeanX/iChat_Pro.git "$APP_DIR/repo"
fi

# --- python env ---
[[ -d "$VENV_DIR" ]] || python3.13 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$APP_DIR/repo/requirements.txt"

# --- state dirs ---
install -d -o ichat -g ichat "$MEDIA_DIR" "$MEDIA_DIR/avatars" "$MEDIA_DIR/uploads"

# --- config validation before touching the running service ---
set -a; source "$APP_DIR/.env"; set +a
"$VENV_DIR/bin/python" "$APP_DIR/repo/manage.py" check
"$VENV_DIR/bin/python" "$APP_DIR/repo/manage.py" makemigrations --check --dry-run

# --- migrate / collect ---
"$VENV_DIR/bin/python" "$APP_DIR/repo/manage.py" migrate --noinput
"$VENV_DIR/bin/python" "$APP_DIR/repo/manage.py" collectstatic --noinput --clear

echo "==> Restarting service"
systemctl restart ichat.service
systemctl enable ichat.service >/dev/null 2>&1 || true
systemctl --no-pager --lines=5 status ichat.service || true

for i in $(seq 1 20); do
    if curl -fsS http://127.0.0.1:8000/health/ready/ >/dev/null 2>&1; then
        echo "==> Deploy OK: readiness probe passed"
        exit 0
    fi
    sleep 1
done
echo "ERROR: readiness probe failed after restart; check 'journalctl -u ichat -n 50'" >&2
exit 1
