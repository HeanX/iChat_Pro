#!/usr/bin/env bash
# Post-deploy smoke gate (P4 Test T23 / Issue #204).
#
# `deploy.sh` runs this AFTER the readiness probe and BEFORE appending the
# DEPLOYMENTS.log entry, so a failing check leaves the release unrecorded and
# the deploy exits non-zero. Readiness only proves "Daphne answers and DB+Redis
# are up"; it does not prove the login form, CSRF handling, session cookies or
# the authenticated APIs still work. This script does.
#
# Checks, each one required:
#   health    GET  /health/live/                -> 200 + {"status": "ok"}
#   readiness GET  /health/ready/               -> 200 + {"status": "ok"}
#   contract  POST /health/ready/               -> 405 (gate endpoint stays GET-only)
#   login     GET  /login/                      -> 200 + csrfmiddlewaretoken
#   session   POST /login/                      -> 302 + sessionid cookie
#   core API  GET  /api/conversations/          -> 200 JSON with "conversations"
#             GET  /api/profile/me/             -> 200 JSON with "profile"
#   auth      GET  /api/conversations/ (anon)   -> 401 (never an HTML redirect)
#
# The smoke session is destroyed again in the EXIT trap (POST /logout/), so a
# deploy does not leave a live session behind.
#
# Usage (deploy.sh does this for you):
#   sudo -u ichat deploy/smoke.sh                       # uses deploy.sh's env
#   BASE_URL=https://chat.example.com:8443 \
#     SMOKE_USERNAME=... SMOKE_PASSWORD=... deploy/smoke.sh
#
# Environment:
#   BASE_URL            Required unless AUTO_BASE_URL=1. No trailing slash.
#   SMOKE_USERNAME      Dedicated smoke account — never a real user.
#   SMOKE_PASSWORD      Its password.
#   AUTO_BASE_URL       "1": derive BASE_URL like deploy.sh does (TLS front door
#                       https://127.0.0.1:8443, else loopback http://127.0.0.1:8000).
#   DJANGO_ALLOWED_HOSTS  Used with AUTO_BASE_URL as the Host header.
#   SMOKE_INSECURE_TLS  "1" -> add -k (self-signed cert on a fresh host).
#   SMOKE_TIMEOUT       Per-request timeout in seconds (default 10).
#
# Exit: 0 all checks passed, 1 a check failed, 2 usage/config error.
#
# Secrets never reach argv: the URL-encoded form body is assembled into a
# 0600 file and handed to curl with `--data-binary @file`, so `ps` and the
# shell history stay clean. Only status codes and boolean assertions print.
set -uo pipefail

CHECK_TIMEOUT=${SMOKE_TIMEOUT:-10}
CURL_TLS=()
[[ "${SMOKE_INSECURE_TLS:-0}" == "1" ]] && CURL_TLS=(-k)

FAILURES=0

# Scratch space: prefer TMPDIR, then /tmp, then $HOME, then the CWD. Git Bash
# on Windows ships a /tmp that is not writable for every shell, and a deploy
# must not fail for that reason alone.
make_workdir() {
    local base candidate
    for base in "${TMPDIR:-}" /tmp "$HOME" "${PWD:-/}"; do
        [[ -n "$base" && -d "$base" && -w "$base" ]] || continue
        candidate=$(mktemp -d "${base%/}/ichat-smoke.XXXXXXXX" 2>/dev/null) || continue
        printf '%s' "$candidate"
        return 0
    done
    return 1
}
WORKDIR=$(make_workdir) || {
    echo "ERROR: cannot create a scratch directory (tried TMPDIR, /tmp, \$HOME, \$PWD)" >&2
    exit 2
}
COOKIE_JAR=$WORKDIR/cookies.txt
LOGIN_HTML=$WORKDIR/login.html
POST_BODY=$WORKDIR/login.body
chmod 700 "$WORKDIR"
: > "$COOKIE_JAR"
: > "$LOGIN_HTML"
: > "$POST_BODY"
chmod 600 "$COOKIE_JAR" "$LOGIN_HTML" "$POST_BODY"

cleanup() {
    # Drop the smoke session first, then the whole scratch directory.
    if [[ -s "$COOKIE_JAR" && -n "${BASE_URL:-}" ]]; then
        curl "${CURL_TLS[@]}" -sS -o /dev/null --max-time "$CHECK_TIMEOUT" \
            ${HOST_HEADER[@]+"${HOST_HEADER[@]}"} \
            -b "$COOKIE_JAR" "$BASE_URL/logout/" >/dev/null 2>&1 || true
    fi
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

pass() { printf '  [PASS] %s\n' "$1"; }
fail() { printf '  [FAIL] %s\n' "$1" >&2; FAILURES=$((FAILURES + 1)); }

# --- configuration -------------------------------------------------------
HOST_HEADER=()
if [[ "${AUTO_BASE_URL:-0}" == "1" ]]; then
    HOST_NAME=${DJANGO_ALLOWED_HOSTS:-127.0.0.1}
    HOST_NAME=${HOST_NAME%%,*}
    HOST_HEADER=(-H "Host: $HOST_NAME")
    BASE_URL=${BASE_URL:-https://127.0.0.1:8443}
    if ! curl "${CURL_TLS[@]}" -fsS -o /dev/null --max-time 3 \
            -H "Host: $HOST_NAME" "$BASE_URL/health/live/" 2>/dev/null; then
        BASE_URL=http://127.0.0.1:8000
    fi
fi

if [[ -z "${BASE_URL:-}" ]]; then
    echo "ERROR: BASE_URL is required (or pass AUTO_BASE_URL=1)." >&2
    exit 2
fi
BASE_URL=${BASE_URL%/}

if [[ -z "${SMOKE_USERNAME:-}" || -z "${SMOKE_PASSWORD:-}" ]]; then
    echo "ERROR: SMOKE_USERNAME and SMOKE_PASSWORD must name a dedicated smoke" >&2
    echo "       account. Refusing to run without credentials." >&2
    exit 2
fi

# Django's CSRF check for a secure request compares the Referer host against
# the request host, and behind Nginx the request host is the Host header we
# inject — not the 127.0.0.1 we dial. Build the origin Django will see.
if [[ ${#HOST_HEADER[@]} -gt 0 ]]; then
    PORT_PART=${BASE_URL#"${BASE_URL%%:*}"}   # "" or ":8443"
    SITE_ORIGIN="https://${HOST_NAME}${PORT_PART}"
else
    SITE_ORIGIN=$BASE_URL
fi

echo "==> Post-deploy smoke (P4 Test T23) against $BASE_URL"

# --- 1. health + readiness ----------------------------------------------
http_code() {  # http_code <url> [extra curl args...]; body/headers discarded
    local url=$1; shift
    curl "${CURL_TLS[@]}" -sS -o /dev/null --max-time "$CHECK_TIMEOUT" \
        ${HOST_HEADER[@]+"${HOST_HEADER[@]}"} -w '%{http_code}' "$@" "$url" 2>/dev/null || true
}

http_body_and_code() {  # sets BODY and CODE from the last line of -w output
    local url=$1; shift
    local out
    out=$(curl "${CURL_TLS[@]}" -sS --max-time "$CHECK_TIMEOUT" \
            ${HOST_HEADER[@]+"${HOST_HEADER[@]}"} -w $'\n%{http_code}' "$@" "$url" 2>/dev/null || true)
    CODE=${out##*$'\n'}
    BODY=${out%$'\n'*}
}

http_body_and_code "$BASE_URL/health/live/"
if [[ "$CODE" == "200" && "$BODY" == *'"status": "ok"'* ]]; then
    pass "health: GET /health/live/ -> 200 ok"
else
    fail "health: GET /health/live/ -> ${CODE:-no-response} (expected 200 and \"status\": \"ok\")"
fi

http_body_and_code "$BASE_URL/health/ready/"
if [[ "$CODE" == "200" && "$BODY" == *'"status": "ok"'* ]]; then
    pass "readiness: GET /health/ready/ -> 200 ok (db + cache)"
else
    fail "readiness: GET /health/ready/ -> ${CODE:-no-response} (expected 200 and \"status\": \"ok\")"
fi

# The readiness endpoint backs the release gate; a POST must never be able to
# return the "ok" body that the gate greps for. Django's CSRF middleware
# rejects an unauthenticated POST first (403), and @require_GET refuses the
# request once CSRF passes, so accept either refusal — but never a 200.
ready_post=$(http_code "$BASE_URL/health/ready/" -X POST)
if [[ "$ready_post" == "200" ]]; then
    fail "contract: POST /health/ready/ -> 200 (the gate could be fooled by a POST)"
elif [[ "$ready_post" =~ ^(403|405)$ ]]; then
    pass "contract: POST /health/ready/ -> $ready_post (refused, cannot fake readiness)"
else
    fail "contract: POST /health/ready/ -> ${ready_post:-no-response} (expected 403/405, never 200)"
fi

# --- 2. login form + CSRF token -----------------------------------------
login_get=$(http_code "$BASE_URL/login/" -c "$COOKIE_JAR")
if [[ "$login_get" != "200" ]]; then
    fail "login form: GET /login/ -> ${login_get:-no-response} (expected 200)"
    echo "==> Smoke FAILED: $FAILURES check(s) failed." >&2
    exit 1
fi
pass "login form: GET /login/ -> 200"

curl "${CURL_TLS[@]}" -sS -o "$LOGIN_HTML" --max-time "$CHECK_TIMEOUT" \
    ${HOST_HEADER[@]+"${HOST_HEADER[@]}"} -c "$COOKIE_JAR" "$BASE_URL/login/" \
    >/dev/null 2>&1 || true
CSRF_TOKEN=$(grep -o 'name="csrfmiddlewaretoken" value="[^"]*"' "$LOGIN_HTML" \
    | head -1 | sed 's/.*value="//; s/"$//')
if [[ -z "$CSRF_TOKEN" ]]; then
    fail "csrf: no csrfmiddlewaretoken rendered in the login form"
    echo "==> Smoke FAILED: $FAILURES check(s) failed." >&2
    exit 1
fi
if grep -q 'sessionid' "$COOKIE_JAR"; then
    fail "csrf: GET /login/ issued a sessionid before authentication"
else
    pass "csrf: token rendered, no session before login"
fi

# --- 3. login POST (body from a 0600 file, never argv) -------------------
# The form body is written to $POST_BODY and replayed with --data-binary @file.
# (--data-urlencode name@file is not portable: MSYS/Git Bash rewrites the
# argument before curl can read it, and some curl builds refuse it outright.)
urlencode() {
    # RFC 3986 percent-encoding for the characters that appear in credentials
    # and CSRF tokens; leaves unreserved characters alone.
    local LC_ALL=C s=$1 out='' c i
    for (( i = 0; i < ${#s}; i++ )); do
        c=${s:i:1}
        case "$c" in
            [a-zA-Z0-9.~_-]) out+=$c ;;
            *) out+=$(printf '%%%02X' "'$c") ;;
        esac
    done
    printf '%s' "$out"
}

printf 'username=%s&password=%s&csrfmiddlewaretoken=%s&next=%%2F' \
    "$(urlencode "$SMOKE_USERNAME")" \
    "$(urlencode "$SMOKE_PASSWORD")" \
    "$(urlencode "$CSRF_TOKEN")" > "$POST_BODY"
chmod 600 "$POST_BODY"

login_post=$(
    curl "${CURL_TLS[@]}" -sS -o /dev/null --max-time "$CHECK_TIMEOUT" \
        ${HOST_HEADER[@]+"${HOST_HEADER[@]}"} \
        -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
        -H "Referer: ${SITE_ORIGIN}/login/" \
        -H 'Content-Type: application/x-www-form-urlencoded' \
        --data-binary @"$POST_BODY" \
        -w '%{http_code}' \
        "$BASE_URL/login/" 2>/dev/null || true
)
if [[ "$login_post" == "302" ]]; then
    pass "login: POST /login/ -> 302 (credentials accepted)"
else
    fail "login: POST /login/ -> ${login_post:-no-response} (expected 302; wrong credentials, CSRF rejection or broken auth)"
fi
if grep -q 'sessionid' "$COOKIE_JAR"; then
    pass "session: sessionid cookie issued"
else
    fail "session: no sessionid cookie after login"
fi

# --- 4. authenticated core APIs -----------------------------------------
http_body_and_code "$BASE_URL/api/conversations/" -b "$COOKIE_JAR"
if [[ "$CODE" == "200" && "$BODY" == *'"conversations"'* ]]; then
    pass "core API: GET /api/conversations/ -> 200 with conversations"
else
    fail "core API: GET /api/conversations/ -> ${CODE:-no-response} (expected 200 JSON with \"conversations\")"
fi

http_body_and_code "$BASE_URL/api/profile/me/" -b "$COOKIE_JAR"
if [[ "$CODE" == "200" && "$BODY" == *'"profile"'* ]]; then
    pass "core API: GET /api/profile/me/ -> 200 with profile"
else
    fail "core API: GET /api/profile/me/ -> ${CODE:-no-response} (expected 200 JSON with \"profile\")"
fi

# --- 5. unauthenticated API contract (T31) ------------------------------
anon_code=$(http_code "$BASE_URL/api/conversations/")
if [[ "$anon_code" == "401" ]]; then
    pass "auth contract: unauthenticated /api/conversations/ -> 401"
else
    fail "auth contract: unauthenticated /api/conversations/ -> ${anon_code:-no-response} (expected 401; a 302 breaks XHR clients)"
fi

# --- result --------------------------------------------------------------
if (( FAILURES > 0 )); then
    echo "==> Smoke FAILED: $FAILURES check(s) failed; the release must not be recorded." >&2
    exit 1
fi
echo "==> Smoke PASSED: health, login, session, core APIs and auth contract all ok."
exit 0
