from dotenv import load_dotenv

load_dotenv()

import os
from datetime import timedelta


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
    SQLALCHEMY_DATABASE_URI = os.environ["DATABASE_URL"]
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 300}
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    WTF_CSRF_TIME_LIMIT = None
    RATELIMIT_DEFAULT = "120/minute"
    AFRICASTALKING_USERNAME = os.getenv("AFRICASTALKING_USERNAME", "sandbox")
    AFRICASTALKING_API_KEY = os.getenv("AFRICASTALKING_API_KEY", "")
    REDIS_URL = os.getenv("REDIS_URL", "")
    CURRENCY = "Ksh"
    FIREBASE_CREDENTIALS_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH", "")
    SIGNUP_EMAIL_DOMAIN = os.getenv("SIGNUP_EMAIL_DOMAIN", "")
    OTP_TTL_MINUTES = int(os.getenv("OTP_TTL_MINUTES", "10"))
    OTP_MAX_ATTEMPTS = 5
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM = os.getenv("SMTP_FROM", "Carace <noreply@localhost>")
    RATELIMIT_STORAGE_URI = REDIS_URL or "memory://"