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

# Directory layout. Every path can be overridden through the environment so
# the SAME script can drive a staging install (deploy/staging/, P4 T49):
# a staging host sets ICHAT_APP_DIR / ICHAT_MEDIA_DIR / ICHAT_SERVICE_NAME
# and lands in its own tree, database, media root and service unit. Defaults
# are the production paths and change nothing for existing usage.
APP_DIR=${ICHAT_APP_DIR:-/opt/ichat}
MEDIA_DIR=${ICHAT_MEDIA_DIR:-/var/lib/ichat/media}
REPO_DIR=$APP_DIR/repo
VENV_DIR=$APP_DIR/.venv
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

# --- resolved install configuration (P4 T49; review findings OPS-01 / OPS-02) --
# ONE resolution feeds the service unit, the media root, the readiness probe and
# the smoke gate. Before this, the runtime identity was fixed from the caller's
# environment *before* .env was read, so an install that declares
# ICHAT_SERVICE_NAME in its .env — the documented staging layout — silently
# restarted the PRODUCTION unit and probed production's ports.
#
# Precedence, highest first:
#   1. the caller's environment — `sudo ICHAT_SERVICE_NAME=... deploy.sh`
#   2. $APP_DIR/.env — the same file systemd loads through EnvironmentFile
#   3. the production default
#
# Two groups of names do NOT follow that precedence, because .env is written by
# whoever owns the tree being deployed and must never be able to authorise its
# own deployment (review findings F-01 / F-02):
#
#   SOVEREIGN (caller-only): who/where the install is, what it may restart and
#   probe, and who is releasing. A .env that sets one of these is REFUSED —
#   silently ignoring it would hide a hostile or misconfigured tree.
#
#   The frozen audit values (actor / approval / via / origin) are computed from
#   the caller's environment BEFORE the file is read and never recomputed, so
#   the value the approval gate validated is the value the log records.
#
# APP_DIR itself is likewise caller-only: it selects which .env is read.
SOVEREIGN_KEYS=(
    ICHAT_APP_DIR
    ICHAT_PROD_APP_DIR
    ICHAT_SERVICE_NAME
    ICHAT_SERVICE_USER
    ICHAT_MEDIA_DIR
    ICHAT_TLS_PORT
    ICHAT_HTTP_PORT
    ICHAT_READY_ORIGIN
    ICHAT_LOOPBACK_ORIGIN
    ICHAT_DEPLOYER
    ICHAT_APPROVAL
    ICHAT_REQUIRE_APPROVAL
    ICHAT_SKIP_SMOKE
)

# Names that may come from .env (they describe the application, not the
# deployment). Kept permissive: unknown keys are harmless because nothing below
# reads them for a decision that .env may influence.
reject_sovereign_in_env() {  # reject_sovereign_in_env <file>
    local file=$1 line key found=()
    [[ -f "$file" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" == *=* ]] || continue
        key=${line%%=*}
        key=${key#"${key%%[![:space:]]*}"}
        key=${key%"${key##*[![:space:]]}"}
        local allowed
        for allowed in "${SOVEREIGN_KEYS[@]}"; do
            [[ "$key" == "$allowed" ]] && found+=("$key")
        done
        # The caller's identity snapshot lives in _SOV_* variables, which this
        # script assigns before `source` and reads after it. `set -a` exports
        # every assignment in .env, so a tree that writes `_SOV_PROD_APP_DIR`
        # (or `_SOV_SKIP_SMOKE`) overwrites the caller's snapshot and re-opens
        # exactly the hole this guard closes (review R2-01/R2-02). The whole
        # prefix is reserved — nothing legitimate in .env starts with it.
        [[ "$key" == _SOV_* ]] && found+=("$key")
    done < "$file"
    if (( ${#found[@]} > 0 )); then
        echo "ERROR: $file sets deployment-identity keys, which this script" >&2
        echo "       accepts ONLY from the caller's environment:" >&2
        for key in "${found[@]}"; do
            echo "         $key" >&2
        done
        echo "       A deployable tree must not be able to choose the unit it" >&2
        echo "       restarts, the ports it probes, or the actor/approval it is" >&2
        echo "       recorded under. Move these to the command line:" >&2
        echo "         sudo $BIN_COPY $(for key in "${found[@]}"; do printf '%s=<value> ' "$key"; done)" >&2
        echo "       Nothing was changed: no fetch, no checkout, no restart." >&2
        exit 2
    fi
}
reject_sovereign_in_env "$APP_DIR/.env"

# Snapshot the caller's identity values under private names before the file is
# read, so `source` cannot shadow them even transiently. The guard above means
# a clean .env never touched them; this is the second line of defence for a
# tree whose .env is malformed in a way the guard did not spell out.
_SOV_SERVICE_NAME=${ICHAT_SERVICE_NAME:-}
_SOV_SERVICE_USER=${ICHAT_SERVICE_USER:-}
_SOV_MEDIA_DIR=${ICHAT_MEDIA_DIR:-}
_SOV_TLS_PORT=${ICHAT_TLS_PORT:-}
_SOV_HTTP_PORT=${ICHAT_HTTP_PORT:-}
_SOV_READY_ORIGIN=${ICHAT_READY_ORIGIN:-}
_SOV_LOOPBACK_ORIGIN=${ICHAT_LOOPBACK_ORIGIN:-}
_SOV_SKIP_SMOKE=${ICHAT_SKIP_SMOKE:-}
_SOV_APPROVAL=${ICHAT_APPROVAL:-}
_SOV_REQUIRE_APPROVAL=${ICHAT_REQUIRE_APPROVAL:-}
_SOV_DEPLOYER=${ICHAT_DEPLOYER:-}
_SOV_PROD_APP_DIR=${ICHAT_PROD_APP_DIR:-}
_SOV_APP_DIR=${APP_DIR:-}

# Frozen audit identity. Computed here — before .env is read — so the approval
# gate and the DEPLOYMENTS.log line can never disagree with each other or with
# the operator who actually ran the command (F-02).
FROZEN_ACTOR=$(deploy_actor)
FROZEN_VIA=$(deploy_via)
FROZEN_ORIGIN=$(deploy_origin)

set -a; source "$APP_DIR/.env"; set +a

# Restore the caller's values: for these names the caller always wins.
APP_DIR=$_SOV_APP_DIR
ICHAT_SERVICE_NAME=$_SOV_SERVICE_NAME
ICHAT_SERVICE_USER=$_SOV_SERVICE_USER
ICHAT_MEDIA_DIR=$_SOV_MEDIA_DIR
ICHAT_TLS_PORT=$_SOV_TLS_PORT
ICHAT_HTTP_PORT=$_SOV_HTTP_PORT
ICHAT_READY_ORIGIN=$_SOV_READY_ORIGIN
ICHAT_LOOPBACK_ORIGIN=$_SOV_LOOPBACK_ORIGIN
ICHAT_SKIP_SMOKE=${_SOV_SKIP_SMOKE:-0}
ICHAT_APPROVAL=$_SOV_APPROVAL
ICHAT_REQUIRE_APPROVAL=$_SOV_REQUIRE_APPROVAL
ICHAT_DEPLOYER=$_SOV_DEPLOYER
PROD_APP_DIR=${_SOV_PROD_APP_DIR:-/opt/ichat}
# APPROVAL / REQUIRE_APPROVAL were computed by the approval gate above, from
# the caller's environment only — .env had not been read yet. They are NOT
# recomputed here: the value the gate validated is the value the log records
# (review finding F-02).

PROD_SERVICE_NAME=ichat
PROD_SERVICE_USER=ichat
PROD_MEDIA_DIR=/var/lib/ichat/media

MEDIA_DIR=${ICHAT_MEDIA_DIR:-$PROD_MEDIA_DIR}
SERVICE_NAME=${ICHAT_SERVICE_NAME:-$PROD_SERVICE_NAME}
SERVICE_USER=${ICHAT_SERVICE_USER:-$PROD_SERVICE_USER}
SERVICE_UNIT=$SERVICE_NAME.service

# Ports are parsed, not trusted: a typo must not silently become port 0.
resolve_port() {  # resolve_port <name> <value> <default>
    local name=$1 value=${2:-} default=$3
    if [[ -z "$value" ]]; then
        printf '%s' "$default"
    elif [[ "$value" =~ ^[0-9]{1,5}$ ]] && (( value >= 1 && value <= 65535 )); then
        printf '%s' "$value"
    else
        echo "ERROR: $name='$value' is not a TCP port (1-65535)." >&2
        exit 2
    fi
}
TLS_PORT=$(resolve_port ICHAT_TLS_PORT "${ICHAT_TLS_PORT:-}" 8443)
HTTP_PORT=$(resolve_port ICHAT_HTTP_PORT "${ICHAT_HTTP_PORT:-}" 8000)

# Probe origins come from the SAME resolved config as the unit above, so a
# staging install (Daphne 8001 / Nginx 8444) cannot fall back to production's
# 8443/8000 through an omission.
TLS_ORIGIN=${ICHAT_READY_ORIGIN:-https://127.0.0.1:$TLS_PORT}
LOOPBACK_ORIGIN=${ICHAT_LOOPBACK_ORIGIN:-http://127.0.0.1:$HTTP_PORT}

# Lexical path normalisation: collapse duplicate separators, drop "." and
# resolve "..". Purely textual (the directories need not exist on this host),
# and deliberately the same algorithm check_isolation.sh uses — a guard that
# compares raw strings is bypassed by /var/lib//ichat/media, .../media/. and
# .../../ichat/media alike (review finding F-04).
normalise_dir() {  # normalise_dir <path>
    local p=${1:-} part
    local -a parts=() out=()
    # Collapse duplicate separators, then split on the remaining ones.
    while [[ "$p" == *"//"* ]]; do p=${p//\/\//\/}; done
    IFS='/' read -r -a parts <<< "$p"
    for part in "${parts[@]}"; do
        case "$part" in
            ''|'.') continue ;;
            '..')
                # Pop the last real component; a leading ".." is dropped
                # because there is nothing above the root to escape to.
                (( ${#out[@]} > 0 )) && unset 'out[${#out[@]}-1]'
                ;;
            *) out+=("$part") ;;
        esac
    done
    printf '/%s' "$(IFS='/'; printf '%s' "${out[*]}")"
}

# The same-or-nested test, shared by every path guard so none of them drifts.
paths_overlap() {  # paths_overlap <a> <b>; 0 when equal or one contains the other
    local a b
    a=$(normalise_dir "$1")
    b=$(normalise_dir "$2")
    [[ "$a" == "$b" || "${a}/" == "${b}/"* || "${b}/" == "${a}/"* ]]
}

# A non-production tree that resolves to a PRODUCTION resource has an
# incomplete staging identity; it is not a working install. Refuse BEFORE the
# restart: "restarted the wrong unit" cannot be undone by the script, and
# "wrote staging media into production" cannot be undone either.
#
# ICHAT_PROD_APP_DIR names the tree that IS production (default /opt/ichat).
# It is caller-only (see SOVEREIGN_KEYS): a tree must not be able to declare
# itself production to skip these guards.
PROD_APP_NORM=$(normalise_dir "$PROD_APP_DIR")
if [[ "$(normalise_dir "$APP_DIR")" != "$PROD_APP_NORM" ]]; then
    if [[ "$SERVICE_NAME" == "$PROD_SERVICE_NAME" ]]; then
        echo "ERROR: install tree $APP_DIR would restart the PRODUCTION unit" >&2
        echo "       '$SERVICE_UNIT' because ICHAT_SERVICE_NAME is unset." >&2
        echo "       Set ICHAT_SERVICE_NAME (and ICHAT_SERVICE_USER) on the" >&2
        echo "       command line, or in $APP_DIR/.env? No: identity keys are" >&2
        echo "       caller-only. Example:" >&2
        echo "         sudo ICHAT_SERVICE_NAME=ichat-staging \\" >&2
        echo "              ICHAT_SERVICE_USER=ichat-staging \\" >&2
        echo "              $BIN_COPY" >&2
        echo "       Nothing was changed: no fetch, no checkout, no restart." >&2
        exit 2
    fi
    if [[ "$SERVICE_USER" == "$PROD_SERVICE_USER" ]]; then
        echo "ERROR: install tree $APP_DIR would create media as the production" >&2
        echo "       user '$PROD_SERVICE_USER' because ICHAT_SERVICE_USER is unset." >&2
        echo "       Identity keys are caller-only; pass it on the command line." >&2
        echo "       Nothing was changed: no fetch, no checkout, no restart." >&2
        exit 2
    fi
    if [[ "$(normalise_dir "$MEDIA_DIR")" == "$PROD_MEDIA_DIR" ]]; then
        echo "ERROR: install tree $APP_DIR would use the PRODUCTION media root" >&2
        echo "       $PROD_MEDIA_DIR because ICHAT_MEDIA_DIR is unset (or is a" >&2
        echo "       spelling of it: //, /., ../ are all normalised first)." >&2
        echo "       Nothing was changed: no fetch, no checkout, no restart." >&2
        exit 2
    fi
    if [[ "$TLS_PORT" == "8443" || "$HTTP_PORT" == "8000" ]]; then
        echo "ERROR: install tree $APP_DIR would probe PRODUCTION's ports" >&2
        echo "       ($TLS_ORIGIN / $LOOPBACK_ORIGIN) because ICHAT_TLS_PORT /" >&2
        echo "       ICHAT_HTTP_PORT are unset. Pass the staging front door and" >&2
        echo "       Daphne port on the command line (8444 / 8001)." >&2
        echo "       Nothing was changed: no fetch, no checkout, no restart." >&2
        exit 2
    fi
    # An explicit origin override is documented and allowed, so the guard has
    # to look at the ORIGINS too: ICHAT_READY_ORIGIN=https://127.0.0.1:8443
    # otherwise probed production while the port variables looked fine
    # (review finding F-03).
    origin_port() {  # origin_port <url> -> trailing :port, or the scheme default
        local url=$1 host
        host=${url#*://}
        host=${host%%/*}
        if [[ "$host" == *:* ]]; then
            printf '%s' "${host##*:}"
        elif [[ "$url" == https://* ]]; then
            printf '443'
        else
            printf '80'
        fi
    }
    for origin in "$TLS_ORIGIN" "$LOOPBACK_ORIGIN"; do
        case "$(origin_port "$origin")" in
            8443|8000)
                echo "ERROR: install tree $APP_DIR would probe PRODUCTION's port" >&2
                echo "       via an explicit origin: $origin" >&2
                echo "       Set ICHAT_READY_ORIGIN / ICHAT_LOOPBACK_ORIGIN to the" >&2
                echo "       staging front door (e.g. https://127.0.0.1:8444)." >&2
                echo "       Nothing was changed: no fetch, no checkout, no restart." >&2
                exit 2
                ;;
        esac
    done
fi
echo "==> Install: tree=$APP_DIR unit=$SERVICE_UNIT user=$SERVICE_USER media=$MEDIA_DIR"
echo "==> Probes: ready=$TLS_ORIGIN fallback=$LOOPBACK_ORIGIN"

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
# SERVICE_USER / SERVICE_NAME / MEDIA_DIR were resolved from .env above; this
# is the only place that creates them, so the ownership matches the unit that
# is about to be restarted.
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$MEDIA_DIR" "$MEDIA_DIR/avatars" "$MEDIA_DIR/uploads"

# --- config validation before touching the running service ---
# (.env is already sourced: the resolved install config above needs it, and a
# second `source` here would let the file overwrite an operator override.)
"$VENV_DIR/bin/python" "$REPO_DIR/manage.py" check
"$VENV_DIR/bin/python" "$REPO_DIR/manage.py" makemigrations --check --dry-run

# --- migrate / collect ---
"$VENV_DIR/bin/python" "$REPO_DIR/manage.py" migrate --noinput
"$VENV_DIR/bin/python" "$REPO_DIR/manage.py" collectstatic --noinput --clear

echo "==> Restarting service"
systemctl restart "$SERVICE_UNIT"
systemctl enable "$SERVICE_UNIT" >/dev/null 2>&1 || true

# --- readiness gate ---
# Primary: probe through the local Nginx TLS front door — works for ANY
# deployed version (old versions redirect plain http to https) and exercises
# the full request path (TLS, proxy, app, DB, Redis).
# Fallback: when nothing listens on the TLS port (fresh host before Nginx is
# set up), probe Daphne over loopback http with the production Host header;
# this is only valid on versions that exempt health paths from the SSL
# redirect.
# Both paths assert the readiness JSON body — never trust a bare connection
# success (a 301/400 body would pass a plain `curl -f`).
# Both origins are resolved from install config (OPS-02): a staging install
# probes ITS OWN 8444/8001, never production's.
ALLOWED_HOST=${DJANGO_ALLOWED_HOSTS%%,*}
BODY=""
for i in $(seq 1 30); do
    BODY=$(curl -fkS --max-time 5 \
        -H "Host: $ALLOWED_HOST" \
        "$TLS_ORIGIN/health/ready/" 2>/dev/null || true)
    if [[ "$BODY" != *'"status": "ok"'* ]]; then
        BODY=$(curl -fsS --max-time 5 \
            -H "Host: $ALLOWED_HOST" \
            "$LOOPBACK_ORIGIN/health/ready/" 2>/dev/null || true)
    fi
    if [[ "$BODY" == *'"status": "ok"'* ]]; then
        echo "==> Readiness OK: db+cache reachable"
        READY=1
        break
    fi
    sleep 1
done
if [[ "${READY:-0}" != "1" ]]; then
    echo "ERROR: readiness probe failed after restart (last body: ${BODY:-none});" >&2
    echo "       probed $TLS_ORIGIN then $LOOPBACK_ORIGIN; check 'journalctl -u $SERVICE_NAME -n 50'" >&2
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
    # The gate script lives in the deployed tree, so a target commit from
    # before #204 (e.g. f2f60ec, the 2026-10-07 production build) has none.
    # Only a ROLLBACK may skip in that case: failing there would make the
    # documented escape hatch unusable. A forward release with no gate script
    # is a broken gate, not a historical rollback (review finding OPS-03) —
    # it must fail closed and record NOTHING.
    if [[ "$IS_ROLLBACK" != "1" ]]; then
        echo "ERROR: $REPO_DIR/deploy/smoke.sh is missing from the target tree." >&2
        echo "       A forward release cannot skip its login/core-API gate:" >&2
        echo "       nothing proves this build can authenticate, so commit" >&2
        echo "       $DEPLOYED_SHA is NOT recorded." >&2
        echo "       Fix the tree, or set ICHAT_SKIP_SMOKE=1 for a known-broken hotfix." >&2
        exit 1
    fi
    # Rolling back past #204: skip loudly and record the exception.
    echo "WARNING: target commit $DEPLOYED_SHA predates deploy/smoke.sh — smoke gate SKIPPED (rollback)." >&2
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
        SMOKE_TLS_ORIGIN="$TLS_ORIGIN" SMOKE_LOOPBACK_ORIGIN="$LOOPBACK_ORIGIN" \
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
# actor/via/from/approval are the FROZEN values computed before .env was read
# (FROZEN_ACTOR/FROZEN_VIA/FROZEN_ORIGIN/APPROVAL): recomputing them here would
# let a deployed tree rewrite who released it and under which approval, and
# would let the recorded approval differ from the one the gate validated
# (review finding F-02).
printf 'ts=%s sha=%s target=%s kind=%s actor=%s via=%s from=%s smoke=%s approval=%s\n' \
    "$(date -u +%FT%TZ)" \
    "$DEPLOYED_SHA" \
    "$TARGET" \
    "$RELEASE_KIND" \
    "$FROZEN_ACTOR" \
    "$FROZEN_VIA" \
    "$FROZEN_ORIGIN" \
    "$SMOKE_RESULT" \
    "$APPROVAL" \
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
echo "==> Recorded: actor=$FROZEN_ACTOR kind=$RELEASE_KIND approval=$APPROVAL"
[[ -n "$PREVIOUS" ]] && echo "    rollback target if needed: $PREVIOUS"
exit 0
