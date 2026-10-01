#!/usr/bin/env bash
# Certbot deploy hook: runs after every successful certificate renewal so
# Nginx never keeps serving a stale certificate. Install to
# /etc/letsencrypt/renewal-hooks/deploy/ichat-reload-nginx.sh (chmod 755);
# `certbot renew` (cron) executes it automatically.
set -euo pipefail

/usr/sbin/nginx -t
/bin/systemctl reload nginx
/usr/bin/logger -t ichat-certbot "nginx reloaded after certificate renewal"
