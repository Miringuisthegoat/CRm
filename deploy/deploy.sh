#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/carace"
PYTHON_BIN="python3.12"

sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx postgresql postgresql-contrib redis-server ufw ${PYTHON_BIN} ${PYTHON_BIN}-venv

sudo mkdir -p "${APP_DIR}"
sudo chown -R "$USER":"$USER" "${APP_DIR}"

${PYTHON_BIN} -m venv "${APP_DIR}/.venv"
source "${APP_DIR}/.venv/bin/activate"
pip install --upgrade pip
pip install -r "${APP_DIR}/requirements.txt"

# .env must be readable by the service user but not world-readable
sudo chgrp www-data "${APP_DIR}/.env"
sudo chmod 640 "${APP_DIR}/.env"

# Apply DB migrations (env loaded from .env)
set -a; source "${APP_DIR}/.env"; set +a
(cd "${APP_DIR}" && "${APP_DIR}/.venv/bin/flask" --app wsgi db upgrade)

sudo cp "${APP_DIR}/deploy/gunicorn.service" /etc/systemd/system/carace.service
sudo cp "${APP_DIR}/deploy/nginx.conf" /etc/nginx/sites-available/carace
sudo ln -sf /etc/nginx/sites-available/carace /etc/nginx/sites-enabled/carace
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t

sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw --force enable

sudo systemctl daemon-reload
sudo systemctl enable --now redis-server carace
sudo systemctl restart carace
sudo systemctl restart nginx

echo "Create the database first if needed:"
echo "  sudo -u postgres psql -f ${APP_DIR}/deploy/postgres_setup.sql   (change the password!)"
echo "Then run certbot:"
echo "  sudo certbot --nginx -d your-domain.com -d www.your-domain.com"