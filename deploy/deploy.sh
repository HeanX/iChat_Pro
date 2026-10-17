#!/usr/bin/env bash
# iChat Pro server-side deploy script (run on the server as root or via sudo).
#
# Canonical entry point: /opt/ichat/bin/deploy.sh (a copy OUTSIDE the repo
# checkout). Invoking the repo copy re-execs the stable copy first, so a
# rollback to an older commit (which rewinds deploy/deploy.sh inside the
# repo) can never break or downgrade the deployment tooling mid-run.
#
# Usage:
#   sudo /opt/ichat/bin/deploy.sh                     # deploy origin/main
#   sudo /opt/ichat/bin/deploy.sh --branch main
#   sudo /opt/ichat/bin/deploy.sh --commit <sha>      # ROLLBACK: deploys an
#       exact commit and stays pinned (no reset back to origin/main)
#
# Two gates must pass before a deploy counts as successful:
#   1. readiness  — /health/ready/ answers 200 with {"status": "ok"}
#   2. smoke      — deploy/smoke.sh proves login + core APIs still work
#                   (P4 Test T23 / #204). Failing either exits non-zero and
#                   appends NOTHING to DEPLOYMENTS.log.
#
# Every successful deploy appends "<utc> <sha> <target> <runtime>" to
# /opt/ichat/DEPLOYMENTS.log — the server-side deployed SHA is recorded
# there, independently of the repository's main branch.
set -euo pipefail

APP_DIR=/opt/ichat
REPO_DIR=$APP_DIR/repo
VENV_DIR=$APP_DIR/.venv
MEDIA_DIR=/var/lib/ichat/media
LOG_FILE=$APP_DIR/DEPLOYMENTS.log
BIN_COPY=$APP_DIR/bin/deploy.sh

# Re-exec from the stable copy (except when we ARE the stable copy).
if [[ "$0" != "$BIN_COPY" ]]; then
    mkdir -p "$(dirname "$BIN_COPY")"
    install -m 755 "$0" "$BIN_COPY"
    exec bash "$BIN_COPY" "$@"
fi

BRANCH=main
COMMIT=
while [[ $# -gt 0 ]]; do
    case "$1" in
        --branch) BRANCH="${2:?--branch needs a value}"; shift 2 ;;
        --commit) COMMIT="${2:?--commit needs a value}"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done
if [[ -n "$COMMIT" ]]; then
    TARGET="$BRANCH@$COMMIT"
else
    TARGET="$BRANCH"
fi

echo "==> Deploying iChat Pro target=$TARGET"

if [[ ! -f "$APP_DIR/.env" ]]; then
    echo "ERROR: $APP_DIR/.env missing. Create it from deploy/env.production.example first." >&2
    exit 1
fi

# --- code (bootstrap clone on first deploy, then fetch/pin) ---
if [[ ! -d "$REPO_DIR/.git" ]]; then
    echo "==> First deploy: cloning repository into $REPO_DIR"
    git clone --quiet --branch "$BRANCH" https://github.com/HeanX/iChat_Pro.git "$REPO_DIR"
fi
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
# Primary: probe through the local Nginx TLS front door — works for ANY
# deployed version (old versions redirect plain http to https) and exercises
# the full request path (TLS, proxy, app, DB, Redis).
# Fallback: when nothing listens on 8443 (fresh host before Nginx is set up),
# probe Daphne over loopback http with the production Host header; this is
# only valid on versions that exempt health paths from the SSL redirect.
# Both paths assert the readiness JSON body — never trust a bare connection
# success (a 301/400 body would pass a plain `curl -f`).
ALLOWED_HOST=${DJANGO_ALLOWED_HOSTS%%,*}
BODY=""
for i in $(seq 1 30); do
    BODY=$(curl -fkS --max-time 5 \
        -H "Host: $ALLOWED_HOST" \
        https://127.0.0.1:8443/health/ready/ 2>/dev/null || true)
    if [[ "$BODY" != *'"status": "ok"'* ]]; then
        BODY=$(curl -fsS --max-time 5 \
            -H "Host: $ALLOWED_HOST" \
            http://127.0.0.1:8000/health/ready/ 2>/dev/null || true)
    fi
    if [[ "$BODY" == *'"status": "ok"'* ]]; then
        echo "==> Readiness OK: db+cache reachable"
        READY=1
        break
    fi
    sleep 1
done
if [[ "${READY:-0}" != "1" ]]; then
    echo "ERROR: readiness probe failed after restart (last body: ${BODY:-none}); check 'journalctl -u ichat -n 50'" >&2
    exit 1
fi

# --- smoke gate (P4 Test T23 / #204) ---
# Readiness only proves the process answers; it does not prove login, CSRF,
# sessions or the authenticated APIs work. A release that cannot log in is a
# failed release, so it must not be recorded in DEPLOYMENTS.log.
# Opt out only for a known-broken hotfix: ICHAT_SKIP_SMOKE=1 (logged loudly).
if [[ "${ICHAT_SKIP_SMOKE:-0}" == "1" ]]; then
    echo "WARNING: ICHAT_SKIP_SMOKE=1 — login/core-API smoke gate SKIPPED for this deploy." >&2
else
    if [[ -z "${SMOKE_USERNAME:-}" || -z "${SMOKE_PASSWORD:-}" ]]; then
        echo "ERROR: SMOKE_USERNAME/SMOKE_PASSWORD are not set in $APP_DIR/.env." >&2
        echo "       The post-deploy smoke gate cannot authenticate, so the release" >&2
        echo "       is not recorded. Create the dedicated smoke account (runbook §7)" >&2
        echo "       or set ICHAT_SKIP_SMOKE=1 for a known-broken hotfix." >&2
        exit 1
    fi
    echo "==> Post-deploy smoke gate"
    AUTO_BASE_URL=1 SMOKE_INSECURE_TLS=1 \
        bash "$REPO_DIR/deploy/smoke.sh" || {
        echo "ERROR: post-deploy smoke gate FAILED — release $DEPLOYED_SHA is NOT recorded." >&2
        echo "       The service was restarted, so the old process is already gone." >&2
        echo "       Roll back with: sudo $BIN_COPY --commit <previous-sha>" >&2
        exit 1
    }
fi

echo "==> Deploy OK: commit=$DEPLOYED_SHA (readiness: db+cache ok; smoke: passed)"
printf '%s  %s  %s  %s\n' "$(date -u +%FT%TZ)" "$DEPLOYED_SHA" "$TARGET" \
    "$([[ "${ICHAT_SKIP_SMOKE:-0}" == "1" ]] && echo 'smoke=skipped' || echo 'smoke=passed')" \
    >> "$LOG_FILE"
exit 0
