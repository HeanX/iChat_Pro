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
#   sudo /opt/ichat/bin/deploy.sh --status            # show the last releases
#   sudo /opt/ichat/bin/deploy.sh --status --verbose  # ... with every field
#
# Two gates must pass before a deploy counts as successful:
#   1. readiness  — /health/ready/ answers 200 with {"status": "ok"}
#   2. smoke      — deploy/smoke.sh proves login + core APIs still work
#                   (P4 Test T23 / #204). Failing either exits non-zero and
#                   appends NOTHING to DEPLOYMENTS.log.
#
# Release audit trail (P4 T50 / #177): every successful deploy appends ONE
# structured line to /opt/ichat/DEPLOYMENTS.log:
#
#   ts=<utc> sha=<sha> target=<branch[@commit]> kind=<deploy|rollback>
#   actor=<who> via=<sudo|direct> from=<host> smoke=<result> approval=<ref>
#
# `actor` answers "who released this". It comes from SUDO_USER when run under
# sudo (the real operator, not root), else $USER/$LOGNAME, or ICHAT_DEPLOYER
# when a CI runner is the caller. `approval` records the approval reference
# (ticket/PR) supplied with ICHAT_APPROVAL; it is *recorded*, only required
# when the deployment is not itself an approved target (see below).
# The deployed SHA in this file remains the authoritative answer to "what is
# running", independent of what main says.
set -euo pipefail

APP_DIR=/opt/ichat
REPO_DIR=$APP_DIR/repo
VENV_DIR=$APP_DIR/.venv
MEDIA_DIR=/var/lib/ichat/media
LOG_FILE=$APP_DIR/DEPLOYMENTS.log
BIN_COPY=$APP_DIR/bin/deploy.sh
# Where the previous release is remembered so `--status` can name a rollback
# target without the operator having to dig through history.
STATE_DIR=$APP_DIR/.deploy-state

# Re-exec from the stable copy (except when we ARE the stable copy).
if [[ "$0" != "$BIN_COPY" ]]; then
    mkdir -p "$(dirname "$BIN_COPY")"
    install -m 755 "$0" "$BIN_COPY"
    exec bash "$BIN_COPY" "$@"
fi

BRANCH=main
COMMIT=
SHOW_STATUS=0
VERBOSE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --branch) BRANCH="${2:?--branch needs a value}"; shift 2 ;;
        --commit) COMMIT="${2:?--commit needs a value}"; shift 2 ;;
        --status) SHOW_STATUS=1; shift ;;
        --verbose|-v) VERBOSE=1; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

# --- release audit helpers ----------------------------------------------
# Who is releasing. Under sudo the operator is SUDO_USER; root itself is not
# an answer to "who deployed this". ICHAT_DEPLOYER takes precedence for CI.
deploy_actor() {
    local actor=${ICHAT_DEPLOYER:-}
    if [[ -z "$actor" ]]; then
        if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
            actor=$SUDO_USER
        else
            actor=${USER:-${LOGNAME:-unknown}}
        fi
    fi
    printf '%s' "$actor" | tr -cs 'A-Za-z0-9._@-' '_' | cut -c1-64
}

deploy_via() {
    [[ -n "${SUDO_USER:-}" ]] && printf 'sudo' || printf 'direct'
}

# Where the request came from. An SSH session carries SSH_CONNECTION, whose
# first field is the client address; otherwise record the local console.
deploy_origin() {
    if [[ -n "${SSH_CONNECTION:-}" ]]; then
        printf '%s' "${SSH_CONNECTION%% *}"
    else
        printf 'local-console'
    fi
}

# Approval reference. Recorded on every line; only *enforced* when the caller
# is not deploying an already-approved target (see require_approval below).
deploy_approval() {
    printf '%s' "${ICHAT_APPROVAL:-none}" | tr -cs 'A-Za-z0-9._@#/-' '_' | cut -c1-96
}

# --- --status -----------------------------------------------------------
if [[ "$SHOW_STATUS" == "1" ]]; then
    if [[ ! -s "$LOG_FILE" ]]; then
        echo "No deployments recorded in $LOG_FILE"
        exit 0
    fi
    echo "Recent releases in $LOG_FILE (newest last):"
    tail -n "${ICHAT_STATUS_LINES:-5}" "$LOG_FILE"
    current=$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null || echo unknown)
    echo
    echo "Running now: $current"
    if [[ "$VERBOSE" != "1" ]]; then
        echo "(use --verbose for the full field list)"
    fi
    exit 0
fi

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

# --- approval gate (P4 T50 / #177) ---
# A production release must name the approval it belongs to, so DEPLOYMENTS.log
# can tie a running SHA back to a decision. Enforced BEFORE anything touching
# the running service, so a refused release changes nothing.
#   ICHAT_APPROVAL=<ticket/PR/change-id>
#   ICHAT_REQUIRE_APPROVAL=1  (default in production; 0 only for a known-good
#                              emergency, which is itself recorded)
# `--commit` (a rollback to a previously approved SHA) is always allowed.
APPROVAL=$(deploy_approval)
REQUIRE_APPROVAL=${ICHAT_REQUIRE_APPROVAL:-1}
if [[ "$APPROVAL" == "none" && "$REQUIRE_APPROVAL" == "1" && -z "$COMMIT" ]]; then
    echo "ERROR: this release has no approval reference." >&2
    echo "       Set ICHAT_APPROVAL to the ticket/PR that approved it, e.g." >&2
    echo "         sudo ICHAT_APPROVAL='#177 PR-42' $BIN_COPY" >&2
    echo "       Rollbacks (--commit) do not require one. Nothing was changed." >&2
    exit 1
fi
if [[ "$APPROVAL" == "none" ]]; then
    echo "WARNING: ICHAT_APPROVAL is unset (kind=$([[ -n "$COMMIT" ]] && echo rollback || echo deploy)); recorded as approval=none." >&2
fi

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
elif [[ ! -f "$REPO_DIR/deploy/smoke.sh" ]]; then
    # Rolling back to a commit from before the gate existed (e.g. f2f60ec,
    # the 2026-10-07 production build) means the target tree has no smoke
    # script. Failing here would make the documented rollback path itself
    # unusable, so skip loudly and record the exception instead.
    echo "WARNING: target commit $DEPLOYED_SHA predates deploy/smoke.sh — smoke gate SKIPPED." >&2
    SMOKE_ABSENT=1
else
    if [[ -z "${SMOKE_USERNAME:-}" || -z "${SMOKE_PASSWORD:-}" ]]; then
        echo "ERROR: SMOKE_USERNAME/SMOKE_PASSWORD are not set in $APP_DIR/.env." >&2
        echo "       The post-deploy smoke gate cannot authenticate, so the release" >&2
        echo "       is not recorded. Create the dedicated smoke account (runbook §7)" >&2
        echo "       or set ICHAT_SKIP_SMOKE=1 for a known-broken hotfix." >&2
        exit 1
    fi
    echo "==> Post-deploy smoke gate"
    AUTO_BASE_URL=1 SMOKE_INSECURE_TLS=1 SMOKE_PYTHON="$VENV_DIR/bin/python" \
        bash "$REPO_DIR/deploy/smoke.sh" || {
        echo "ERROR: post-deploy smoke gate FAILED — release $DEPLOYED_SHA is NOT recorded." >&2
        echo "       The service was restarted, so the old process is already gone." >&2
        echo "       Roll back with: sudo $BIN_COPY --commit <previous-sha>" >&2
        exit 1
    }
fi

SMOKE_RESULT=passed
if [[ "${ICHAT_SKIP_SMOKE:-0}" == "1" ]]; then
    SMOKE_RESULT=skipped
elif [[ "${SMOKE_ABSENT:-0}" == "1" ]]; then
    SMOKE_RESULT=skipped-no-script
fi

if [[ -n "$COMMIT" ]]; then
    RELEASE_KIND=rollback
else
    RELEASE_KIND=deploy
fi

echo "==> Deploy OK: commit=$DEPLOYED_SHA (readiness: db+cache ok; smoke: $SMOKE_RESULT)"
# One structured line per release: who, what, when, and how it was gated.
# Field values are sanitised by the helpers so the log stays greppable.
printf 'ts=%s sha=%s target=%s kind=%s actor=%s via=%s from=%s smoke=%s approval=%s\n' \
    "$(date -u +%FT%TZ)" \
    "$DEPLOYED_SHA" \
    "$TARGET" \
    "$RELEASE_KIND" \
    "$(deploy_actor)" \
    "$(deploy_via)" \
    "$(deploy_origin)" \
    "$SMOKE_RESULT" \
    "$(deploy_approval)" \
    >> "$LOG_FILE"

# Remember the SHA that is now running and the one it replaced, so a rollback
# target is always available even after the log is rotated.
mkdir -p "$STATE_DIR"
PREVIOUS=$(git -C "$REPO_DIR" rev-parse 'HEAD@{1}' 2>/dev/null || true)
[[ -n "$PREVIOUS" && "$PREVIOUS" == "$DEPLOYED_SHA" ]] && PREVIOUS=
printf '%s\n' "$DEPLOYED_SHA" > "$STATE_DIR/current"
[[ -n "$PREVIOUS" ]] && printf '%s\n' "$PREVIOUS" > "$STATE_DIR/previous"
echo "==> Recorded: actor=$(deploy_actor) kind=$RELEASE_KIND approval=$(deploy_approval)"
exit 0
