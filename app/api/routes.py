from datetime import datetime, timedelta
import math
import re

from app.models import Accessory, CalendarEvent, Customer, Payment, ReminderDelivery, SIMCard, Tracker, Vehicle
from datetime import datetime, timedelta
from flask import current_app
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
        .filter(CalendarEvent.starts_at >= today, CalendarEvent.starts_at < upcoming_until,
                CalendarEvent.event_type.in_(("general", "expiry")))
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
                "event_type": event.event_type,
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



@api_bp.post("/customers")
@login_required
def create_customer():
    data = request.get_json(silent=True) or {}

    full_name = str(data.get("full_name", "")).strip()
    phone = str(data.get("phone", "")).strip() or "N/A"
    email = str(data.get("email", "")).strip().lower()
    notes = str(data.get("notes", "")).strip()

    if not full_name or len(full_name) > 120:
        return jsonify({"error": "Enter a valid customer name."}), 400
    if len(phone) > 32 or len(email) > 190 or len(notes) > 500:
        return jsonify({"error": "One or more fields are too long."}), 400

    customer = Customer(
        full_name=full_name,
        phone=phone,
        email=email or None,
        notes=notes or None,
    )

    try:
        db.session.add(customer)
        db.session.commit()
        return jsonify({"ok": True, "id": customer.id}), 201
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Customer creation failed")
        return jsonify({"error": "Could not create customer."}), 500


@api_bp.post("/accessories")
@login_required
def create_accessory():
    data = request.get_json(silent=True) or {}

    name = str(data.get("name", "")).strip()
    sku = str(data.get("sku", "")).strip().upper()

    if not name or len(name) > 120:
        return jsonify({"error": "Enter a valid product name."}), 400
    if not sku or len(sku) > 64:
        return jsonify({"error": "Enter a valid SKU."}), 400

    try:
        price = float(data.get("price_kes"))
        quantity = int(data.get("quantity"))
    except (TypeError, ValueError):
        return jsonify({"error": "Price and stock must be valid numbers."}), 400

    if not math.isfinite(price) or price < 0:
        return jsonify({"error": "Price must be non-negative."}), 400
    if quantity < 0:
        return jsonify({"error": "Stock cannot be negative."}), 400

    if Accessory.query.filter_by(sku=sku).first():
        return jsonify({"error": "That SKU already exists."}), 409

    accessory = Accessory(
        name=name,
        sku=sku,
        price_kes=price,
        quantity=quantity,
    )

    try:
        db.session.add(accessory)
        db.session.commit()
        return jsonify({"ok": True, "id": accessory.id}), 201
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Accessory creation failed")
        return jsonify({"error": "Could not create accessory."}), 500

@api_bp.patch("/customers/<int:record_id>")
@login_required
def update_customer(record_id):
    data = request.get_json(silent=True) or {}
    allowed = {"full_name", "notes", "email", "phone"}

    if not data or set(data) - allowed:
        return jsonify({"error": "Invalid customer fields."}), 400

    record = db.session.get(Customer, record_id)
    if record is None:
        return jsonify({"error": "Customer not found."}), 404

    if "full_name" in data:
        value = str(data["full_name"]).strip()
        if not value or len(value) > 120:
            return jsonify({"error": "Enter a valid customer name."}), 400
        record.full_name = value

    if "phone" in data:
        value = str(data["phone"]).strip()
        if not value or len(value) > 32:
            return jsonify({"error": "Enter a valid phone number."}), 400
        record.phone = value

    if "email" in data:
        value = str(data["email"]).strip()
        if len(value) > 190:
            return jsonify({"error": "Email is too long."}), 400
        record.email = value or None

    if "notes" in data:
        record.notes = str(data["notes"]).strip() or None

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Customer update failed")
        return jsonify({"error": "Could not save customer changes."}), 500

    return jsonify({"ok": True, "id": record.id})


# Update an existing vehicle
@api_bp.patch("/vehicles/<int:record_id>")
@login_required
def update_vehicle(record_id):
    data = request.get_json(silent=True) or {}
    allowed = {"plate_number", "make", "model", "network"}

    if not data or set(data) - allowed:
        return jsonify({"error": "Invalid vehicle fields."}), 400

    record = db.session.get(Vehicle, record_id)
    if record is None:
        return jsonify({"error": "Vehicle not found."}), 404

    if "plate_number" in data:
        plate = str(data["plate_number"]).strip().upper()
        if not is_valid_plate(plate):
            return jsonify({"error": "Invalid vehicle plate."}), 400
        record.plate_number = plate

    for field in ("make", "model"):
        if field in data:
            value = str(data[field]).strip()
            if len(value) > 64:
                return jsonify({"error": f"{field} is too long."}), 400
            setattr(record, field, value or None)

    if "network" in data:
        network = str(data["network"]).strip()
        if network not in NETWORKS:
            return jsonify({"error": "Unsupported network."}), 400
        record.network = network

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Vehicle update failed")
        return jsonify({"error": "Could not save vehicle changes."}), 500

    return jsonify({"ok": True, "id": record.id})


# Update an accessory
@api_bp.patch("/accessories/<int:record_id>")
@login_required
def update_accessory(record_id):
    data = request.get_json(silent=True) or {}
    allowed = {"name", "price_kes", "quantity"}

    if not data or set(data) - allowed:
        return jsonify({"error": "Invalid accessory fields."}), 400

    record = db.session.get(Accessory, record_id)
    if record is None:
        return jsonify({"error": "Accessory not found."}), 404

    if "name" in data:
        name = str(data["name"]).strip()
        if not name or len(name) > 120:
            return jsonify({"error": "Enter a valid product name."}), 400
        record.name = name

    if "price_kes" in data:
        try:
            price = float(data["price_kes"])
        except (TypeError, ValueError):
            return jsonify({"error": "Price must be numeric."}), 400
        if not math.isfinite(price) or price < 0:
            return jsonify({"error": "Price must be non-negative."}), 400
        record.price_kes = price

    if "quantity" in data:
        try:
            quantity = int(data["quantity"])
        except (TypeError, ValueError):
            return jsonify({"error": "Stock must be a whole number."}), 400
        if quantity < 0:
            return jsonify({"error": "Stock cannot be negative."}), 400
        record.quantity = quantity

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Accessory update failed")
        return jsonify({"error": "Could not save accessory changes."}), 500

    return jsonify({"ok": True, "id": record.id})


# Update a tracker
@api_bp.patch("/trackers/<int:record_id>")
@login_required
def update_tracker(record_id):
    data = request.get_json(silent=True) or {}
    allowed = {"imei", "status"}

    if not data or set(data) - allowed:
        return jsonify({"error": "Invalid tracker fields."}), 400

    record = db.session.get(Tracker, record_id)
    if record is None:
        return jsonify({"error": "Tracker not found."}), 404

    if "imei" in data:
        imei = str(data["imei"]).strip()
        if not imei or len(imei) > 64:
            return jsonify({"error": "Enter a valid IMEI."}), 400

        duplicate = Tracker.query.filter(
            Tracker.imei == imei,
            Tracker.id != record.id,
        ).first()

        if duplicate:
            return jsonify({"error": "That IMEI is already registered."}), 409

        record.imei = imei

    if "status" in data:
        status = str(data["status"]).strip()
        if not status or len(status) > 32:
            return jsonify({"error": "Enter a valid tracker status."}), 400
        record.status = status

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Tracker update failed")
        return jsonify({"error": "Could not save tracker changes."}), 500

    return jsonify({"ok": True, "id": record.id})


# Update a SIM card
@api_bp.patch("/sims/<int:record_id>")
@login_required
def update_sim(record_id):
    data = request.get_json(silent=True) or {}
    allowed = {"phone_number", "iccid", "provider"}

    if not data or set(data) - allowed:
        return jsonify({"error": "Invalid SIM fields."}), 400

    record = db.session.get(SIMCard, record_id)
    if record is None:
        return jsonify({"error": "SIM card not found."}), 404

    if "phone_number" in data:
        phone = str(data["phone_number"]).strip()
        if not phone or len(phone) > 32:
            return jsonify({"error": "Enter a valid SIM number."}), 400
        record.phone_number = phone

    if "iccid" in data:
        iccid = str(data["iccid"]).strip()
        if not iccid or len(iccid) > 64:
            return jsonify({"error": "Enter a valid ICCID."}), 400

        duplicate = SIMCard.query.filter(
            SIMCard.iccid == iccid,
            SIMCard.id != record.id,
        ).first()

        if duplicate:
            return jsonify({"error": "That ICCID is already registered."}), 409

        record.iccid = iccid

    if "provider" in data:
        provider = str(data["provider"]).strip()
        if not provider or len(provider) > 32:
            return jsonify({"error": "Enter a valid SIM provider."}), 400
        record.provider = provider

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("SIM update failed")
        return jsonify({"error": "Could not save SIM changes."}), 500

    return jsonify({"ok": True, "id": record.id})




@api_bp.post("/sms/send")
@login_required
def sms_send():
    data = request.get_json(silent=True) or {}
    messages = data.get("messages")
    message = str(data.get("message", "")).strip()
    recipients = data.get("recipients", [])

    if not message or len(message) > 5000:
        return jsonify({"error": "Enter a valid SMS message."}), 400

    if not isinstance(recipients, list) or not recipients:
        return jsonify({"error": "Select at least one recipient."}), 400

    if len(recipients) > 500:
        return jsonify({"error": "Send a maximum of 500 recipients per request."}), 400

    if not all(isinstance(phone, str) and phone.strip() for phone in recipients):
        return jsonify({"error": "One or more recipient phone numbers are invalid."}), 400

    # Use one provider request per message when messages are personalized.
    if messages is not None:
        if not isinstance(messages, list) or len(messages) != len(recipients):
            return jsonify({"error": "Personalized messages do not match the recipients."}), 400

        # Prevent accidentally sending to a different list than the supplied messages.
        for item, phone in zip(messages, recipients):
            if not isinstance(item, dict) or item.get("phone", "").strip() != phone:
                return jsonify({"error": "Recipient/message mismatch."}), 400
            if not isinstance(item.get("message"), str) or not item["message"].strip():
                return jsonify({"error": "A personalized message is empty."}), 400

        # The existing service accepts one shared message. Send each personalized
        # message individually through that service and return all log statuses.
        results = []
        for item in messages:
            log = send_sms(item["message"].strip(), [item["phone"].strip()])
            results.append({
                "id": log.id,
                "status": log.status,
                "parts": log.parts,
                "cost_kes": log.cost_kes,
            })

        all_sent = all(item["status"] == "sent" for item in results)
        return jsonify({
            "status": "sent" if all_sent else "retry_pending",
            "results": results,
            "sent": sum(item["status"] == "sent" for item in results),
            "total": len(results),
        })

    log = send_sms(message, recipients)
    return jsonify({
        "id": log.id,
        "status": log.status,
        "parts": log.parts,
        "cost_kes": log.cost_kes,
    })


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

    return jsonify({
        "customers": [
            {
                "id": customer.id,
                "name": customer.full_name,
                "company": customer.notes or "",
                "email": customer.email or "",
                "phone": customer.phone or "",
                "type": (
                    "Fleet client" if customer.vehicles
                    else "Individual"
                ),
                "vehicles": str(len(customer.vehicles)),
                "status": "Registered",
                "plate": (
                    customer.vehicles[0].plate_number
                    if customer.vehicles else "—"
                ),
            }
            for customer in customers
        ],

        "vehicles": [
            {
                "id": vehicle.id,
                "plate": vehicle.plate_number,
                "owner": (
                    vehicle.customer.full_name
                    if vehicle.customer else ""
                ),
                "make": vehicle.make or "",
                "model": vehicle.model or "",
                "network": vehicle.network or "Safaricom",
                "type": "Not recorded",
                "mileage": "Not recorded",
                "status": "Registered",
                "tracker": (
                    "Assigned" if vehicle.trackers
                    else "Not assigned"
                ),
                "service": "Not recorded",
                "customer_id": vehicle.customer_id,
            }
            for vehicle in vehicles
        ],

        "accessories": [
            {
                "id": accessory.id,
                "product": accessory.name,
                "price": str(accessory.price_kes),
                "stock": accessory.quantity,
                "status": (
                    "ok" if accessory.quantity > 0 else "out"
                ),
            }
            for accessory in accessories
        ],

        "trackers": [
            {
                "id": tracker.id,
                "vehicle_id": tracker.vehicle_id,
                "imei": tracker.imei,
                "status": tracker.status,
                "vehicle": (
                    tracker.vehicle.plate_number
                    if tracker.vehicle else ""
                ),
            }
            for tracker in trackers
        ],

        "sims": [
            {
                "id": sim.id,
                "tracker_id": sim.tracker_id,
                "sim": sim.phone_number,
                "phone_number": sim.phone_number,
                "iccid": sim.iccid or "",
                "network": sim.provider or "",
                "provider": sim.provider or "",
                "tracker": (
                    sim.tracker.imei if sim.tracker else ""
                ),
            }
            for sim in sims
        ],
    })

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

    
# ============ DELETE RECORDS ============

@api_bp.delete("/customers/<int:customer_id>")
@login_required
def delete_customer(customer_id):
    customer = db.session.get(Customer, customer_id)

    if customer is None:
        return jsonify({"error": "Customer not found."}), 404

    # Protect records linked to vehicles, payments, or calendar events.
    has_vehicles = Vehicle.query.filter_by(customer_id=customer_id).first()
    has_payments = Payment.query.filter_by(customer_id=customer_id).first()
    has_events = CalendarEvent.query.filter(
        CalendarEvent.customer_id == customer_id,
        CalendarEvent.event_type.in_(("general", "expiry")),
    ).first()

    if has_vehicles or has_payments or has_events:
        return jsonify({
            "error": (
                "This customer has linked vehicles, payments, or calendar "
                "events. Remove or reassign those records first."
            )
        }), 409

    try:
        db.session.delete(customer)
        db.session.commit()
        return jsonify({"ok": True, "message": "Customer deleted."})
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Customer deletion failed")
        return jsonify({"error": "Could not delete customer."}), 500


@api_bp.delete("/accessories/<int:accessory_id>")
@login_required
def delete_accessory(accessory_id):
    accessory = db.session.get(Accessory, accessory_id)

    if accessory is None:
        return jsonify({"error": "Accessory not found."}), 404

    try:
        db.session.delete(accessory)
        db.session.commit()
        return jsonify({"ok": True, "message": "Accessory deleted."})
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Accessory deletion failed")
        return jsonify({"error": "Could not delete accessory."}), 500


@api_bp.delete("/sims/<int:sim_id>")
@login_required
def delete_sim(sim_id):
    sim = db.session.get(SIMCard, sim_id)

    if sim is None:
        return jsonify({"error": "SIM card not found."}), 404

    try:
        db.session.delete(sim)
        db.session.commit()
        return jsonify({"ok": True, "message": "SIM card deleted."})
    except Exception:
        db.session.rollback()
        current_app.logger.exception("SIM card deletion failed")
        return jsonify({
            "error": "Could not delete SIM card. Check whether it is referenced elsewhere."
        }), 500


# ============ CREATE CALENDAR EVENTS ============

@api_bp.post("/calendar/events")
@login_required
def create_calendar_event():
    data = request.get_json(silent=True) or {}

    title = str(data.get("title", "")).strip()
    date_value = str(data.get("date", "")).strip()
    time_value = str(data.get("time", "09:00")).strip()
    description = str(data.get("description", "")).strip()
    customer_id = data.get("customer_id")

    if not title:
        return jsonify({"error": "Enter an event title."}), 400

    if len(title) > 150:
        return jsonify({"error": "Event title must be 150 characters or fewer."}), 400

    try:
        starts_at = datetime.strptime(
            f"{date_value} {time_value}", "%Y-%m-%d %H:%M"
        )
    except ValueError:
        return jsonify({"error": "Enter a valid event date and time."}), 400

    customer = None
    if customer_id not in (None, ""):
        try:
            customer_id = int(customer_id)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid customer selection."}), 400

        customer = db.session.get(Customer, customer_id)
        if customer is None:
            return jsonify({"error": "Selected customer was not found."}), 404

    event_type = str(data.get("event_type", "general")).strip()
    if event_type not in ("general", "expiry"):
        return jsonify({"error": "Invalid event type."}), 400
    vehicle = None
    if event_type == "expiry":
        if customer is None:
            return jsonify({"error": "Select a customer for an expiry event."}), 400
        if data.get("vehicle_id") not in (None, ""):
            try:
                vehicle = db.session.get(Vehicle, int(data["vehicle_id"]))
            except (TypeError, ValueError):
                vehicle = None
            if vehicle is None or vehicle.customer_id != customer.id:
                return jsonify({"error": "Selected vehicle does not belong to this customer."}), 400

    event = CalendarEvent(
        title=title,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        description=description or None,
        customer_id=customer.id if customer else None,
        vehicle_id=vehicle.id if vehicle else None,
        event_type=event_type,
        reminders_enabled=(event_type == "expiry" and data.get("reminders", True) is not False),
    )

    try:
        db.session.add(event)
        db.session.commit()
        return jsonify({
            "ok": True,
            "id": event.id,
            "title": event.title,
            "starts_at": event.starts_at.isoformat(),
        }), 201
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Calendar event creation failed")
        return jsonify({
            "error": "Could not save event. Check the database model and required fields."
        }), 500

@api_bp.get("/reminders")
@login_required
def reminders_list():
    rows = (db.session.query(ReminderDelivery, CalendarEvent.title, Customer.full_name)
            .join(CalendarEvent, ReminderDelivery.event_id == CalendarEvent.id)
            .outerjoin(Customer, CalendarEvent.customer_id == Customer.id)
            .order_by(ReminderDelivery.send_at.desc()).limit(300).all())
    return jsonify([{
        "id": d.id, "event": title, "customer": name, "channel": d.channel,
        "offset_days": d.offset_days, "send_at": d.send_at.isoformat(),
        "status": d.status, "attempts": d.attempts, "error": d.last_error,
    } for d, title, name in rows])