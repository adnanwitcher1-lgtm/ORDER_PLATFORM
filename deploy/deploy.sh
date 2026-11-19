#!/usr/bin/env bash
# First-time deployment of Order Platform on an Ubuntu server (run as root).
#
#   1. Copy your production env file to the server:   scp deploy/env.production root@SERVER:/root/order.env
#   2. On the server:  bash deploy.sh https://<github-token>@github.com/<user>/<repo>.git
#
# Optional variables:  PORT=8090  HOST=167.86.77.100  BRANCH=main
# It is safe to run again: it never deletes the database or the .env file.
set -euo pipefail

REPO_URL="${1:?Usage: bash deploy.sh <git repository url>}"
HOST="${HOST:-167.86.77.100}"
PORT="${PORT:-8090}"
BRANCH="${BRANCH:-main}"
APP_USER="orderplatform"
APP_DIR="/srv/order_platform"
ENV_SOURCE="${ENV_SOURCE:-/root/order.env}"
DB_NAME="order_platform"
DB_USER="order_platform"

[ "$(id -u)" -eq 0 ] || { echo "Run as root."; exit 1; }
git config --global --add safe.directory /srv/order_platform

echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git nginx libpq-dev cron curl
# Only install PostgreSQL if the server has none (an existing server is reused and left untouched).
if ! command -v pg_lsclusters >/dev/null 2>&1 || [ -z "$(pg_lsclusters -h 2>/dev/null)" ]; then
  apt-get install -y postgresql postgresql-contrib
fi

echo "==> App user and code"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash "$APP_USER"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull origin "$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

echo "==> Environment file"
if [ ! -f "$APP_DIR/.env" ]; then
  [ -f "$ENV_SOURCE" ] || { echo "Missing $ENV_SOURCE (copy deploy/env.production there first)."; exit 1; }
  cp "$ENV_SOURCE" "$APP_DIR/.env"
fi
chmod 600 "$APP_DIR/.env"
chown "$APP_USER":"$APP_USER" "$APP_DIR/.env"

set_env() {  # set_env KEY VALUE  (adds or replaces KEY in .env)
  python3 - "$APP_DIR/.env" "$1" "$2" <<'PY'
import sys
path, key, value = sys.argv[1:4]
lines = open(path).read().split("\n")
out, done = [], False
for line in lines:
    if line.startswith(key + "="):
        out.append(f"{key}={value}"); done = True
    else:
        out.append(line)
if not done:
    out.append(f"{key}={value}")
open(path, "w").write("\n".join(out))
PY
}
get_env() { grep -E "^$1=" "$APP_DIR/.env" | head -1 | cut -d= -f2- || true; }

# A new SECRET_KEY is created on the server the first time.
if [ -z "$(get_env SECRET_KEY)" ] || [ "$(get_env SECRET_KEY)" = "GENERATE_ON_SERVER" ]; then
  set_env SECRET_KEY "$(python3 -c 'import secrets,string; a=string.ascii_letters+string.digits+"-_"; print("".join(secrets.choice(a) for _ in range(64)))')"
fi
DB_PASSWORD="$(get_env DB_PASSWORD)"
if [ -z "$DB_PASSWORD" ]; then
  DB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  set_env DB_PASSWORD "$DB_PASSWORD"
fi
set_env DEBUG False
set_env ALLOWED_HOSTS "$HOST,127.0.0.1,localhost"
set_env SITE_URL "http://$HOST:$PORT"
set_env CSRF_TRUSTED_ORIGINS "http://$HOST:$PORT"
set_env GOOGLE_OAUTH_REDIRECT_URI "http://$HOST:$PORT/google-accounts/callback/"
set_env USE_HTTPS False
set_env TRUST_PROXY_HEADERS True
set_env DB_NAME "$DB_NAME"
set_env DB_USER "$DB_USER"
set_env DB_HOST 127.0.0.1
set_env BACKUP_DIR "$APP_DIR/backups"

echo "==> PostgreSQL database"
# Use the cluster that is already running on port 5432 (else the first online cluster).
PG_PORT=5432
pg_lsclusters -h | awk '$3==5432 && $4=="online"' | grep -q . \
  || PG_PORT="$(pg_lsclusters -h | awk '$4=="online"{print $3; exit}')"
[ -n "$PG_PORT" ] || { echo "No running PostgreSQL cluster found."; exit 1; }
echo "Using PostgreSQL on port $PG_PORT"
PSQL="sudo -u postgres psql -p $PG_PORT"
$PSQL -tc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1 \
  || $PSQL -c "CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASSWORD'"
$PSQL -c "ALTER ROLE $DB_USER PASSWORD '$DB_PASSWORD'"
$PSQL -tc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 \
  || sudo -u postgres createdb -p "$PG_PORT" -O "$DB_USER" "$DB_NAME"
set_env DB_PORT "$PG_PORT"
chown "$APP_USER":"$APP_USER" "$APP_DIR/.env"

echo "==> Python environment"
sudo -u "$APP_USER" bash -c "cd $APP_DIR && python3 -m venv venv && venv/bin/pip install -q --upgrade pip && venv/bin/pip install -q -r requirements.txt"

echo "==> Database tables and static files"
sudo -u "$APP_USER" bash -c "cd $APP_DIR && mkdir -p backups && venv/bin/python manage.py migrate --noinput && venv/bin/python manage.py createcachetable && venv/bin/python manage.py collectstatic --noinput"

echo "==> Gunicorn service"
cat > /etc/systemd/system/order_platform.service <<UNIT
[Unit]
Description=Order Platform (gunicorn)
After=network.target postgresql.service

[Service]
User=$APP_USER
Group=www-data
WorkingDirectory=$APP_DIR
RuntimeDirectory=order_platform
ExecStart=$APP_DIR/venv/bin/gunicorn config.wsgi:application --workers 3 --bind unix:/run/order_platform/gunicorn.sock --timeout 60
Restart=always

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now order_platform
systemctl restart order_platform

echo "==> Nginx (port $PORT)"
cat > /etc/nginx/sites-available/order_platform <<NGINX
server {
    listen $PORT;
    server_name $HOST;
    client_max_body_size 10m;

    location / {
        proxy_pass http://unix:/run/order_platform/gunicorn.sock;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/order_platform /etc/nginx/sites-enabled/order_platform
nginx -t
systemctl reload nginx
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then ufw allow "$PORT"/tcp; fi

echo "==> Scheduled jobs (checks every 5 minutes, reports daily, backup daily, cleanup weekly)"
cat > /etc/cron.d/order_platform <<CRON
*/5 * * * * $APP_USER cd $APP_DIR && venv/bin/python manage.py run_checks >> /var/log/order_platform_checks.log 2>&1
0 9 * * *   $APP_USER cd $APP_DIR && venv/bin/python manage.py send_client_reports >> /var/log/order_platform_reports.log 2>&1
30 2 * * *  $APP_USER cd $APP_DIR && venv/bin/python manage.py backup_db >> /var/log/order_platform_backup.log 2>&1
0 3 * * 0   $APP_USER cd $APP_DIR && venv/bin/python manage.py prune_old_data >> /var/log/order_platform_prune.log 2>&1
CRON
for f in checks reports backup prune; do touch /var/log/order_platform_$f.log; chown "$APP_USER" /var/log/order_platform_$f.log; done
chmod 644 /etc/cron.d/order_platform

echo
echo "Done. Open: http://$HOST:$PORT/"
echo "Create the first admin user:  sudo -u $APP_USER bash -c 'cd $APP_DIR && venv/bin/python manage.py createsuperuser'"
