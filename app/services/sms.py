import africastalking
from flask import current_app

from app.extensions import db
from app.models import SMSLog
from app.services.utils import sms_parts_and_cost


def send_sms(message: str, recipients: list[str]) -> SMSLog:
    parts, cost_kes = sms_parts_and_cost(message, len(recipients))
    log = SMSLog(recipients=len(recipients), message=message, parts=parts, cost_kes=cost_kes, status="queued")
    db.session.add(log)
    db.session.commit()
    try:
        africastalking.initialize(
            current_app.config["AFRICASTALKING_USERNAME"],
            current_app.config["AFRICASTALKING_API_KEY"],
        )
        africastalking.SMS.send(message=message, recipients=recipients)
        log.status = "sent"
    except Exception:
        log.status = "retry_pending"
    db.session.commit()
    return log
