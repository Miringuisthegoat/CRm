# Carace - Fleet CRM

Internal Flask CRM for Carace (Nairobi, Kenya): fleet, fuel, payments, SIM/tracker registry and customer messaging.

## Stack
- Backend: Flask, SQLAlchemy, Flask-Login, Flask-Migrate, Flask-SocketIO
- Database: PostgreSQL 16 (primary), SQLite (local fallback)
- Frontend: Bootstrap 5.3.2, Boxicons, SheetJS
- SMS: Africa's Talking
- Deployment: Ubuntu 24.04, Nginx, Gunicorn, systemd, certbot

## Features
- Dashboard KPIs
- Modular APIs via Blueprints
- Auth (login/logout/me) with hashed passwords
- Models: users, vehicles, customers, trackers, sim_cards, fuel_logs, accessories, payments, sms_logs, calendar_events
- Kenyan + East African plate validation
- Kenyan network constraints: Safaricom, Airtel, Telkom, Faiba
- SMS billing: `parts = ceil(len(message)/160)`, `cost = parts * recipients * 0.80`
- Import fuzzy column mapping: `/api/import/preview`

## Run locally (Windows PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # then edit .env (SECRET_KEY, DATABASE_URL, SMS keys)
flask --app wsgi db upgrade # first time only: flask --app wsgi db init, then db migrate -m "initial schema"
python wsgi.py              # http://127.0.0.1:5000
```

## Run locally (Linux/macOS)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
flask --app wsgi db upgrade
python wsgi.py
```

## Deploy (Contabo Ubuntu 24.04)
1. Copy project to `/opt/carace`
2. Configure `.env` with production secrets
3. Run `deploy/deploy.sh`
4. Create the database:
   `sudo -u postgres psql -f /opt/carace/deploy/postgres_setup.sql` (change the password in that file first)
5. Apply migrations: `cd /opt/carace && .venv/bin/flask --app wsgi db upgrade`
6. Enable SSL: `sudo certbot --nginx -d your-domain.com`

## Firewall
- 80/tcp, 443/tcp open
- 5000/tcp internal only

## Security notes
- Keep `.env` out of git
- Set a strong `SECRET_KEY`
- Restrict database access to localhost/private network
- Add RBAC checks per route (admin vs staff) before go-live

## Paths
- Entry: `wsgi.py`
- Factory: `app/__init__.py`
- Models: `app/models/__init__.py`
- APIs: `app/api/routes.py`
- Deployment: `deploy/`
