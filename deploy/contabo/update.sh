#!/usr/bin/env bash
# Update the site to the newest code:   sudo bash /srv/medf/app/deploy/contabo/update.sh
set -euo pipefail
SITE=/srv/medf/app/src/MedicalForms
VENV=/srv/medf/venv
sudo -u medf git -C /srv/medf/app pull --ff-only
sudo -u medf "$VENV/bin/pip" install -q -r /srv/medf/app/requirements/prod.txt gunicorn
cd "$SITE"
sudo -u medf "$VENV/bin/python" manage.py migrate --noinput
sudo -u medf "$VENV/bin/python" manage.py collectstatic --noinput -v0
systemctl restart medf
echo "Updated."
