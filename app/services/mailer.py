import smtplib
from email.message import EmailMessage

from flask import current_app


def send_email(to_email: str, subject: str, body: str) -> None:
    cfg = current_app.config
    if not cfg["SMTP_HOST"]:
        current_app.logger.warning("DEV MODE - email to %s | %s", to_email, subject)
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["SMTP_FROM"]
    msg["To"] = to_email
    msg.set_content(body)
    with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15) as s:
        s.starttls()
        s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
        s.send_message(msg)