#!/usr/bin/env bash
# Load the PythonAnywhere data into the new server.
#
#   sudo bash import.sh medf-pa.sql [medf-media.tar.gz]
#
# medf-pa.sql        - mysqldump of the PythonAnywhere database
# medf-media.tar.gz  - the staff photos folder (optional)
# The current database on this server is replaced (a copy of it is kept first).
set -euo pipefail
DUMP="${1:?usage: sudo bash import.sh medf-pa.sql [medf-media.tar.gz]}"
MEDIA_TAR="${2:-}"
SITE=/srv/medf/app/src/MedicalForms
VENV=/srv/medf/venv
[ "$(id -u)" = 0 ] || { echo "Run with sudo"; exit 1; }
[ -s "$DUMP" ] || { echo "File not found or empty: $DUMP"; exit 1; }

STAMP=$(date +%F_%H%M)
echo "== copy of the current database -> /srv/medf/backups/before-import-$STAMP.sql"
mysqldump medf > "/srv/medf/backups/before-import-$STAMP.sql"

echo "== loading $DUMP"
systemctl stop medf
mysql -e "DROP DATABASE medf; CREATE DATABASE medf CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
# the dump may name the PythonAnywhere database ("medf\$MDF"): load into "medf" anyway
sed -e '/^CREATE DATABASE/d' -e '/^USE `/d' "$DUMP" | mysql medf

if [ -n "$MEDIA_TAR" ]; then
    echo "== photos"
    tar -xzf "$MEDIA_TAR" -C /srv/medf/media --strip-components=1
    chown -R medf:medf /srv/medf/media
fi

echo "== migrations (newer site parts), static files"
cd "$SITE"
sudo -u medf "$VENV/bin/python" manage.py migrate --noinput
sudo -u medf "$VENV/bin/python" manage.py collectstatic --noinput -v0
systemctl start medf
echo "Done. Open the site, log in, and compare one printed prescription with PythonAnywhere."
