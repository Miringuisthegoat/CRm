from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(190), unique=True, index=True, nullable=False)
    phone = db.Column(db.String(32))
    password_hash = db.Column(db.String(255))  # null for Firebase-managed users
    firebase_uid = db.Column(db.String(128), unique=True, index=True)
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    role = db.Column(db.String(32), default="staff", nullable=False)

    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return bool(self.password_hash) and check_password_hash(self.password_hash, raw_password)


class EmailCode(TimestampMixin, db.Model):
    __tablename__ = "email_codes"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    code_hash = db.Column(db.String(64), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    attempts = db.Column(db.Integer, default=0, nullable=False)


class Customer(TimestampMixin, db.Model):
    __tablename__ = "customers"
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(190), index=True)
    phone = db.Column(db.String(32), index=True, nullable=False)
    notes = db.Column(db.Text)


class Vehicle(TimestampMixin, db.Model):
    __tablename__ = "vehicles"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False)
    plate_number = db.Column(db.String(20), index=True, nullable=False, unique=True)
    make = db.Column(db.String(64))
    model = db.Column(db.String(64))
    network = db.Column(db.String(32), default="Safaricom")
    customer = db.relationship("Customer", backref=db.backref("vehicles", cascade="all, delete-orphan"))


class Tracker(TimestampMixin, db.Model):
    __tablename__ = "trackers"
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False)
    imei = db.Column(db.String(64), unique=True, index=True, nullable=False)
    status = db.Column(db.String(32), default="active")
    vehicle = db.relationship("Vehicle", backref=db.backref("trackers", cascade="all, delete-orphan"))


class SIMCard(TimestampMixin, db.Model):
    __tablename__ = "sim_cards"
    id = db.Column(db.Integer, primary_key=True)
    tracker_id = db.Column(db.Integer, db.ForeignKey("trackers.id", ondelete="CASCADE"), nullable=False)
    phone_number = db.Column(db.String(32), index=True, nullable=False)
    provider = db.Column(db.String(32), default="Safaricom")
    iccid = db.Column(db.String(64), unique=True, nullable=False)
    tracker = db.relationship("Tracker", backref=db.backref("sim_cards", cascade="all, delete-orphan"))


class FuelLog(TimestampMixin, db.Model):
    __tablename__ = "fuel_logs"
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False)
    liters = db.Column(db.Float, nullable=False)
    cost_kes = db.Column(db.Float, nullable=False)
    odometer_km = db.Column(db.Float)
    vehicle = db.relationship("Vehicle", backref=db.backref("fuel_logs", cascade="all, delete-orphan"))


class Accessory(TimestampMixin, db.Model):
    __tablename__ = "accessories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    sku = db.Column(db.String(64), unique=True, index=True, nullable=False)
    quantity = db.Column(db.Integer, default=0, nullable=False)
    price_kes = db.Column(db.Float, nullable=False)


class Payment(TimestampMixin, db.Model):
    __tablename__ = "payments"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id", ondelete="SET NULL"))
    amount_kes = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(32), nullable=False)  # M-Pesa, PayPal, Visa
    reference = db.Column(db.String(128), unique=True, nullable=False)


class SMSLog(TimestampMixin, db.Model):
    __tablename__ = "sms_logs"
    id = db.Column(db.Integer, primary_key=True)
    recipients = db.Column(db.Integer, nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(32), default="queued", nullable=False)
    parts = db.Column(db.Integer, default=1, nullable=False)
    cost_kes = db.Column(db.Float, default=0.0, nullable=False)


class CalendarEvent(TimestampMixin, db.Model):
    __tablename__ = "calendar_events"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    starts_at = db.Column(db.DateTime, nullable=False)
    ends_at = db.Column(db.DateTime, nullable=False)
    description = db.Column(db.Text)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
