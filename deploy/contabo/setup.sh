#!/usr/bin/env bash
# MedicalForms on a fresh Ubuntu 24.04 server (Contabo VPS).
#
#   sudo bash setup.sh ramaz.ge you@example.com
#
# Installs: Python + the site, MySQL 8 (the same as PythonAnywhere), LibreOffice (prescription PDF),
# nginx + free HTTPS certificate, firewall, automatic security updates,
# a daily database backup. Safe to run again (it skips what is already done).
# Secrets (database password, Django key) are generated here and kept only in
# /srv/medf/app/src/MedicalForms/.env on the server.
set -euo pipefail

DOMAIN="${1:?usage: sudo bash setup.sh DOMAIN EMAIL}"
EMAIL="${2:?usage: sudo bash setup.sh DOMAIN EMAIL}"
BRANCH="${BRANCH:-claude/dazzling-cerf-tp4313}"
REPO="https://github.com/apkhadzeangio/New_MedF.git"
APP=/srv/medf/app
SITE=$APP/src/MedicalForms
VENV=/srv/medf/venv
MEDIA=/srv/medf/media
BACKUPS=/srv/medf/backups

[ "$(id -u)" = 0 ] || { echo "Run with sudo"; exit 1; }
export DEBIAN_FRONTEND=noninteractive

echo "== 1/8 packages"
apt-get update -q
apt-get upgrade -yq
apt-get install -yq git python3 python3-venv python3-dev build-essential pkg-config \
    default-libmysqlclient-dev mysql-server nginx certbot python3-certbot-nginx \
    libreoffice-writer-nogui fonts-liberation fonts-liberation2 fonts-dejavu fonts-noto-core \
    ufw fail2ban unattended-upgrades
timedatectl set-timezone Asia/Tbilisi || true

echo "== 2/8 user and code"
id medf >/dev/null 2>&1 || useradd --system --create-home --home-dir /srv/medf --shell /bin/bash medf
mkdir -p "$MEDIA" "$BACKUPS"
if [ ! -d "$APP/.git" ]; then
    sudo -u medf git clone -b "$BRANCH" "$REPO" "$APP"
else
    sudo -u medf git -C "$APP" pull --ff-only
fi
[ -d "$VENV" ] || sudo -u medf python3 -m venv "$VENV"
sudo -u medf "$VENV/bin/pip" install -q --upgrade pip
sudo -u medf "$VENV/bin/pip" install -q -r "$APP/requirements/prod.txt" gunicorn

echo "== 3/8 database"
if [ ! -f "$SITE/.env" ]; then
    DB_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
    SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
    mysql <<SQL
CREATE DATABASE IF NOT EXISTS medf CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'medf'@'localhost' IDENTIFIED BY '$DB_PASS';
ALTER USER 'medf'@'localhost' IDENTIFIED BY '$DB_PASS';
GRANT ALL PRIVILEGES ON medf.* TO 'medf'@'localhost';
FLUSH PRIVILEGES;
SQL
    cat > "$SITE/.env" <<ENV
DJANGO_SECRET_KEY=$SECRET
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=$DOMAIN,www.$DOMAIN
DJANGO_HTTPS=1
DB_NAME=medf
DB_USER=medf
DB_HOST=localhost
DB_PASSWORD=$DB_PASS
MEDIA_ROOT=$MEDIA
ENV
    chown medf:medf "$SITE/.env"
    chmod 600 "$SITE/.env"
fi
chown -R medf:medf /srv/medf
chmod 755 /srv/medf /srv/medf/media     # nginx reads static files and photos

echo "== 4/8 Django"
cd "$SITE"
sudo -u medf "$VENV/bin/python" manage.py migrate --noinput
sudo -u medf "$VENV/bin/python" manage.py collectstatic --noinput -v0

echo "== 5/8 service (gunicorn)"
cat > /etc/systemd/system/medf.service <<UNIT
[Unit]
Description=MedicalForms (gunicorn)
After=network.target mysql.service

[Service]
User=medf
Group=www-data
WorkingDirectory=$SITE
Environment=DJANGO_SETTINGS_MODULE=MedicalForms.settings.prod
Environment=HOME=/srv/medf
ExecStart=$VENV/bin/gunicorn MedicalForms.wsgi:application --bind unix:/run/medf/medf.sock --workers 3 --timeout 120 --access-logfile - --error-logfile -
RuntimeDirectory=medf
Restart=always

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now medf
systemctl restart medf

echo "== 6/8 nginx"
cat > /etc/nginx/sites-available/medf <<NGINX
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;
    client_max_body_size 12M;

    location /static/ { alias $SITE/staticfiles/; expires 30d; access_log off; }
    location /media/  { alias $MEDIA/; expires 7d; }

    location / {
        proxy_pass http://unix:/run/medf/medf.sock;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/medf /etc/nginx/sites-enabled/medf
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "== 7/8 firewall, updates, HTTPS"
ufw allow OpenSSH >/dev/null
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null
systemctl enable --now fail2ban >/dev/null
dpkg-reconfigure -f noninteractive unattended-upgrades
if certbot --nginx -n --agree-tos -m "$EMAIL" -d "$DOMAIN" -d "www.$DOMAIN" --redirect; then
    echo "HTTPS ok"
else
    echo "!! HTTPS certificate failed: check that $DOMAIN and www.$DOMAIN point to this server (DNS A record), then run:"
    echo "   sudo certbot --nginx -d $DOMAIN -d www.$DOMAIN --redirect"
fi

echo "== 8/8 daily backup (03:15, keeps 14)"
cat > /etc/cron.d/medf-backup <<CRON
15 3 * * * medf cd $SITE && $VENV/bin/python manage.py backup_data --dir $BACKUPS --keep 14 >> /srv/medf/backup.log 2>&1
CRON

echo
echo "Done: https://$DOMAIN"
echo "Next: bring the data from PythonAnywhere — sudo bash $APP/deploy/contabo/import.sh medf-pa.sql medf-media.tar.gz"
