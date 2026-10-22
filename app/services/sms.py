
import re

import africastalking
from flask import current_app

from app.extensions import db
from app.models import SMSLog
from app.services.utils import sms_parts_and_cost


ACCEPTED_WORDS = {"success", "sent", "processed", "queued"}
ACCEPTED_CODES = {"100", "101", "102"}  # backup if the word is ever missing

FAILURE_REASONS = {
    "InsufficientBalance": "Africa's Talking balance is too low",
    "InvalidPhoneNumber": "invalid phone number",
    "UserInBlacklist": "recipient has blocked SMS",
    "InvalidSenderId": "sender ID not approved",
    "RiskHold": "held by Africa's Talking for review",
    "UnsupportedNumberType": "phone number type not supported",
    "CouldNotRoute": "network could not route the message",
    "RejectedByGateway": "rejected by the mobile network",
}


def _accepted(item: dict) -> bool:
    return (
        str(item.get("status", "")).lower() in ACCEPTED_WORDS
        or str(item.get("statusCode")) in ACCEPTED_CODES
    )

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

    sms_log.reason = None  # plain-English failure reason, not stored in the database

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
            # 100/101/102 mean accepted by Africa's Talking, not confirmed delivery.
            accepted = all(_accepted(item) for item in recipient_results)
            sms_log.status = "sent" if accepted else "retry_pending"
            if not accepted:
                failed = next(i for i in recipient_results if not _accepted(i))
                word = str(failed.get("status") or "Unknown")
                sms_log.reason = FAILURE_REASONS.get(word, word)

    except Exception:
        current_app.logger.exception(
            "Africa's Talking SMS send failed (SMS log id=%s)",
            sms_log.id,
        )
        sms_log.status = "retry_pending"
        sms_log.reason = "could not reach Africa's Talking"

    db.session.commit()
    return sms_log
