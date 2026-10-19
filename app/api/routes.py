from datetime import datetime, timedelta
import math
import re

from flask import Blueprint, current_app, jsonify, request
from flask_wtf.csrf import generate_csrf

try:
    from flask_login import login_required
except ImportError:  # pragma: no cover - fallback for environments without Flask-Login
    from functools import wraps

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            return view(*args, **kwargs)

        return wrapped

from app.extensions import db
from app.models import Accessory, CalendarEvent, Customer, Payment, SIMCard, Tracker, Vehicle
from app.services.importer import fuzzy_map_columns
from app.services.sms import send_sms
from app.services.utils import NETWORKS, is_valid_plate

api_bp = Blueprint("api", __name__, url_prefix="/api")
web_bp = Blueprint("web", __name__)


def _import_text(value):
    return "" if value is None else str(value).strip()


def _import_number(value):
    text = _import_text(value)
    text = re.sub(r"(?i)\b(?:KES|KSH)\b", "", text).replace(",", "").strip()
    try:
        number = float(text)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _import_datetime(value):
    text = _import_text(value)
    if not text:
        return None
    try:
        serial = float(text)
        if 1 <= serial <= 100000:
            return datetime(1899, 12, 30) + timedelta(days=serial)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        pass
    for date_format in ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, date_format)
        except ValueError:
            continue
    return None


@web_bp.get("/")
def index():
    return current_app.send_static_file("carace_dashboard.html")


@api_bp.get("/dashboard/kpis")
@login_required
def dashboard_kpis():
    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    upcoming_until = today + timedelta(days=7)
    upcoming_rows = (
        db.session.query(CalendarEvent, Customer.full_name)
        .outerjoin(Customer, CalendarEvent.customer_id == Customer.id)
        .filter(CalendarEvent.starts_at >= today, CalendarEvent.starts_at < upcoming_until)
        .order_by(CalendarEvent.starts_at)
        .all()
    )
    payment_rows = (
        db.session.query(Payment, Customer.full_name)
        .outerjoin(Customer, Payment.customer_id == Customer.id)
        .order_by(Payment.created_at.desc())
        .limit(5)
        .all()
    )
    return jsonify(
        {
            "currency": "KES",
            "vehicles": Vehicle.query.count(),
            "customers": Customer.query.count(),
            "trackers": Tracker.query.count(),
            "sim_cards": SIMCard.query.count(),
            "accessory_units": db.session.query(db.func.coalesce(db.func.sum(Accessory.quantity), 0)).scalar(),
            "payments_kes": round(db.session.query(db.func.coalesce(db.func.sum(Payment.amount_kes), 0)).scalar(), 2),
            "recent_payments": [
                {
                    "amount_kes": payment.amount_kes,
                    "method": payment.method,
                    "reference": payment.reference,
                    "customer_name": customer_name,
                    "created_at": payment.created_at.isoformat(),
                }
                for payment, customer_name in payment_rows
            ],
            "upcoming_events": [
                {
                    "title": event.title,
                    "starts_at": event.starts_at.isoformat(),
                    "customer_name": customer_name,
                }
                for event, customer_name in upcoming_rows
            ],
        }
    )


@api_bp.get("/calendar/events")
@login_required
def calendar_events():
    month = request.args.get("month", "")
    try:
        starts_at = datetime.strptime(month, "%Y-%m").replace(day=1)
    except ValueError:
        return jsonify({"error": "month must use YYYY-MM format"}), 400

    ends_at = (starts_at.replace(day=28) + timedelta(days=4)).replace(day=1)
    rows = (
        db.session.query(CalendarEvent, Customer.full_name)
        .outerjoin(Customer, CalendarEvent.customer_id == Customer.id)
        .filter(CalendarEvent.starts_at >= starts_at, CalendarEvent.starts_at < ends_at)
        .order_by(CalendarEvent.starts_at)
        .all()
    )
    return jsonify(
        [
            {
                "id": event.id,
                "title": event.title,
                "starts_at": event.starts_at.isoformat(),
                "ends_at": event.ends_at.isoformat(),
                "description": event.description,
                "customer_name": customer_name,
            }
            for event, customer_name in rows
        ]
    )


@api_bp.post("/vehicles")
@login_required
def create_vehicle():
    data = request.get_json() or {}
    if not is_valid_plate(data.get("plate_number", "")):
        return jsonify({"error": "Invalid Kenyan/East African plate format"}), 400
    network = data.get("network", "Safaricom")
    if network not in NETWORKS:
        return jsonify({"error": "Unsupported network"}), 400
    vehicle = Vehicle(
        customer_id=data["customer_id"],
        plate_number=data["plate_number"].upper(),
        make=data.get("make"),
        model=data.get("model"),
        network=network,
    )
    db.session.add(vehicle)
    db.session.commit()
    return jsonify({"ok": True, "id": vehicle.id}), 201


@api_bp.post("/sms/send")
@login_required
def sms_send():
    data = request.get_json() or {}
    log = send_sms(data["message"], data["recipients"])
    return jsonify({"id": log.id, "status": log.status, "parts": log.parts, "cost_kes": log.cost_kes})


@api_bp.post("/import/preview")
@login_required
def import_preview():
    data = request.get_json() or {}
    columns = data.get("columns", [])
    return jsonify({"mapped_columns": fuzzy_map_columns(columns), "errors": []})


@api_bp.get("/csrf-token")
@login_required
def api_csrf_token():
    return jsonify({"csrf_token": generate_csrf()})


@api_bp.get("/import/data")
@login_required
def import_data():
    customers = Customer.query.order_by(Customer.full_name).all()
    vehicles = Vehicle.query.order_by(Vehicle.plate_number).all()
    accessories = Accessory.query.order_by(Accessory.name).all()
    trackers = Tracker.query.order_by(Tracker.imei).all()
    sims = SIMCard.query.order_by(SIMCard.phone_number).all()

    return jsonify(
        {
            "customers": [
                {
                    "name": customer.full_name,
                    "company": customer.notes or "",
                    "email": customer.email or "",
                    "phone": customer.phone or "",
                    "type": "Fleet client" if customer.vehicles else "Individual",
                    "vehicles": str(len(customer.vehicles)),
                    "status": "Registered",
                    "plate": customer.vehicles[0].plate_number if customer.vehicles else "—",
                }
                for customer in customers
            ],
            "vehicles": [
                {
                    "plate": vehicle.plate_number,
                    "owner": vehicle.customer.full_name,
                    "make": " ".join(part for part in (vehicle.make, vehicle.model) if part),
                    "type": "Not recorded",
                    "mileage": "Not recorded",
                    "status": "Registered",
                    "tracker": "Assigned" if vehicle.trackers else "Not assigned",
                    "service": "Not recorded",
                }
                for vehicle in vehicles
            ],
            "accessories": [
                {
                    "product": accessory.name,
                    "price": str(accessory.price_kes),
                    "stock": accessory.quantity,
                    "status": "ok" if accessory.quantity > 0 else "out",
                }
                for accessory in accessories
            ],
            "trackers": [
                {
                    "id": tracker.id,
                    "plate": tracker.vehicle.plate_number,
                    "imei": tracker.imei,
                    "brand": "Not recorded",
                    "model": "",
                    "simNum": tracker.sim_cards[0].phone_number if tracker.sim_cards else "—",
                    "iccid": tracker.sim_cards[0].iccid if tracker.sim_cards else "—",
                    "network": tracker.sim_cards[0].provider if tracker.sim_cards else "Not recorded",
                    "status": (tracker.status or "registered").title(),
                    "signal": 0,
                    "lastSeen": "Not available",
                }
                for tracker in trackers
            ],
            "sims": [
                {
                    "sim": sim.phone_number,
                    "iccid": sim.iccid,
                    "network": sim.provider or "Not recorded",
                    "plan": "Not recorded",
                    "trackerImei": sim.tracker.imei,
                    "trackerLabel": sim.tracker.vehicle.plate_number,
                    "expiry": "Not recorded",
                    "status": "active",
                }
                for sim in sims
            ],
        }
    )


@api_bp.post("/import/commit")
@login_required
def import_commit():
    data = request.get_json(silent=True) or {}
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        return jsonify({"error": "Select a file containing at least one data row."}), 400
    if len(rows) > 500:
        return jsonify({"error": "Import is limited to 500 rows per batch."}), 400

    counts = {"customers": 0, "vehicles": 0, "trackers": 0, "sims": 0, "accessories": 0, "payments": 0, "events": 0, "duplicates": 0, "skipped": 0}
    warnings = []

    def warn(row_number, message):
        if len(warnings) < 50:
            warnings.append(f"Row {row_number}: {message}")

    try:
        for row_number, raw in enumerate(rows, 2):
            if not isinstance(raw, dict):
                counts["skipped"] += 1
                warn(row_number, "row is not valid data")
                continue

            row = {key: _import_text(raw.get(key)) for key in (
                "plate", "vehicle", "owner", "make", "model", "status", "service_date", "name", "company", "email", "phone",
                "date", "event_title", "description", "amount", "method", "ref", "product", "sku", "price", "stock",
                "imei", "tracker_imei", "sim_num", "iccid", "network", "dataplan",
            )}
            plate = (row["plate"] or row["vehicle"]).strip().upper()
            if re.fullmatch(r"K[A-Z]{2}\d{3}[A-Z]", plate):
                plate = f"{plate[:3]} {plate[3:]}"
            valid_plate = bool(plate and is_valid_plate(plate))
            if row["plate"] and not valid_plate:
                warn(row_number, f"invalid plate '{row['plate']}'")

            email = row["email"][:190].lower()
            phone = row["phone"][:32]
            customer_name = (row["name"] or row["owner"] or row["company"])[:120]
            vehicle = Vehicle.query.filter_by(plate_number=plate).first() if valid_plate else None
            customer = None
            if email:
                customer = Customer.query.filter(db.func.lower(Customer.email) == email).first()
            if customer is None and phone and phone.lower() != "n/a":
                customer = Customer.query.filter_by(phone=phone).first()
            if customer is None and customer_name:
                customer = Customer.query.filter_by(full_name=customer_name).first()
            if customer is None and vehicle:
                customer = vehicle.customer

            has_customer_data = bool(customer_name or email or (phone and phone.lower() != "n/a"))
            if customer is None and has_customer_data:
                customer = Customer(full_name=customer_name or email or phone, email=email or None, phone=phone or "N/A", notes=row["company"] or None)
                db.session.add(customer)
                db.session.flush()
                counts["customers"] += 1
            elif customer is not None:
                if email and not customer.email:
                    customer.email = email
                if phone and phone.lower() != "n/a" and customer.phone in (None, "", "N/A"):
                    customer.phone = phone
                if row["company"] and not customer.notes:
                    customer.notes = row["company"]

            handled = customer is not None
            if valid_plate:
                if customer is None:
                    warn(row_number, "vehicle not saved because its owner/customer is missing")
                elif vehicle is None:
                    network = row["network"] if row["network"] in NETWORKS else "Safaricom"
                    vehicle = Vehicle(customer_id=customer.id, plate_number=plate, make=row["make"][:64] or None, model=row["model"][:64] or None, network=network)
                    db.session.add(vehicle)
                    db.session.flush()
                    counts["vehicles"] += 1
                    handled = True
                else:
                    vehicle.customer_id = customer.id
                    vehicle.make = row["make"][:64] or vehicle.make
                    vehicle.model = row["model"][:64] or vehicle.model
                    if row["network"] in NETWORKS:
                        vehicle.network = row["network"]
                    counts["duplicates"] += 1
                    handled = True

            tracker = None
            tracker_imei = row["imei"] or row["tracker_imei"]
            if tracker_imei:
                tracker = Tracker.query.filter_by(imei=tracker_imei[:64]).first()
                if tracker is None:
                    if vehicle is None and valid_plate:
                        vehicle = Vehicle.query.filter_by(plate_number=plate).first()
                    if vehicle is None:
                        warn(row_number, "tracker not saved because no matching vehicle was found")
                    else:
                        tracker = Tracker(vehicle_id=vehicle.id, imei=tracker_imei[:64], status=(row["status"] or "registered")[:32])
                        db.session.add(tracker)
                        db.session.flush()
                        counts["trackers"] += 1
                        handled = True
                else:
                    counts["duplicates"] += 1
                    handled = True

            if row["sim_num"]:
                iccid = row["iccid"][:64]
                if not iccid:
                    warn(row_number, "SIM not saved because ICCID is required")
                else:
                    existing_sim = SIMCard.query.filter_by(iccid=iccid).first() or SIMCard.query.filter_by(phone_number=row["sim_num"][:32]).first()
                    if existing_sim:
                        counts["duplicates"] += 1
                        handled = True
                    elif tracker is None:
                        warn(row_number, "SIM not saved because its tracker IMEI is missing or unknown")
                    else:
                        db.session.add(SIMCard(tracker_id=tracker.id, phone_number=row["sim_num"][:32], provider=(row["network"] or "Safaricom")[:32], iccid=iccid))
                        counts["sims"] += 1
                        handled = True

            if row["product"]:
                price = _import_number(row["price"])
                quantity = _import_number(row["stock"])
                sku = re.sub(r"[^A-Z0-9]+", "-", (row["sku"] or row["product"]).upper()).strip("-")[:64]
                if price is None or quantity is None or not sku:
                    warn(row_number, "accessory needs a product, numeric price, and numeric stock")
                else:
                    accessory = Accessory.query.filter_by(sku=sku).first()
                    if accessory:
                        counts["duplicates"] += 1
                    else:
                        db.session.add(Accessory(name=row["product"][:120], sku=sku, price_kes=price, quantity=max(0, int(quantity))))
                        counts["accessories"] += 1
                    handled = True

            if row["amount"] or row["ref"]:
                amount = _import_number(row["amount"])
                reference = row["ref"][:128]
                if amount is None or amount < 0 or not reference:
                    warn(row_number, "payment needs a non-negative amount and a reference")
                elif Payment.query.filter_by(reference=reference).first():
                    counts["duplicates"] += 1
                    handled = True
                else:
                    payment = Payment(customer_id=customer.id if customer else None, amount_kes=amount, method=(row["method"] or "Imported")[:32], reference=reference)
                    payment_date = _import_datetime(row["date"])
                    if payment_date:
                        payment.created_at = payment_date
                    db.session.add(payment)
                    counts["payments"] += 1
                    handled = True

            event_date = _import_datetime(row["service_date"] or row["date"])
            should_create_event = bool(row["service_date"] or (row["date"] and row["event_title"]))
            if should_create_event:
                if event_date is None:
                    warn(row_number, "calendar event not created because its date could not be read")
                elif customer is None:
                    warn(row_number, "calendar event not created because its customer is missing")
                else:
                    title = (row["event_title"] or (f"Service due: {plate}" if plate else f"Service due: {customer.full_name}"))[:150]
                    if event_date.hour == 0 and event_date.minute == 0:
                        event_date = event_date.replace(hour=9)
                    existing_event = CalendarEvent.query.filter_by(customer_id=customer.id, title=title, starts_at=event_date).first()
                    if existing_event:
                        counts["duplicates"] += 1
                    else:
                        db.session.add(CalendarEvent(customer_id=customer.id, title=title, starts_at=event_date, ends_at=event_date + timedelta(hours=1), description=row["description"] or "Imported from spreadsheet"))
                        counts["events"] += 1
                    handled = True

            if not handled:
                counts["skipped"] += 1
                warn(row_number, "no supported record fields were mapped")

        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Spreadsheet import transaction failed")
        return jsonify({"error": "Import failed. No rows were committed; check the file values and try again."}), 500

    return jsonify({"ok": True, "counts": counts, "warnings": warnings}), 201


@api_bp.post("/leads/intake")
@login_required
def lead_intake():
    data = request.get_json() or {}
    customer = Customer(
        full_name=data.get("name", "Unknown"),
        phone=data.get("phone", "N/A"),
        email=data.get("email"),
        notes=data.get("request_type"),
    )
    db.session.add(customer)
    db.session.flush()
    event = CalendarEvent(
        customer_id=customer.id,
        title=f"Lead follow-up: {data.get('request_type', 'General request')}",
        starts_at=datetime.utcnow() + timedelta(hours=2),
        ends_at=datetime.utcnow() + timedelta(hours=3),
        description=f"Plate: {data.get('plate', 'N/A')}",
    )
    db.session.add(event)
    db.session.commit()
    return jsonify({"ok": True, "customer_id": customer.id, "event_id": event.id})
