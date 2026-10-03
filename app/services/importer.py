from rapidfuzz import fuzz

EXPECTED_COLUMNS = ["full_name", "email", "phone", "plate_number", "request_type"]


def fuzzy_map_columns(columns: list[str]) -> dict[str, str]:
    mapping = {}
    for expected in EXPECTED_COLUMNS:
        best = max(columns, key=lambda c: fuzz.ratio(expected.lower(), c.lower()), default="")
        if best and fuzz.ratio(expected.lower(), best.lower()) > 60:
            mapping[expected] = best
    return mapping
