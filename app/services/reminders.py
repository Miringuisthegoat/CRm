import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask import current_app

from app.extensions import db, socketio
from app.models import CalendarEvent, Customer, ReminderDelivery, Vehicle
from app.services.mailer import send_email
from app.services.sms import send_sms

NAIROBI = ZoneInfo("Africa/Nairobi")
UTC = ZoneInfo("UTC")
OFFSETS = (30, 14, 7)          # days before expiry
CHANNELS = ("sms", "email")
SEND_TIME = time(8, 0)         # Nairobi
MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=15)
POLL_SECONDS = 300

SMS_TEMPLATE = ("Dear {{name}}, your vehicle {{plate}} ({{company}}) is due to expire on {{date}} "
                "({{days}} days left). Please contact Carace to renew.")
EMAIL_SUBJECT = "Vehicle {{plate}} expires on {{date}}"
EMAIL_TEMPLATE = ("Dear {{name}},\n\nThis is a reminder that your vehicle {{plate}} ({{company}}) is due to "
                  "expire on {{date}}, in {{days}} days.\n\nPlease contact Carace to renew and avoid "
                  "interruption.\n\nCarace")


class _Skip(Exception):
    pass


def _now_local() -> datetime:
    return datetime.now(NAIROBI).replace(tzinfo=None)


def _to_utc(local: datetime) -> datetime:
    return local.replace(tzinfo=NAIROBI).astimezone(UTC).replace(tzinfo=None)


def _to_local(utc: datetime) -> datetime:
    return utc.replace(tzinfo=UTC).astimezone(NAIROBI).replace(tzinfo=None)


def _context(event, customer) -> dict:
    vehicle = db.session.get(Vehicle, event.vehicle_id) if event.vehicle_id else None
    if vehicle is None and customer.vehicles:
        vehicle = customer.vehicles[0]
    expiry = event.starts_at.date()
    return {
        "name": customer.full_name,
        "plate": vehicle.plate_number if vehicle else "",
        "company": customer.notes or "",
        "date": f"{expiry.day} {expiry:%B %Y}",
        "days": str((expiry - _now_local().date()).days),
    }


def _render(template: str, ctx: dict) -> str:
    text = re.sub(r"\{\{(\w+)\}\}", lambda m: ctx.get(m.group(1), ""), template)
    text = re.sub(r"\s*\(\)", "", text)       # drop "()" when company is empty
    return re.sub(r" {2,}", " ", text)        # drop double spaces when plate is empty


def sync_reminders() -> None:
    """Create/adjust delivery rows for every upcoming expiry event."""
    now = _now_local()
    today = now.date()
    events = CalendarEvent.query.filter(
        CalendarEvent.event_type == "expiry",
        CalendarEvent.reminders_enabled.is_(True),
        CalendarEvent.customer_id.isnot(None),
        CalendarEvent.starts_at >= datetime.combine(today, time.min),
    ).all()
    if not events:
        return
    rows = ReminderDelivery.query.filter(ReminderDelivery.event_id.in_([e.id for e in events])).all()
    by_event: dict = {}
    for r in rows:
        by_event.setdefault(r.event_id, {})[(r.offset_days, r.channel)] = r

    for event in events:
        existing = by_event.get(event.id, {})
        expiry = event.starts_at.date()
        for offset in OFFSETS:
            trigger = expiry - timedelta(days=offset)
            when = datetime.combine(trigger, SEND_TIME)
            if trigger == today:
                when = max(when, now + timedelta(minutes=1))
            for channel in CHANNELS:
                row = existing.get((offset, channel))
                if row is None:
                    past = trigger < today
                    db.session.add(ReminderDelivery(
                        event_id=event.id, offset_days=offset, channel=channel, send_at=_to_utc(when),
                        status="skipped" if past else "pending",
                        last_error="trigger date already passed" if past else None))
                elif row.status == "pending" and _to_local(row.send_at).date() != trigger:
                    if trigger < today:
                        row.status, row.last_error = "skipped", "trigger date already passed"
                    else:
                        row.send_at = _to_utc(when)   # expiry date was edited
    db.session.commit()


def _log_to_calendar(event, customer, row, ctx, ok: bool) -> None:
    channel = "SMS" if row.channel == "sms" else "email"
    plate = ctx["plate"] or "(no plate on file)"
    company = f" of company {ctx['company']}" if ctx["company"] else ""
    if ok:
        kind = "reminder_sent"
        title = f"{channel} sent: {customer.full_name}"
        desc = (f"Message sent to {customer.full_name} that vehicle {plate}{company} is due to expire "
                f"on {ctx['date']} ({row.offset_days}-day notice, via {channel}).\n\nMessage: {row.message}")
    else:
        kind = "reminder_failed"
        title = f"{channel} NOT sent: {customer.full_name}"
        desc = (f"{row.offset_days}-day {channel} reminder to {customer.full_name} for vehicle {plate}{company} "
                f"(expires {ctx['date']}) was not sent: {row.last_error}")
    now = _now_local()
    db.session.add(CalendarEvent(
        customer_id=customer.id, vehicle_id=event.vehicle_id, event_type=kind, reminders_enabled=False,
        title=title[:150], starts_at=now, ends_at=now + timedelta(minutes=5), description=desc))


def _deliver(row) -> None:
    event = db.session.get(CalendarEvent, row.event_id)
    customer = db.session.get(Customer, event.customer_id) if event and event.customer_id else None
    if (event is None or customer is None or not event.reminders_enabled
            or event.starts_at.date() < _now_local().date()):
        row.status, row.last_error = "cancelled", "event changed or expired"
        db.session.commit()
        return

    ctx = _context(event, customer)
    try:
        if row.channel == "sms":
            phone = (customer.phone or "").strip()
            if not phone or phone.upper() == "N/A":
                raise _Skip("customer has no phone number")
            row.message = _render(SMS_TEMPLATE, ctx)
            log = send_sms(row.message, [phone])
            if log.status != "sent":
                raise RuntimeError(log.reason or "SMS was not accepted")
        else:
            if not customer.email:
                raise _Skip("customer has no email address")
            row.message = _render(EMAIL_TEMPLATE, ctx)
            send_email(customer.email, _render(EMAIL_SUBJECT, ctx), row.message)
        row.status, row.last_error = "sent", None
        _log_to_calendar(event, customer, row, ctx, ok=True)
    except _Skip as skip:
        row.status, row.last_error = "skipped", str(skip)
        _log_to_calendar(event, customer, row, ctx, ok=False)
    except ValueError as exc:                      # bad phone format: retrying won't help
        row.status, row.last_error = "failed", str(exc)[:255]
        _log_to_calendar(event, customer, row, ctx, ok=False)
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Reminder %s failed", row.id)
        row.last_error = str(exc)[:255]
        if row.attempts >= MAX_ATTEMPTS:
            row.status = "failed"
            _log_to_calendar(event, customer, row, ctx, ok=False)
        else:
            row.status = "pending"
            row.send_at = datetime.utcnow() + RETRY_DELAY
    db.session.commit()


def dispatch_due(batch: int = 50) -> None:
    # Recover rows stuck in "sending" after a crash (at-least-once delivery).
    ReminderDelivery.query.filter(
        ReminderDelivery.status == "sending",
        ReminderDelivery.updated_at < datetime.utcnow() - timedelta(minutes=10),
    ).update({"status": "pending"})
    rows = (ReminderDelivery.query
            .filter(ReminderDelivery.status == "pending", ReminderDelivery.send_at <= datetime.utcnow())
            .order_by(ReminderDelivery.send_at)
            .with_for_update(skip_locked=True)
            .limit(batch).all())
    for row in rows:
        row.status = "sending"
        row.attempts += 1
    db.session.commit()
    for row in rows:
        _deliver(row)


def start_reminder_loop(app) -> None:
    def loop():
        while True:
            with app.app_context():
                try:
                    sync_reminders()
                    dispatch_due()
                except Exception:
                    db.session.rollback()
                    app.logger.exception("Reminder loop failed")
            socketio.sleep(POLL_SECONDS)
    socketio.start_background_task(loop)