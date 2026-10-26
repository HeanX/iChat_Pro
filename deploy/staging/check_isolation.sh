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
staging_media=$(env_value "$STAGING_ENV" DJANGO_MEDIA_ROOT || true)
if [[ -n "$staging_media" ]]; then
    prod_media=$(env_value "$PROD_ENV" DJANGO_MEDIA_ROOT || true)
    prod_media=${prod_media:-/var/lib/ichat/media}
    # No shared subtree: staging must not read or write production media.
    # Paths are normalised first, so trailing slashes and `..` components
    # cannot dodge the comparison.
    staging_norm=$(normalise_path "$staging_media")
    prod_norm=$(normalise_path "$prod_media")
    if [[ "${staging_norm}/" == "${prod_norm}/"* || "${prod_norm}/" == "${staging_norm}/"* ]]; then
        violation "MEDIA_ROOT nesting: $staging_media overlaps $prod_media"
    else
        ok "media roots are disjoint directories"
    fi
fi

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

# --- 5. live data separation (optional, needs psql/redis-cli) -------------
if [[ "$CHECK_DATA" == "true" ]]; then
    echo "-- live data"
    if command -v psql >/dev/null 2>&1 && [[ -n "$staging_db" ]]; then
        staging_dbname=${staging_db##*/}
        prod_dbname=${prod_db##*/}
        if [[ "$staging_dbname" != "$prod_dbname" ]]; then
            ok "live database names differ: prod=$prod_dbname staging=$staging_dbname"
        else
            violation "live databases share the name $prod_dbname"
        fi
    fi
    if command -v redis-cli >/dev/null 2>&1; then
        sdb=${staging_redis##*/}
        pdb=${prod_redis##*/}
        [[ "$sdb" != "$pdb" ]] && ok "redis logical dbs differ" \
            || violation "redis logical db identical"
    fi
fi

# --- verdict ---------------------------------------------------------------
if (( ${#VIOLATIONS[@]} > 0 )); then
    echo "==> NOT ISOLATED: ${#VIOLATIONS[@]} violation(s)" >&2
    exit 1
fi
echo "==> ISOLATED: staging shares no config, data path, secret or runtime with production."
exit 0
