#!/usr/bin/env bash
# Isolation check for the staging environment (P4 T49 / Issue #176).
#
# Acceptance criterion: "与生产配置、数据和密钥隔离" — isolated from
# production config, data and secrets. Every assertion here compares the two
# environments and FAILS if they share anything: the check is the evidence,
# re-runnable after every staging change.
#
# Usage (on the host that has both environments):
#   sudo deploy/staging/check_isolation.sh \
#       --prod-env /opt/ichat/.env --staging-env /opt/ichat-staging/.env
#
# Exits 0 when fully isolated, 1 with a list of violations.
#
# Only the two .env files and (optionally) live PostgreSQL/Redis are read.
# Secrets are never printed — a leaked value is reported as its SHA-256.
set -euo pipefail

PROD_ENV=/opt/ichat/.env
STAGING_ENV=/opt/ichat-staging/.env
CHECK_DATA=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prod-env) PROD_ENV=$2; shift 2 ;;
        --staging-env) STAGING_ENV=$2; shift 2 ;;
        --check-data) CHECK_DATA=true; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

VIOLATIONS=()
violation() { VIOLATIONS+=("$1"); printf '  [FAIL] %s\n' "$1" >&2; }
ok()       { printf '  [ OK ] %s\n' "$1"; }

# A check that could not run is itself a violation: silent skipping would
# make "unchecked" look like "checked and fine".
env_value() {  # env_value <file> <key>; empty when absent
    local file=$1 key=$2 line
    line=$(grep -E "^${key}=" "$file" 2>/dev/null | head -1 || true)
    printf '%s' "${line#${key}=}"
}

# Normalise a declared path for comparison: strip trailing slashes, drop any
# `.`/`..` components lexically (the dirs need not exist on this host), and
# collapse duplicate separators. Without this, `/opt/ichat/` or a `../`
# suffix would dodge the equality and nesting assertions.
normalise_path() {
    local p=$1 part
    local -a out_parts=()
    p=${p//\/\///}
    p=${p%/}
    IFS='/' read -r -a parts <<< "$p"
    for part in "${parts[@]}"; do
        [[ -z "$part" || "$part" == "." ]] && continue
        if [[ "$part" == ".." ]]; then
            local count=${#out_parts[@]}
            (( count > 0 )) && unset 'out_parts[count-1]'
        else
            out_parts+=("$part")
        fi
    done
    local joined="${out_parts[*]}"
    printf '/%s' "${joined// /\/}"
}

# A secret is compared by digest so this script never echoes one.
sha() { printf '%s' "$1" | sha256sum | cut -c1-16; }

same_or_diff() {  # same_or_diff <label> <prod-value> <staging-value>
    local label=$1 prod=$2 staging=$3
    if [[ -z "$prod" || -z "$staging" ]]; then
        violation "$label: missing on one side (prod set: $([[ -n "$prod" ]] && echo yes || echo NO), staging set: $([[ -n "$staging" ]] && echo yes || echo NO))"
    elif [[ "$prod" == "$staging" ]]; then
        violation "$label: IDENTICAL to production (value hash $(sha "$prod")…)"
    else
        ok "$label differs from production"
    fi
}

echo "==> Isolation check (P4 T49): staging=$STAGING_ENV vs production=$PROD_ENV"

for f in "$PROD_ENV" "$STAGING_ENV"; do
    [[ -r "$f" ]] || { echo "ERROR: cannot read $f" >&2; exit 2; }
done

# --- 1. secrets ------------------------------------------------------------
echo "-- secrets"
same_or_diff "DJANGO_SECRET_KEY" \
    "$(env_value "$PROD_ENV" DJANGO_SECRET_KEY || true)" \
    "$(env_value "$STAGING_ENV" DJANGO_SECRET_KEY || true)"

prod_db=$(env_value "$PROD_ENV" DATABASE_URL || true)
staging_db=$(env_value "$STAGING_ENV" DATABASE_URL || true)
same_or_diff "DATABASE_URL" "$prod_db" "$staging_db"

# A URL is not one secret. Comparing the whole string and calling it "secret
# isolation" let staging keep production's ROLE and PASSWORD as long as the
# database name was different (review finding OPS-09) — the write risk is the
# credential, not the path. Each component is therefore compared on its own
# and reported by digest, never by value.
url_user() {  # url_user <url> -> percent-decoded userinfo username
    local url=$1 userinfo
    userinfo=$(printf '%s' "$url" | sed -E 's|^[a-z0-9+.-]+://||')
    userinfo=${userinfo%%/*}
    [[ "$userinfo" == *"@"* ]] || { printf ''; return 0; }
    printf '%s' "${userinfo%@*}" | cut -d: -f1
}
url_password() {  # url_password <url> -> percent-decoded userinfo password
    local url=$1 userinfo credentials
    userinfo=$(printf '%s' "$url" | sed -E 's|^[a-z0-9+.-]+://||')
    userinfo=${userinfo%%/*}
    [[ "$userinfo" == *"@"* ]] || { printf ''; return 0; }
    credentials=${userinfo%@*}
    [[ "$credentials" == *":"* ]] || { printf ''; return 0; }
    printf '%s' "${credentials#*:}"
}

if [[ -n "$prod_db" && -n "$staging_db" ]]; then
    prod_role=$(url_user "$prod_db")
    staging_role=$(url_user "$staging_db")
    prod_pw=$(url_password "$prod_db")
    staging_pw=$(url_password "$staging_db")

    if [[ -z "$staging_role" ]]; then
        violation "DATABASE_URL: staging URL carries no role (userinfo missing)"
    elif [[ "$staging_role" == "$prod_role" ]]; then
        violation "DATABASE_URL: staging shares the production database ROLE '${staging_role}' (staging must have its own login, runbook §3c)"
    else
        ok "database role differs (hash $(sha "$staging_role")…)"
    fi

    # An absent password on either side cannot be compared, and "no password"
    # is not isolation: say so instead of silently passing the dimension.
    if [[ -z "$prod_pw" || -z "$staging_pw" ]]; then
        violation "DATABASE_URL: cannot compare database passwords (prod set: $([[ -n "$prod_pw" ]] && echo yes || echo NO), staging set: $([[ -n "$staging_pw" ]] && echo yes || echo NO))"
    elif [[ "$staging_pw" == "$prod_pw" ]]; then
        violation "DATABASE_URL: staging shares the production database PASSWORD (value hash $(sha "$staging_pw")…); rotate the staging role's password"
    else
        ok "database password differs (hash $(sha "$staging_pw")…)"
    fi
fi

# --- 2. config: database name, redis logical db, origins ------------------
echo "-- config"
if [[ -z "$staging_db" ]]; then
    : # already reported as missing above
elif [[ "$staging_db" == "$prod_db" ]]; then
    : # already reported as identical above
else
    # Parse the database name out of the URL path (never print the URL: the
    # userinfo segment may carry the password).
    staging_dbname=$(printf '%s' "$staging_db" | sed -E 's|^[a-z]+://[^/]+/||')
    if [[ "$staging_dbname" != "ichat_staging" || "$staging_dbname" == *"*"* ]]; then
        violation "DATABASE_URL: staging database is not named ichat_staging (db name hash $(sha "$staging_dbname")…)"
    else
        ok "staging database name is distinct (ichat_staging)"
    fi
fi

prod_redis=$(env_value "$PROD_ENV" REDIS_URL || true)
staging_redis=$(env_value "$STAGING_ENV" REDIS_URL || true)
if [[ -n "$prod_redis" && -n "$staging_redis" ]]; then
    prod_dbno=${prod_redis##*/}; staging_dbno=${staging_redis##*/}
    if [[ "$prod_dbno" == "$staging_dbno" ]]; then
        violation "REDIS_URL: both use logical db $prod_dbno (staging should use a different db index)"
    else
        ok "Redis logical db differs (prod=$prod_dbno staging=$staging_dbno)"
    fi
else
    violation "REDIS_URL missing on one side"
fi

staging_origin=$(env_value "$STAGING_ENV" DJANGO_CSRF_TRUSTED_ORIGINS || true)
prod_origin=$(env_value "$PROD_ENV" DJANGO_CSRF_TRUSTED_ORIGINS || true)
same_or_diff "DJANGO_CSRF_TRUSTED_ORIGINS" "$prod_origin" "$staging_origin"

# --- 3. storage paths ------------------------------------------------------
echo "-- storage"
for key in DJANGO_MEDIA_ROOT DJANGO_STATIC_ROOT; do
    same_or_diff "$key" "$(env_value "$PROD_ENV" "$key")" "$(env_value "$STAGING_ENV" "$key")"
done

# Both roots need the nesting test, not just media. `collectstatic --clear`
# empties the whole static root, so a staging root nested inside production's
# (or the reverse) makes each release delete the other environment's files —
# review finding OPS-10, where only MEDIA_ROOT was checked. Comparison is on
# normalised paths, so trailing slashes, duplicate separators and `..` cannot
# dodge it.
check_no_nesting() {  # check_no_nesting <label> <key> <prod-default> [display]
    local label=$1 key=$2 prod_default=${3:-} display=${4:-$1}
    local staging_path prod_path staging_norm prod_norm
    staging_path=$(env_value "$STAGING_ENV" "$key" || true)
    prod_path=$(env_value "$PROD_ENV" "$key" || true)
    prod_path=${prod_path:-$prod_default}
    if [[ -z "$staging_path" ]]; then
        violation "$label: staging $key is unset, so the roots cannot be compared"
        return 0
    fi
    if [[ -z "$prod_path" ]]; then
        violation "$label: production $key is unset, so the roots cannot be compared"
        return 0
    fi
    staging_norm=$(normalise_path "$staging_path")
    prod_norm=$(normalise_path "$prod_path")
    # Trailing "/" makes the prefix test component-aware: /a/bc is not inside
    # /a/b. Equality is the degenerate case of the same test.
    if [[ "${staging_norm}/" == "${prod_norm}/"* || "${prod_norm}/" == "${staging_norm}/"* ]]; then
        violation "$label nesting: $staging_path overlaps $prod_path (one must not be an ancestor of the other)"
    else
        ok "${display} roots are disjoint directories"
    fi
}
check_no_nesting "STATIC_ROOT" DJANGO_STATIC_ROOT /opt/ichat/staticfiles "static"
check_no_nesting "MEDIA_ROOT" DJANGO_MEDIA_ROOT /var/lib/ichat/media "media"

# --- 4. runtime separation (ports, service, user) -------------------------
echo "-- runtime"
staging_app=$(env_value "$STAGING_ENV" ICHAT_APP_DIR || true)
[[ "$(normalise_path "${staging_app:-/opt/ichat-staging}")" == "/opt/ichat" ]] \
    && violation "ICHAT_APP_DIR: staging uses the production tree" \
    || ok "staging tree: ${staging_app:-<unset>}"
staging_service=$(env_value "$STAGING_ENV" ICHAT_SERVICE_NAME || true)
[[ "$staging_service" == "ichat" ]] && violation "ICHAT_SERVICE_NAME: staging reuses the production unit" \
    || ok "staging service unit: ${staging_service:-<unset>}"
staging_user=$(env_value "$STAGING_ENV" ICHAT_SERVICE_USER || true)
[[ "$staging_user" == "ichat" ]] && violation "ICHAT_SERVICE_USER: staging runs as the production user" \
    || ok "staging service user: ${staging_user:-<unset>}"

# daphne loopback port must differ from production's 8000. The unit lives in
# the repo checkout of the staging tree; also accept a repo-root relative
# layout so the check works from any checkout shape.
staging_tree=$(env_value "$STAGING_ENV" ICHAT_APP_DIR || true)
staging_tree=${staging_tree:-/opt/ichat-staging}
port_checked=0
for candidate in \
    "$staging_tree/repo/deploy/staging/ichat-staging.service" \
    "$(dirname "$STAGING_ENV")/../deploy/staging/ichat-staging.service"; do
    if [[ -f "$candidate" ]]; then
        staging_port=$(grep -oE '\-p [0-9]+' "$candidate" | awk '{print $2}')
        if [[ "$staging_port" == "8000" ]]; then
            violation "staging daphne port 8000 collides with production"
        elif [[ -n "$staging_port" ]]; then
            ok "staging daphne port: $staging_port (production: 8000)"
        fi
        port_checked=1
        break
    fi
done
if (( port_checked == 0 )); then
    violation "staging systemd unit not found in $staging_tree/repo/deploy/staging/; daphne port UNVERIFIED"
fi

# --- 5. live data separation (--check-data) -------------------------------
# Before this, the branch only compared the tail of two URLs it had already
# compared as text and never opened a connection, yet printed "live database
# names differ" and contributed to an ISOLATED verdict (review finding
# OPS-08). "Live data" now means a live query:
#   * the role each environment actually authenticates as is read from the
#     server (SELECT current_user), not from the URL;
#   * the Redis logical database index comes from CONFIG GET / SELECT, not
#     from the URL tail;
#   * a missing client, an unreachable server or a rejected login is
#     UNVERIFIED — a violation with a non-zero exit, never a quiet pass.
# The password still travels only through PGPASSWORD, and no URL or secret is
# printed.
#
# ICHAT_PSQL names the client. Production leaves it unset (PATH lookup); the
# regression suite points it at a stub so it can simulate "client not
# installed" without depending on what the host happens to ship. Prepending a
# stub directory to PATH was not enough: on Linux a real /usr/bin/psql stayed
# reachable behind it (CI run 40343782825).
PSQL_BIN=${ICHAT_PSQL:-psql}
REDIS_CLI_BIN=${ICHAT_REDIS_CLI:-redis-cli}

live_psql_role() {  # live_psql_role <db-url>; echoes the role, or nothing
    local url=$1 user pass host port dbname out
    user=$(url_user "$url")
    pass=$(url_password "$url")
    # host[:port]/dbname, without the userinfo section.
    local rest=${url#*://}
    rest=${rest#*@}
    host=${rest%%/*}
    dbname=${rest#*/}
    dbname=${dbname%%\?*}
    port=5432
    if [[ "$host" == *:* ]]; then
        port=${host##*:}
        host=${host%:*}
    fi
    out=$(PGPASSWORD="$pass" PGUSER="$user" PGHOST="$host" PGPORT="$port" \
        PGDATABASE="$dbname" "$PSQL_BIN" -X -qAt -c 'SELECT current_user;' 2>/dev/null) \
        || return 1
    printf '%s' "$out"
}

live_redis_db() {  # live_redis_db <redis-url>; echoes the db index in use
    local url=$1 host port path db from_url out reply
    local rest=${url#*://}
    rest=${rest#*@}
    host=${rest%%/*}
    port=6379
    if [[ "$host" == *:* ]]; then
        port=${host##*:}
        host=${host%:*}
    fi
    # The URL names the logical database after the last '/'. It must be passed
    # to redis-cli: without -n, every check connects to db 0, so two correctly
    # separated servers both report 0 and a GOOD staging install is judged a
    # violation (review finding F-07, second half).
    path=${rest#*/}
    path=${path%%\?*}
    from_url=${path##*/}
    if [[ ! "$from_url" =~ ^[0-9]+$ ]]; then
        # No usable index in the URL: cannot tell which db this URL means, so
        # report "unverified" rather than guessing 0 and producing a wrong
        # verdict.
        return 1
    fi
    # Ask the server which db THIS connection is on. Real `CLIENT INFO` is a
    # single line beginning with id=, with db= in the middle:
    #   id=3 addr=127.0.0.1:... fd=8 name= age=0 ... db=0 sub=0 psub=0 ...
    # The previous `sed -n 's/^db=...'` only matched the test stub's shape and
    # failed against a real server (review finding F-07).
    reply=$("$REDIS_CLI_BIN" -h "${host:-127.0.0.1}" -p "$port" -n "$from_url" \
        --no-auth-warning client info 2>/dev/null) || return 1
    out=$(printf '%s\n' "$reply" | grep -o 'db=[0-9][0-9]*' | head -1)
    out=${out#db=}
    if [[ -z "$out" ]]; then
        # A server that answered without a db= field (or an ACL-restricted
        # CLIENT INFO) leaves the value unknown: fail closed.
        return 1
    fi
    if [[ "$out" != "$from_url" ]]; then
        # The server says we are not on the db the URL asked for. That is a
        # real finding, but it is about the connection, not about isolation,
        # so surface it as its own message from the caller.
        printf 'mismatch:%s:%s' "$from_url" "$out"
        return 0
    fi
    printf '%s' "$out"
}

if [[ "$CHECK_DATA" == "true" ]]; then
    echo "-- live data"
    if ! command -v "$PSQL_BIN" >/dev/null 2>&1; then
        violation "live database: $PSQL_BIN not installed, so the live role is UNVERIFIED"
    elif [[ -z "$staging_db" || -z "$prod_db" ]]; then
        violation "live database: DATABASE_URL missing on one side, so the live role is UNVERIFIED"
    else
        staging_live_role=$(live_psql_role "$staging_db") \
            || staging_live_role=""
        prod_live_role=$(live_psql_role "$prod_db") || prod_live_role=""
        if [[ -z "$staging_live_role" ]]; then
            violation "live database: could not authenticate to the staging database, so its role is UNVERIFIED"
        elif [[ -z "$prod_live_role" ]]; then
            violation "live database: could not authenticate to the production database, so the comparison is UNVERIFIED"
        elif [[ "$staging_live_role" == "$prod_live_role" ]]; then
            violation "live database role: staging authenticates as '${staging_live_role}', IDENTICAL to production"
        else
            ok "live database role differs (staging hash $(sha "$staging_live_role")…)"
        fi
    fi

    if ! command -v "$REDIS_CLI_BIN" >/dev/null 2>&1; then
        violation "live redis: $REDIS_CLI_BIN not installed, so the live logical db is UNVERIFIED"
    elif [[ -z "$staging_redis" || -z "$prod_redis" ]]; then
        violation "live redis: REDIS_URL missing on one side, so the logical db is UNVERIFIED"
    else
        staging_live_db=$(live_redis_db "$staging_redis") || staging_live_db=""
        prod_live_db=$(live_redis_db "$prod_redis") || prod_live_db=""
        # "mismatch:<wanted>:<actual>" means the server answered but is not on
        # the db the URL names. Neither side can then be compared, so it is
        # UNVERIFIED (fail-closed), not a violation of isolation.
        if [[ "$staging_live_db" == mismatch:* || "$prod_live_db" == mismatch:* ]]; then
            violation "live redis: the server is not on the logical db the URL names (${staging_live_db#mismatch:} / ${prod_live_db#mismatch:}); the comparison is UNVERIFIED"
        elif [[ -z "$staging_live_db" ]]; then
            violation "live redis: could not query the staging server, so its logical db is UNVERIFIED"
        elif [[ -z "$prod_live_db" ]]; then
            violation "live redis: could not query the production server, so the comparison is UNVERIFIED"
        elif [[ "$staging_live_db" == "$prod_live_db" ]]; then
            violation "live redis: both servers are on logical db ${staging_live_db}, IDENTICAL to production"
        else
            ok "live redis logical db differs (prod=$prod_live_db staging=$staging_live_db)"
        fi
    fi
fi

# --- verdict ---------------------------------------------------------------
if (( ${#VIOLATIONS[@]} > 0 )); then
    echo "==> NOT ISOLATED: ${#VIOLATIONS[@]} violation(s)" >&2
    exit 1
fi
echo "==> ISOLATED: staging shares no config, data path, secret or runtime with production."
exit 0
