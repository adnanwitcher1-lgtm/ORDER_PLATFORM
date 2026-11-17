#!/usr/bin/env bash
# Update the running server after you pushed new code to GitHub. Run as root.
set -euo pipefail
APP_DIR="/srv/order_platform"; APP_USER="orderplatform"; BRANCH="${BRANCH:-main}"
git config --global --add safe.directory "$APP_DIR"
git -C "$APP_DIR" pull origin "$BRANCH"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
sudo -u "$APP_USER" bash -c "cd $APP_DIR && venv/bin/pip install -q -r requirements.txt && venv/bin/python manage.py migrate --noinput && venv/bin/python manage.py collectstatic --noinput"
systemctl restart order_platform
echo "Updated."
