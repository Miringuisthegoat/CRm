import hashlib
import time

import requests
from flask import current_app

_token = {"value": None, "exp": 0.0}


def _call(action, body=None, token=None, extra=None):
    base = current_app.config["GPS51_BASE_URL"].rstrip("/")
    params = {"action": action}
    if token:
        params["token"] = token
    params.update(extra or {})
    r = requests.post(f"{base}/openapi", params=params, json=body or {}, timeout=15)
    r.raise_for_status()
    return r.json()


def _login():
    cfg = current_app.config
    pw = hashlib.md5(cfg["GPS51_PASSWORD"].encode()).hexdigest()
    data = _call("login", {
        "username": cfg["GPS51_USERNAME"],
        "password": pw,
        "from": "WEB",
        "type": "USER",
    })
    if data.get("status") != 0:
        raise RuntimeError(data.get("cause") or "GPS51 login failed")
    _token.update(value=data["token"], exp=time.time() + 3300)


def _authed(action, body=None, extra=None):
    if not _token["value"] or time.time() > _token["exp"]:
        _login()
    data = _call(action, body, _token["value"], extra)
    if data.get("status") != 0:  # token may have expired; retry once
        _login()
        data = _call(action, body, _token["value"], extra)
    return data


def list_devices():
    data = _authed(
        "querymonitorlist",
        extra={"username": current_app.config["GPS51_USERNAME"]},
    )
    devices = []
    for group in data.get("groups", []):
        devices.extend(group.get("devices", []))
    return devices


def _normalize(rec, names):
    return {
        "deviceId": rec.get("deviceid"),
        "name": names.get(rec.get("deviceid"), rec.get("deviceid")),
        "latitude": rec.get("callat"),
        "longitude": rec.get("callon"),
        "speed": rec.get("speed"),  # verify units (m/h vs km/h) in GPS51 docs
        "status": rec.get("strstatus"),
        "fixTime": rec.get("updatetime"),
    }


def last_positions():
    devices = list_devices()
    names = {d["deviceid"]: d.get("devicename") for d in devices}
    ids = list(names.keys())
    if not ids:
        return []
    data = _authed("lastposition", {"deviceids": ids, "lastquerypositiontime": ""})
    return [_normalize(r, names) for r in data.get("records", [])]


def track_history(device_id, begin, end):
    """begin/end: 'YYYY-MM-DD HH:MM:SS'"""
    data = _authed("querytracks", {
        "deviceid": device_id,
        "begintime": begin,
        "endtime": end,
        "timezone": 3,
    })
    return data.get("records", [])