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
# Every value that lands in DEPLOYMENTS.log goes through a whitelist first.
# The log is one key=value record per line and it is read by humans and by
# --status, so an embedded newline in an environment variable must never be
# able to forge a second record (e.g. SSH_CONNECTION or --branch).
sanitise() {  # sanitise <value> [maxlen]
    local value=$1 max=${2:-96}
    # tr -cs collapses every run of non-whitelisted bytes into one '_'; a
    # newline therefore cannot survive. Leading/trailing separators are
    # trimmed so a whitespace-only input becomes empty, not '_'.
    printf '%s' "$value" \
        | tr -cs 'A-Za-z0-9._@#/-' '_' \
        | sed -e 's/^_*//' -e 's/_*$//' \
        | cut -c1-"$max"
}

# Who is releasing. Under sudo the operator is SUDO_USER; root itself is not
# an answer to "who deployed this". ICHAT_DEPLOYER takes precedence for CI.
deploy_actor() {
    local actor=${ICHAT_DEPLOYER:-}
    if [[ -z "$actor" ]]; then
        if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
            actor=$SUDO_USER
        else
            actor=${USER:-${LOGNAME:-}}
        fi
    fi
    actor=$(sanitise "$actor" 64)
    printf '%s' "${actor:-unknown}"
}

deploy_via() {
    [[ -n "${SUDO_USER:-}" ]] && printf 'sudo' || printf 'direct'
}

# Where the request came from. An SSH session carries SSH_CONNECTION, whose
# first field is the client address; otherwise record the local console.
deploy_origin() {
    local origin=local-console
    if [[ -n "${SSH_CONNECTION:-}" ]]; then
        origin=$(sanitise "${SSH_CONNECTION%% *}" 64)
        [[ -n "$origin" ]] || origin=unknown-ssh
    fi
    printf '%s' "$origin"
}

# Approval reference. Recorded on every line; only *enforced* when the caller
# is not deploying an already-approved target (see the approval gate below).
# A whitespace/punctuation-only value sanitises to empty — or to bare
# separators like "///" — and either way is NOT an approval: a real reference
# must contain at least one alphanumeric character.
deploy_approval() {
    local approval
    approval=$(sanitise "${ICHAT_APPROVAL:-}" 96)
    if [[ ! "$approval" =~ [A-Za-z0-9] ]]; then
        approval=""
    fi
    printf '%s' "${approval:-none}"
}

# ICHAT_REQUIRE_APPROVAL is parsed against an explicit truthy set: a typo such
# as "true"/"yes"/"True" must fail loudly instead of silently disabling the
# gate, and an unknown value must not be read as "off". The result is set in
# REQUIRE_APPROVAL by reference (not echoed from a subshell), because `exit`
# inside $(...) would only leave the subshell and let the deploy continue.
parse_require_approval() {
    local raw=${ICHAT_REQUIRE_APPROVAL:-1}
    case "${raw,,}" in
        1|true|yes|on) REQUIRE_APPROVAL=1 ;;
        0|false|no|off) REQUIRE_APPROVAL=0 ;;
        *)
            echo "ERROR: ICHAT_REQUIRE_APPROVAL=$raw is not a recognised value." >&2
            echo "       Use 1/true/yes/on or 0/false/no/off." >&2
            exit 2
            ;;
    esac
}

# --- --status -----------------------------------------------------------
if [[ "$SHOW_STATUS" == "1" ]]; then
    if [[ ! -s "$LOG_FILE" ]]; then
        echo "No deployments recorded in $LOG_FILE"
        exit 0
    fi
    # The last successfully recorded release is the authoritative answer to
    # "what is running": DEPLOYMENTS.log is only appended after both gates
    # pass, while `git rev-parse HEAD` in the repo can lag behind (a refused
    # release never reaches the log, and a manual checkout changes the tree
    # without deploying).
    echo "Recent releases in $LOG_FILE (newest last):"
    if [[ "$VERBOSE" == "1" ]]; then
        tail -n "${ICHAT_STATUS_LINES:-5}" "$LOG_FILE"
    else
        tail -n "${ICHAT_STATUS_LINES:-5}" "$LOG_FILE" \
            | sed -e 's/ via=[^ ]*//' -e 's/ from=[^ ]*//'
    fi
    last_sha=$(awk -F'sha=' 'NF>1 {split($2, a, " "); sha=a[1]} END {print sha}' "$LOG_FILE")
    echo
    echo "Running now (last recorded release): ${last_sha:-unknown}"
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
# --branch/--commit come from the command line; keep the logged target free of
# whitespace so it cannot break the one-record-per-line invariant.
TARGET=$(sanitise "$TARGET" 96)
[[ -n "$TARGET" ]] || { echo "ERROR: empty deploy target after sanitising." >&2; exit 2; }

# --- approval gate (P4 T50 / #177) ---
# A production release must name the approval it belongs to, so DEPLOYMENTS.log
# can tie a running SHA back to a decision.
#   ICHAT_APPROVAL=<ticket/PR/change-id>
#   ICHAT_REQUIRE_APPROVAL=1|0  (1 in production; 0 only for a known-good
#                                emergency, which is itself recorded)
# A ROLLBACK to an exact commit needs no approval: the escape hatch must not
# depend on paperwork. That exemption applies only to a full 40-hex commit id
# — a branch/ref/tag like `--commit main` is a forward release hiding in
# rollback clothing, so it is treated as a normal deploy and needs approval.
#
# THIS RUNS BEFORE ANY GIT COMMAND. /opt/ichat/repo is the tree systemd runs,
# so fetching/checking out first would leave the production tree on an
# unapproved commit even when the release is then refused — and would make
# `git rev-parse HEAD` the evidence of what runs, pointing at a commit that
# was never deployed. A refused release must not touch the tree.
APPROVAL=$(deploy_approval)
REQUIRE_APPROVAL=""
parse_require_approval
IS_ROLLBACK=0
if [[ -n "$COMMIT" ]]; then
    if [[ "$COMMIT" =~ ^[0-9a-fA-F]{40}$ ]]; then
        IS_ROLLBACK=1
    else
        echo "ERROR: --commit must be a full 40-character commit SHA." >&2
        echo "       Got '$COMMIT', which names a branch/ref rather than a" >&2
        echo "       rollback target. A branch move is a forward release:" >&2
        echo "         sudo ICHAT_APPROVAL='#177' $BIN_COPY --branch <name>" >&2
        echo "       Nothing was changed: no fetch, no checkout, no restart." >&2
        exit 1
    fi
fi
if [[ "$APPROVAL" == "none" && "$REQUIRE_APPROVAL" == "1" && "$IS_ROLLBACK" != "1" ]]; then
    echo "ERROR: this release has no approval reference." >&2
    echo "       Set ICHAT_APPROVAL to the ticket/PR that approved it, e.g." >&2
    echo "         sudo ICHAT_APPROVAL='#177' $BIN_COPY" >&2
    echo "       Rollbacks (--commit <40-hex sha>) do not require one." >&2
    echo "       Nothing was changed: no fetch, no checkout, no restart." >&2
    exit 1
fi
if [[ "$APPROVAL" == "none" ]]; then
    echo "WARNING: no approval reference (kind=$([[ "$IS_ROLLBACK" == "1" ]] && echo rollback || echo deploy)); recorded as approval=none." >&2
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

if [[ "$IS_ROLLBACK" == "1" ]]; then
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

# Remember the SHA of the last SUCCESSFUL release and the one it replaced, so
# a rollback target is always available even after the log is rotated. This is
# bookkeeping only: the log line above is already written and the release is
# live, so a failure here must not turn a successful deploy into a failed one.
# The previous SHA is whatever the state file said before this run — NOT git
# reflog, which reflects fetches/checkouts rather than deployments.
PREVIOUS=""
if state_ok=1; then
    mkdir -p "$STATE_DIR" 2>/dev/null || state_ok=0
fi
if [[ "$state_ok" == "1" && -s "$STATE_DIR/current" ]]; then
    PREVIOUS=$(cat "$STATE_DIR/current" 2>/dev/null || true)
    [[ "$PREVIOUS" == "$DEPLOYED_SHA" ]] && PREVIOUS=
fi
if [[ "$state_ok" == "1" ]]; then
    printf '%s\n' "$DEPLOYED_SHA" > "$STATE_DIR/current" 2>/dev/null || state_ok=0
fi
if [[ "$state_ok" == "1" && -n "$PREVIOUS" ]]; then
    printf '%s\n' "$PREVIOUS" > "$STATE_DIR/previous" 2>/dev/null || state_ok=0
fi
if [[ "$state_ok" != "1" ]]; then
    echo "WARNING: could not update $STATE_DIR; the release IS recorded in" >&2
    echo "         $LOG_FILE and is running. Rollback target: see the log." >&2
fi
echo "==> Recorded: actor=$(deploy_actor) kind=$RELEASE_KIND approval=$(deploy_approval)"
[[ -n "$PREVIOUS" ]] && echo "    rollback target if needed: $PREVIOUS"
exit 0
