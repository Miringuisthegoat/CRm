
import re

import africastalking
from flask import current_app

from app.extensions import db
from app.models import SMSLog
from app.services.utils import sms_parts_and_cost


def normalize_phone_number(phone: str) -> str:
    """Normalize a phone number to international format."""
    phone = re.sub(r"[\s\-()]", "", phone.strip())

    if not phone.startswith("+"):
        raise ValueError(
            "Phone number must include its country code, e.g. +254..."
        )

    if not re.fullmatch(r"\+\d{8,15}", phone):
        raise ValueError("Invalid international phone number format.")

    return phone


def send_sms(message: str, recipients: list[str]) -> SMSLog:
    normalized_recipients = [
        normalize_phone_number(phone) for phone in recipients
    ]

    parts, cost_kes = sms_parts_and_cost(
        message, len(normalized_recipients)
    )

    sms_log = SMSLog(
        recipients=len(normalized_recipients),
        message=message,
        parts=parts,
        cost_kes=cost_kes,
        status="queued",
    )

    db.session.add(sms_log)
    db.session.commit()

    try:
        username = current_app.config["AFRICASTALKING_USERNAME"]
        api_key = current_app.config["AFRICASTALKING_API_KEY"]

        africastalking.initialize(username, api_key)

        # Capture the actual response from Africa's Talking.
        response = africastalking.SMS.send(
            message=message,
            recipients=normalized_recipients,
        )

        sms_data = response.get("SMSMessageData", {})
        recipient_results = sms_data.get("Recipients", [])

        # Log provider feedback without logging phone numbers.
        current_app.logger.info(
            "Africa's Talking response for SMS log id=%s: "
            "message=%s, recipients=%s",
            sms_log.id,
            sms_data.get("Message", "No message provided"),
            [
                {
                    "status": item.get("status"),
                    "statusCode": item.get("statusCode"),
                    "messageId": item.get("messageId"),
                }
                for item in recipient_results
            ],
        )

        if not recipient_results:
            sms_log.status = "retry_pending"
            current_app.logger.warning(
                "No recipient results returned for SMS log id=%s",
                sms_log.id,
            )
        else:
            # 101 indicates acceptance for sending, not confirmed delivery.
            accepted = all(
                str(item.get("statusCode")) == "101"
                for item in recipient_results
            )

            sms_log.status = "sent" if accepted else "retry_pending"

    except Exception:
        current_app.logger.exception(
            "Africa's Talking SMS send failed (SMS log id=%s)",
            sms_log.id,
        )
        sms_log.status = "retry_pending"

    db.session.commit()
    return sms_log
