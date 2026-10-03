#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/carace"
PYTHON_BIN="python3.12"

sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx postgresql postgresql-contrib redis-server ${PYTHON_BIN} ${PYTHON_BIN}-venv

sudo mkdir -p "${APP_DIR}"
sudo chown -R "$USER":"$USER" "${APP_DIR}"

${PYTHON_BIN} -m venv "${APP_DIR}/.venv"
source "${APP_DIR}/.venv/bin/activate"
pip install --upgrade pip
pip install -r "${APP_DIR}/requirements.txt"

sudo cp "${APP_DIR}/deploy/gunicorn.service" /etc/systemd/system/carace.service
sudo cp "${APP_DIR}/deploy/nginx.conf" /etc/nginx/sites-available/carace
sudo ln -sf /etc/nginx/sites-available/carace /etc/nginx/sites-enabled/carace
sudo nginx -t
sudo systemctl daemon-reload
sudo systemctl enable carace
sudo systemctl restart carace
sudo systemctl restart nginx

echo "Run certbot:"
echo "sudo certbot --nginx -d your-domain.com -d www.your-domain.com"
