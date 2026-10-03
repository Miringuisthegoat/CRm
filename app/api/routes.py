from datetime import datetime, timedelta

from flask import Blueprint, current_app, jsonify, request

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
from app.models import CalendarEvent, Customer, FuelLog, Payment, Vehicle
from app.services.importer import fuzzy_map_columns
from app.services.sms import send_sms
from app.services.utils import NETWORKS, is_valid_plate

api_bp = Blueprint("api", __name__, url_prefix="/api")
web_bp = Blueprint("web", __name__)


@web_bp.get("/")
def index():
    return current_app.send_static_file("carace_dashboard.html")


@api_bp.get("/dashboard/kpis")
@login_required
def dashboard_kpis():
    return jsonify(
        {
            "currency": "KES",
            "vehicles": Vehicle.query.count(),
            "customers": Customer.query.count(),
            "fuel_spend_kes": round(db.session.query(db.func.coalesce(db.func.sum(FuelLog.cost_kes), 0)).scalar(), 2),
            "payments_kes": round(db.session.query(db.func.coalesce(db.func.sum(Payment.amount_kes), 0)).scalar(), 2),
        }
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
