import hashlib
import hmac
import secrets
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage

from flask import current_app

from app.extensions import db
from app.models import EmailCode

RESEND_COOLDOWN_SECONDS = 60


def _hash(code: str) -> str:
    return hmac.new(current_app.config["SECRET_KEY"].encode(), code.encode(), hashlib.sha256).hexdigest()


def _send(to_email: str, code: str) -> None:
    cfg = current_app.config
    ttl = cfg["OTP_TTL_MINUTES"]
    if not cfg["SMTP_HOST"]:
        current_app.logger.warning("DEV MODE - OTP for %s is %s", to_email, code)
        return
    msg = EmailMessage()
    msg["Subject"] = "Your Carace verification code"
    msg["From"] = cfg["SMTP_FROM"]
    msg["To"] = to_email
    msg.set_content(f"Your Carace verification code is {code}.\nIt expires in {ttl} minutes.\n\nIf you didn't request this, ignore this email.")
    with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15) as s:
        s.starttls()
        s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
        s.send_message(msg)


def issue_code(user, enforce_cooldown: bool = False) -> str | None:
    """Returns an error string if throttled, else None."""
    existing = EmailCode.query.filter_by(user_id=user.id).first()
    if enforce_cooldown and existing and (datetime.utcnow() - existing.created_at).total_seconds() < RESEND_COOLDOWN_SECONDS:
        return "Please wait a minute before requesting another code."
    EmailCode.query.filter_by(user_id=user.id).delete()
    code = f"{secrets.randbelow(10**6):06d}"
    db.session.add(EmailCode(
        user_id=user.id,
        code_hash=_hash(code),
        expires_at=datetime.utcnow() + timedelta(minutes=current_app.config["OTP_TTL_MINUTES"]),
    ))
    db.session.commit()
    _send(user.email, code)
    return None


def check_code(user, code: str) -> str | None:
    """Returns an error string, or None if the code is valid."""
    rec = EmailCode.query.filter_by(user_id=user.id).first()
    if not rec or rec.expires_at < datetime.utcnow():
        return "Code expired. Request a new one."
    if rec.attempts >= current_app.config["OTP_MAX_ATTEMPTS"]:
        return "Too many attempts. Request a new code."
    rec.attempts += 1
    db.session.commit()
    if not hmac.compare_digest(rec.code_hash, _hash(code)):
        return "Incorrect code."
    db.session.delete(rec)
    db.session.commit()
    return None