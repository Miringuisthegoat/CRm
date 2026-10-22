import os

from app import create_app
from app.extensions import socketio
from app.services.reminders import start_reminder_loop

app = create_app()

if os.getenv("RUN_REMINDERS") == "1":   # gunicorn / production
    start_reminder_loop(app)

if __name__ == "__main__":              # local dev
    start_reminder_loop(app)
    socketio.run(app, host="0.0.0.0", port=5000)