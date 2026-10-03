import firebase_admin
from firebase_admin import auth as fb_auth, credentials
from flask import current_app


def _init():
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(current_app.config["FIREBASE_CREDENTIALS_PATH"]))


def verify_id_token(token: str) -> dict:
    _init()
    return fb_auth.verify_id_token(token, check_revoked=True, clock_skew_seconds=10)


def mark_email_verified(uid: str) -> None:
    _init()
    fb_auth.update_user(uid, email_verified=True)


def delete_firebase_user(uid: str) -> None:
    _init()
    try:
        fb_auth.delete_user(uid)
    except Exception:
        pass