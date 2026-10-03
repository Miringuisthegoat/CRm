from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import db
from app.models import User
from flask import current_app
from app.extensions import csrf, limiter
from app.services.firebase_auth import delete_firebase_user, mark_email_verified, verify_id_token
from app.services.otp import check_code, issue_code

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@auth_bp.post("/login")
def login():
    data = request.get_json() or {}
    user = User.query.filter_by(email=data.get("email", "").strip().lower()).first()
    if not user or not user.check_password(data.get("password", "")):
        return jsonify({"error": "Invalid credentials"}), 401
    login_user(user)
    return jsonify({"ok": True, "user": {"id": user.id, "email": user.email, "role": user.role}})


def _user_json(u):
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role}


def _claims(data):
    try:
        return verify_id_token(data.get("idToken", ""))
    except Exception:
        return None


@auth_bp.post("/firebase/register")
@csrf.exempt  # authenticated by Firebase ID token, not cookie
@limiter.limit("10/hour")
def fb_register():
    data = request.get_json() or {}
    claims = _claims(data)
    if not claims:
        return jsonify({"error": "Invalid session. Try again."}), 401
    email = (claims.get("email") or "").lower()
    domain = current_app.config["SIGNUP_EMAIL_DOMAIN"]
    if domain and not email.endswith("@" + domain):
        delete_firebase_user(claims["uid"])
        return jsonify({"error": f"Only @{domain} emails can create an account."}), 403
    user = User.query.filter_by(email=email).first()
    if user and user.email_verified:
        return jsonify({"error": "Account already exists. Sign in instead."}), 409
    if not user:
        user = User(
            name=(data.get("name") or email.split("@")[0]).strip()[:120],
            email=email,
            phone=("+254" + str(data.get("phone", "")).replace(" ", ""))[:32] if data.get("phone") else None,
            role="staff",  # never from the client
        )
        db.session.add(user)
    user.firebase_uid = claims["uid"]
    db.session.commit()
    issue_code(user)
    return jsonify({"ok": True}), 201


@auth_bp.post("/firebase/resend")
@csrf.exempt
@limiter.limit("10/hour")
def fb_resend():
    claims = _claims(request.get_json() or {})
    user = User.query.filter_by(firebase_uid=claims["uid"]).first() if claims else None
    if not user or user.email_verified:
        return jsonify({"error": "Nothing to verify."}), 400
    err = issue_code(user, enforce_cooldown=True)
    if err:
        return jsonify({"error": err}), 429
    return jsonify({"ok": True})


@auth_bp.post("/firebase/verify")
@csrf.exempt
@limiter.limit("20/hour")
def fb_verify():
    data = request.get_json() or {}
    claims = _claims(data)
    user = User.query.filter_by(firebase_uid=claims["uid"]).first() if claims else None
    if not user:
        return jsonify({"error": "Invalid session."}), 401
    err = check_code(user, str(data.get("code", "")).strip())
    if err:
        return jsonify({"error": err}), 400
    user.email_verified = True
    db.session.commit()
    mark_email_verified(user.firebase_uid)
    login_user(user)
    return jsonify({"ok": True, "user": _user_json(user)})


@auth_bp.post("/firebase/login")
@csrf.exempt
@limiter.limit("20/minute")
def fb_login():
    claims = _claims(request.get_json() or {})
    user = User.query.filter_by(firebase_uid=claims["uid"]).first() if claims else None
    if not user:
        return jsonify({"error": "No Carace account for this login."}), 403
    if not user.email_verified:
        return jsonify({"error": "Email not verified.", "code": "unverified"}), 403
    login_user(user)
    return jsonify({"ok": True, "user": _user_json(user)})

@auth_bp.post("/logout")
@login_required
def logout():
    logout_user()
    return jsonify({"ok": True})


@auth_bp.get("/me")
@login_required
def me():
    return jsonify({"id": current_user.id, "name": current_user.name, "email": current_user.email, "role": current_user.role})
