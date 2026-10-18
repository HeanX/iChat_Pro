#!/usr/bin/env bash
# Resource / certificate / readiness monitoring for iChat Pro
# (P4 T12 / Issue #139). Designed for the shared t3.small (1.9 GiB) host where
# Xray already occupies 443 and Prometheus/Grafana would be too heavy: one
# script, run from cron, that checks the four things the acceptance criteria
# name and notifies through a webhook when a threshold is crossed.
#
#   能发现服务、资源、证书和关键错误状态
#       服务  — /health/ready/ consecutive failures
#       资源  — PostgreSQL disk usage + connection count, Redis memory
#       证书  — remaining validity of the installed certificate
#       错误  — journalctl -u ichat error-level entries in the last window
#
# A check that CANNOT RUN (tool missing, auth rejected, state unwritable) is
# itself alerted on and makes the run exit 1: "cannot observe" must never be
# indistinguishable from "everything is fine".
#
# State is kept in /var/lib/ichat/monitor so "consecutive failures" survives
# across runs. Never alert on a single failed probe: a 30-second deploy
# restart would page nobody.
#
# Usage:
#   sudo /opt/ichat/repo/deploy/monitor.sh                 # from cron
#   ICHAT_ALERT_TEST=1 /opt/ichat/repo/deploy/monitor.sh   # send a test alert
#
# Cron (root):
#   */5 * * * * /opt/ichat/repo/deploy/monitor.sh >> /var/log/ichat-monitor.log 2>&1
#
# Configuration (in /opt/ichat/.env):
#   ICHAT_ALERT_WEBHOOK    https://hooks… (Slack/Discord/generic). Without it
#                          alerts only reach stderr and syslog — that is a
#                          degraded mode, not a silent one.
#   ICHAT_ALERT_MIN_SECS   minimum seconds between two alerts for the same key
#                          (default 1800) — no alert storms.
#   ICHAT_DISK_PCT         alert when the data volume exceeds this percent
#                          (default 85)
#   ICHAT_PG_MAX_CONN      alert when pg connection count reaches this
#                          (default 40)
#   ICHAT_REDIS_MAX_MB     alert when used_memory peaks above this MB
#                          (default 512)
#   ICHAT_READY_MAX_FAILS  consecutive readiness failures before alerting
#                          (default 3)
#   ICHAT_CERT_MIN_DAYS    alert when the TLS cert expires within this many
#                          days (default 14)
#   ICHAT_BASE_URL         readiness probe origin (default https://127.0.0.1:8443)
#   ICHAT_DB_URL           PostgreSQL URL for the pg-conn check; falls back to
#                          DATABASE_URL in $ENV_FILE. The password is passed to
#                          libpq through PGPASSWORD, never through argv, so it
#                          is invisible in `ps`.
#
# Red lines honoured: reads only — no service is restarted, no port is
# touched, nothing under /etc/x-ui, and no secret (DB password, cookie,
# payload) is ever included in an alert message.
set -uo pipefail

ENV_FILE=${ICHAT_ENV_FILE:-/opt/ichat/.env}
STATE_DIR=${ICHAT_MONITOR_STATE:-/var/lib/ichat/monitor}
HEALTH_URL=${ICHAT_BASE_URL:-https://127.0.0.1:8443}

# Pull the knobs we act on out of .env without sourcing it: .env carries DB
# credentials that must not leak into every child process of a monitor run.
if [[ -f "$ENV_FILE" ]]; then
    while IFS='=' read -r key value; do
        case "$key" in
            ICHAT_ALERT_WEBHOOK|DJANGO_ALLOWED_HOSTS|ICHAT_DB_URL|DATABASE_URL)
                export "$key=$value"
                ;;
        esac
    done < <(grep -E '^(ICHAT_ALERT_WEBHOOK|DJANGO_ALLOWED_HOSTS|ICHAT_DB_URL|DATABASE_URL)=' "$ENV_FILE" 2>/dev/null)
fi

ALLOWED_HOST=${DJANGO_ALLOWED_HOSTS:-}
HOST_HEADER=()
[[ -n "$ALLOWED_HOST" ]] && HOST_HEADER=(-H "Host: ${ALLOWED_HOST%%,*}")

num_or_default() {  # num_or_default <value> <fallback>
    local value=$1 fallback=$2
    [[ "$value" =~ ^[0-9]+$ ]] && printf '%s' "$value" || printf '%s' "$fallback"
}
DISK_PCT=$(num_or_default "${ICHAT_DISK_PCT:-}" 85)
PG_MAX_CONN=$(num_or_default "${ICHAT_PG_MAX_CONN:-}" 40)
REDIS_MAX_MB=$(num_or_default "${ICHAT_REDIS_MAX_MB:-}" 512)
READY_MAX_FAILS=$(num_or_default "${ICHAT_READY_MAX_FAILS:-}" 3)
CERT_MIN_DAYS=$(num_or_default "${ICHAT_CERT_MIN_DAYS:-}" 14)
MIN_ALERT_SECS=$(num_or_default "${ICHAT_ALERT_MIN_SECS:-}" 1800)
NOW=$(date -u +%s)

# Paths are injectable so the checks can be exercised off-host (tests); the
# defaults are the production locations.
PG_DATA_DIR=${ICHAT_PG_DATA_DIR:-/var/lib/postgresql}
LETLS_DIR=${ICHAT_LETLS_DIR:-/etc/letsencrypt/live}

mkdir -p "$STATE_DIR" 2>/dev/null || true
STATE_WRITABLE=0
if [[ -d "$STATE_DIR" && -w "$STATE_DIR" ]]; then
    STATE_WRITABLE=1
fi

log()  { printf '[monitor] %s\n' "$*"; }
err()  { printf '[monitor] %s\n' "$*" >&2; }

# Checks that could not run are collected here; the run finishes with a
# dedicated alert and exit 1 (see run() at the bottom).
CHECK_FAILURES=()
check_failed() {  # check_failed <check-name> <reason>
    CHECK_FAILURES+=("$1")
    err "CHECK FAILED: $1 — $2"
}

# --- state helpers (atomic write, validated read) -------------------------
write_state() {  # write_state <name> <numeric-value>
    (( STATE_WRITABLE )) || return 0
    local tmp="$STATE_DIR/.$1.tmp"
    printf '%s\n' "$2" > "$tmp" 2>/dev/null || { STATE_WRITABLE=0; return 1; }
    mv -f "$tmp" "$STATE_DIR/$1" 2>/dev/null || { STATE_WRITABLE=0; return 1; }
    return 0
}

read_state() {  # read_state <name> <default>
    local value
    value=$(cat "$STATE_DIR/$1" 2>/dev/null || true)
    [[ "$value" =~ ^[0-9]+$ ]] && printf '%s' "$value" || printf '%s' "$2"
}

# --- alert plumbing -------------------------------------------------------
json_escape() {  # message -> JSON string body (no control characters survive)
    local s=$1
    s=${s//\\/\\\\}
    s=${s//\"/\\\"}
    s=${s//$'\n'/ }
    s=${s//$'\r'/ }
    printf '%s' "$s"
}

should_alert() {  # rate-limit check only; stamping happens after delivery
    local key=$1 last=0
    last=$(read_state "last-alert-$key" 0)
    (( NOW - last >= MIN_ALERT_SECS ))
}

send_alert() {
    local key=$1 message=$2
    if [[ "${ICHAT_ALERT_TEST:-0}" != "1" ]] && ! should_alert "$key"; then
        log "alert suppressed (rate limit): $key — $message"
        return 0
    fi
    err "ALERT [$key] $message"
    logger -t ichat-monitor "ALERT [$key] $message" 2>/dev/null || true
    if [[ -n "${ICHAT_ALERT_WEBHOOK:-}" ]]; then
        if curl -fsS --max-time 10 -H 'Content-Type: application/json' \
                -d "{\"text\":\"[ichat $key] $(json_escape "$message")\"}" \
                "$ICHAT_ALERT_WEBHOOK" >/dev/null 2>&1; then
            write_state "last-alert-$key" "$NOW"
        else
            # Delivery failed: leave the stamp untouched so the next run
            # retries instead of staying silent for the whole rate window.
            err "webhook delivery failed for $key; will retry next run"
        fi
    else
        # Log-only mode: stamp anyway, or a broken webhook config would log
        # on every 5-minute run.
        write_state "last-alert-$key" "$NOW"
        err "no ICHAT_ALERT_WEBHOOK configured; alert only logged"
    fi
}

# --- 1. readiness (service up) -------------------------------------------
check_readiness() {
    local body code fails had
    body=$(curl -kS --max-time 10 "${HOST_HEADER[@]}" \
        -w '\n%{http_code}' "$HEALTH_URL/health/ready/" 2>/dev/null || true)
    code=${body##*$'\n'}
    if [[ "$code" == "200" && "$body" == *'"status": "ok"'* ]]; then
        had=$(read_state "ready-fails" 0)
        if (( had >= READY_MAX_FAILS )); then
            log "readiness recovered after $had failed probe(s)"
        fi
        write_state "ready-fails" 0
        return 0
    fi
    if (( ! STATE_WRITABLE )); then
        # The consecutive counter cannot persist, so "3 in a row" can never
        # be established — say so instead of silently never alerting.
        check_failed "ready" "state dir $STATE_DIR not writable; failure counter lost"
        return 0
    fi
    fails=$(( $(read_state "ready-fails" 0) + 1 ))
    write_state "ready-fails" "$fails"
    log "readiness check $fails/$READY_MAX_FAILS failed (http=${code:-none})"
    if (( fails >= READY_MAX_FAILS )); then
        send_alert "ready" "readiness failed $fails consecutive probes (http=${code:-none}); service degraded"
    fi
}

# --- 2. PostgreSQL: disk + connections ------------------------------------
check_disk() {
    local pg_dir=$PG_DATA_DIR
    [[ -d "$pg_dir" ]] || pg_dir=/var
    local pct
    pct=$(df -P "$pg_dir" 2>/dev/null | awk 'NR==2 {gsub(/%/,""); print $5}')
    if [[ -z "$pct" || ! "$pct" =~ ^[0-9]+$ ]]; then
        check_failed "disk" "could not read usage for $pg_dir"
        return 0
    fi
    if (( pct >= DISK_PCT )); then
        send_alert "disk" "disk usage at ${pct}% (>= ${DISK_PCT}%) on $(df -P "$pg_dir" | awk 'NR==2 {print $6}')"
    else
        log "disk: ${pct}% (ok)"
    fi
}

# Parse DATABASE_URL into libpq env vars so the password travels through the
# environment (invisible in ps) rather than as a psql argv element.
load_pg_env() {
    local url=${ICHAT_DB_URL:-${DATABASE_URL:-}}
    [[ "$url" == postgres* ]] || return 1
    # An interpreter must actually RUN: Windows ships a python3 alias that is
    # a Store stub (present in PATH, exits silently), so probe before use.
    local py=${ICHAT_PYTHON:-/opt/ichat/.venv/bin/python}
    if ! command -v "$py" >/dev/null 2>&1 || ! "$py" -c 'pass' >/dev/null 2>&1; then
        py=""
        local candidate
        for candidate in python3 python; do
            if command -v "$candidate" >/dev/null 2>&1 \
                    && "$candidate" -c 'pass' >/dev/null 2>&1; then
                py=$candidate
                break
            fi
        done
    fi
    [[ -n "$py" ]] || return 1
    local parsed
    parsed=$("$py" -c '
import sys, urllib.parse as up
p = up.urlparse(sys.argv[1])
print(p.hostname or "")
print(p.port or 5432)
print(up.unquote(p.username or ""))
print(up.unquote(p.password or ""))
print((p.path or "/").lstrip("/"))
' "$url" 2>/dev/null) || return 1
    local host port user pass db
    { read -r host; read -r port; read -r user; read -r pass; read -r db; } <<< "$parsed"
    [[ -n "$host" && -n "$user" ]] || return 1
    export PGHOST=$host PGPORT=$port PGUSER=$user PGPASSWORD=$pass PGDATABASE=$db
    return 0
}

check_pg_connections() {
    command -v psql >/dev/null 2>&1 || {
        check_failed "pg-conn" "psql not installed"
        return 0
    }
    load_pg_env || {
        check_failed "pg-conn" "no usable DATABASE_URL (postgres://) in $ENV_FILE; cannot authenticate (peer auth as root does not map to the ichat role)"
        return 0
    }
    local count
    count=$(psql -X -qAt -c 'SELECT count(*) FROM pg_stat_activity;' 2>/dev/null || true)
    if [[ -z "$count" || ! "$count" =~ ^[0-9]+$ ]]; then
        check_failed "pg-conn" "connection query failed (check credentials / pg_hba)"
        return 0
    fi
    if (( count >= PG_MAX_CONN )); then
        send_alert "pg-conn" "PostgreSQL connections at $count (>= $PG_MAX_CONN)"
    else
        log "pg connections: $count (ok)"
    fi
}

# --- 3. Redis memory -------------------------------------------------------
check_redis() {
    command -v redis-cli >/dev/null 2>&1 || {
        check_failed "redis-mem" "redis-cli not installed"
        return 0
    }
    local used_mb
    used_mb=$(redis-cli -h 127.0.0.1 -p 6379 --no-auth-warning info memory 2>/dev/null \
        | awk -F: '/used_memory:/ {printf "%d", $2/1048576}')
    if [[ -z "$used_mb" || ! "$used_mb" =~ ^[0-9]+$ ]]; then
        check_failed "redis-mem" "could not read used_memory"
        return 0
    fi
    if (( used_mb >= REDIS_MAX_MB )); then
        send_alert "redis-mem" "Redis used_memory ${used_mb}MB (>= ${REDIS_MAX_MB}MB)"
    else
        log "redis memory: ${used_mb}MB (ok)"
    fi
}

# --- 4. certificate expiry ------------------------------------------------
# certbot renew --dry-run hits the Let's Encrypt staging endpoint and is rate
# limited; instead read the expiry certbot already knows and alert when the
# renewal window is being missed (default renew happens at <30 days).
check_certificate() {
    local cert_dir end days_left
    cert_dir=$(ls -1d "$LETLS_DIR"/*/ 2>/dev/null | head -1)
    if [[ -z "$cert_dir" ]]; then
        check_failed "cert" "no certificate directory under $LETLS_DIR"
        return 0
    fi
    end=$(openssl x509 -enddate -noout -in "${cert_dir}cert.pem" 2>/dev/null | cut -d= -f2)
    if [[ -z "$end" ]]; then
        check_failed "cert" "could not read expiry from ${cert_dir}cert.pem"
        return 0
    fi
    days_left=$(( ($(date -d "$end" +%s) - NOW) / 86400 ))
    if (( days_left < CERT_MIN_DAYS )); then
        send_alert "cert" "TLS cert expires in ${days_left} day(s) (< $CERT_MIN_DAYS); certbot renew is not succeeding — check /var/log/letsencrypt/letsencrypt.log"
    else
        log "cert: expires in ${days_left} days (ok)"
    fi
}

# --- 5. application error log --------------------------------------------
check_journal_errors() {
    command -v journalctl >/dev/null 2>&1 || {
        check_failed "journal" "journalctl not installed"
        return 0
    }
    local count
    count=$(journalctl -u ichat --since "-10 min" -p err 2>/dev/null | grep -c . || true)
    # journalctl prints the header lines too; subtract the two-line banner.
    count=$(( count > 2 ? count - 2 : 0 ))
    if (( count >= 10 )); then
        send_alert "journal" "$count error-level ichat journal entries in the last 10 min (threshold 10)"
    else
        log "journal errors (10 min): $count (ok)"
    fi
}

# --- run -------------------------------------------------------------------
if [[ "${ICHAT_ALERT_TEST:-0}" == "1" ]]; then
    send_alert "test" "monitor test alert ($(date -u +%FT%TZ)) — if you can read this, the webhook works"
    exit 0
fi

check_readiness
check_disk
check_pg_connections
check_redis
check_certificate
check_journal_errors

if (( ${#CHECK_FAILURES[@]} > 0 )); then
    # Partial observability is a condition in its own right: the states these
    # checks cover are currently unmonitored. Rate limited like every alert.
    send_alert "monitor" "monitor checks could not run: ${CHECK_FAILURES[*]} — these states are UNMONITORED until fixed"
    log "checks finished WITH FAILURES: ${CHECK_FAILURES[*]}"
    exit 1
fi

log "checks complete"
exit 0
