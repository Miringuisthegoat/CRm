import math
import re

KENYA_PLATE_PATTERN = re.compile(r"^[K][A-Z]{2}\s?\d{3}[A-Z]$")
EAST_AFRICA_PLATE_PATTERN = re.compile(r"^(KE|UG|TZ|RW|BI|DRC|ET|SS)-?[A-Z0-9]{4,8}$")
NETWORKS = {"Safaricom", "Airtel", "Telkom", "Faiba"}


def is_valid_plate(plate: str) -> bool:
    plate = plate.strip().upper()
    return bool(KENYA_PLATE_PATTERN.match(plate) or EAST_AFRICA_PLATE_PATTERN.match(plate))


def sms_parts_and_cost(message: str, recipients: int) -> tuple[int, float]:
    parts = max(1, math.ceil(len(message) / 160))
    return parts, parts * recipients * 0.80
