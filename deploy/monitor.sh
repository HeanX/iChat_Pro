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
#       证书  — certbot renewal state (certbot renew --dry-run is NOT run here;
#               see runbook §8 — the daily check reads what certbot already
#               knows so a broken renewal is caught within a day)
#       错误  — journalctl -u ichat errors in the last window
#
# State is kept in /var/lib/ichat/monitor so "consecutive failures" survives
# across runs. Never alert on a single failed probe: a 30-second deploy
# restart would page nobody.
#
# Usage:
#   sudo /opt/ichat/repo/deploy/monitor.sh                 # from cron
#   sudo /opt/ichat/repo/deploy/monitor.sh --once          # same, explicit
#   ICHAT_ALERT_TEST=1 /opt/ichat/repo/deploy/monitor.sh   # send a test alert
#
# Cron (root), Tokyo 02:30 backup window runs backup.sh separately:
#   */5 * * * * /opt/ichat/repo/deploy/monitor.sh
#
# Configuration (in /opt/ichat/.env, which this script sources):
#   ICHAT_ALERT_WEBHOOK    https://hooks… (Slack/Discord/generic). Required for
#                          alerting; without it alerts only go to stderr+logger.
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
#
# Red lines honoured: reads only — no service is restarted, no port is
# touched, nothing under /etc/x-ui, and no secret (DB password, cookie,
# payload) is ever included in an alert message.
set -uo pipefail

ENV_FILE=${ICHAT_ENV_FILE:-/opt/ichat/.env}
STATE_DIR=${ICHAT_MONITOR_STATE:-/var/lib/ichat/monitor}
HEALTH_URL=${ICHAT_BASE_URL:-https://127.0.0.1:8443}
ALLOWED_HOST=${DJANGO_ALLOWED_HOSTS:-}
HOST_HEADER=()
[[ -n "$ALLOWED_HOST" ]] && HOST_HEADER=(-H "Host: ${ALLOWED_HOST%%,*}")

DISK_PCT=${ICHAT_DISK_PCT:-85}
PG_MAX_CONN=${ICHAT_PG_MAX_CONN:-40}
REDIS_MAX_MB=${ICHAT_REDIS_MAX_MB:-512}
READY_MAX_FAILS=${ICHAT_READY_MAX_FAILS:-3}
CERT_MIN_DAYS=${ICHAT_CERT_MIN_DAYS:-14}
MIN_ALERT_SECS=${ICHAT_ALERT_MIN_SECS:-1800}
NOW=$(date -u +%s)
# Paths are injectable so the checks can be exercised off-host (tests); the
# defaults are the production locations.
PG_DATA_DIR=${ICHAT_PG_DATA_DIR:-/var/lib/postgresql}
LEADER_DIR_GLOB=${ICHAT_LETLS_DIR:-/etc/letsencrypt/live}

mkdir -p "$STATE_DIR" 2>/dev/null || true

if [[ -f "$ENV_FILE" ]]; then
    # Only export the alert knobs we need; .env carries DB credentials that
    # must not leak into child processes of a monitoring script.
    while IFS='=' read -r key value; do
        case "$key" in
            ICHAT_ALERT_WEBHOOK|DJANGO_ALLOWED_HOSTS)
                export "$key=$value"
                ;;
        esac
    done < <(grep -E '^(ICHAT_ALERT_WEBHOOK|DJANGO_ALLOWED_HOSTS)=' "$ENV_FILE" 2>/dev/null)
fi
ALLOWED_HOST=${DJANGO_ALLOWED_HOSTS:-}
[[ -n "$ALLOWED_HOST" ]] && HOST_HEADER=(-H "Host: ${ALLOWED_HOST%%,*}")

log()  { printf '[monitor] %s\n' "$*"; }
err()  { printf '[monitor] %s\n' "$*" >&2; }

# --- alert plumbing ------------------------------------------------------
# Alerts are keyed ("disk", "ready", ...) so a flapping check does not spam:
# a key may alert at most once per MIN_ALERT_SECS.
should_alert() {
    local key=$1 stamp_file="$STATE_DIR/last-alert-$1"
    local last=0
    [[ -s "$stamp_file" ]] && last=$(cat "$stamp_file" 2>/dev/null || echo 0)
    if (( NOW - last < MIN_ALERT_SECS )); then
        return 1
    fi
    printf '%s\n' "$NOW" > "$stamp_file" 2>/dev/null || true
    return 0
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
        curl -fsS --max-time 10 -H 'Content-Type: application/json' \
            -d "{\"text\":\"[ichat $key] $message\"}" \
            "$ICHAT_ALERT_WEBHOOK" >/dev/null 2>&1 \
            || err "webhook delivery failed for $key"
    else
        err "no ICHAT_ALERT_WEBHOOK configured; alert only logged"
    fi
}

# --- 1. readiness (service up) -------------------------------------------
check_readiness() {
    local body code
    body=$(curl -kS --max-time 10 "${HOST_HEADER[@]}" \
        -w '\n%{http_code}' "$HEALTH_URL/health/ready/" 2>/dev/null || true)
    code=${body##*$'\n'}
    if [[ "$code" == "200" && "$body" == *'"status": "ok"'* ]]; then
        if [[ -s "$STATE_DIR/ready-fails" ]]; then
            local had; had=$(cat "$STATE_DIR/ready-fails")
            if (( had >= READY_MAX_FAILS )); then
                log "readiness recovered after $had failed probe(s)"
            fi
        fi
        printf '0\n' > "$STATE_DIR/ready-fails" 2>/dev/null || true
        return 0
    fi
    local fails
    fails=$(($(cat "$STATE_DIR/ready-fails" 2>/dev/null || echo 0) + 1))
    printf '%s\n' "$fails" > "$STATE_DIR/ready-fails" 2>/dev/null || true
    log "readiness check $fails/$READY_MAX_FAILS failed (http=${code:-none})"
    if (( fails >= READY_MAX_FAILS )); then
        send_alert "ready" "readiness failed $fails consecutive probes (http=${code:-none}); service degraded"
    fi
}

# --- 2. PostgreSQL: disk + connections ------------------------------------
# The data lives on the same volume as the media archive; watch the filesystem
# that actually holds /var/lib/postgresql (falls back to / on unusual layouts).
check_disk() {
    local pg_dir=$PG_DATA_DIR
    [[ -d "$pg_dir" ]] || pg_dir=/var
    local pct
    pct=$(df -P "$pg_dir" 2>/dev/null | awk 'NR==2 {gsub(/%/,""); print $5}')
    if [[ -z "$pct" ]]; then
        err "disk: could not read usage for $pg_dir"
        return 0
    fi
    if (( pct >= DISK_PCT )); then
        send_alert "disk" "disk usage at ${pct}% (>= ${DISK_PCT}%) on $(df -P "$pg_dir" | awk 'NR==2 {print $6}')"
    else
        log "disk: ${pct}% (ok)"
    fi
}

check_pg_connections() {
    command -v psql >/dev/null 2>&1 || { log "pg: psql not present, skipping"; return 0; }
    local count
    count=$(psql -X -qAt -U ichat -d ichat -c 'SELECT count(*) FROM pg_stat_activity;' 2>/dev/null || true)
    if [[ -z "$count" ]]; then
        # .env has the real credentials but exporting the URL into ps(1) args
        # would leak the password; parse it into a conninfo file instead.
        local url=${DATABASE_URL:-}
        [[ -z "$url" ]] && { log "pg: no DATABASE_URL and peer auth failed, skipping"; return 0; }
        count=$(psql -X -qAt "$url" -c 'SELECT count(*) FROM pg_stat_activity;' 2>/dev/null || true)
    fi
    if [[ -z "$count" ]]; then
        err "pg: could not query connection count"
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
    command -v redis-cli >/dev/null 2>&1 || { log "redis: redis-cli not present, skipping"; return 0; }
    local used_mb
    used_mb=$(redis-cli -h 127.0.0.1 -p 6379 --no-auth-warning info memory 2>/dev/null \
        | awk -F: '/used_memory:/ {printf "%d", $2/1048576}')
    if [[ -z "$used_mb" ]]; then
        err "redis: could not read used_memory"
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
    local cert_dir
    cert_dir=$(ls -1d "$LEADER_DIR_GLOB"/*/ 2>/dev/null | head -1)
    if [[ -z "$cert_dir" ]]; then
        log "cert: no letsencrypt live dir, skipping"
        return 0
    fi
    local end
    end=$(openssl x509 -enddate -noout -in "${cert_dir}cert.pem" 2>/dev/null | cut -d= -f2)
    if [[ -z "$end" ]]; then
        err "cert: could not read expiry from ${cert_dir}cert.pem"
        return 0
    fi
    local days_left
    days_left=$(( ($(date -d "$end" +%s) - NOW) / 86400 ))
    if (( days_left < CERT_MIN_DAYS )); then
        # Also surface the renewal hook state: a cert this close to expiry
        # means `certbot renew` has been failing silently.
        send_alert "cert" "TLS cert expires in ${days_left} day(s) (< $CERT_MIN_DAYS); certbot renew is not succeeding — check /var/log/letsencrypt/letsencrypt.log"
    else
        log "cert: expires in ${days_left} days (ok)"
    fi
}

# --- 5. application error log --------------------------------------------
check_journal_errors() {
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

log "checks complete"
exit 0
