from __future__ import annotations

import re


class InvalidPhoneNumber(ValueError):
    pass


def normalize_phone(value: str, *, default_country_code: str = "84") -> str:
    """Return a conservative E.164 key while keeping all values as text."""
    raw = str(value or "").strip()
    country_code = re.sub(r"\D", "", default_country_code)
    digits = re.sub(r"\D", "", raw)

    if not digits or not country_code:
        raise InvalidPhoneNumber("Phone number or default country code is empty.")

    if raw.startswith("+"):
        normalized_digits = digits
    elif digits.startswith("00"):
        normalized_digits = digits[2:]
    elif digits.startswith("0"):
        normalized_digits = country_code + digits[1:]
    elif digits.startswith(country_code):
        normalized_digits = digits
    else:
        normalized_digits = country_code + digits

    if not 8 <= len(normalized_digits) <= 15:
        raise InvalidPhoneNumber("Phone number must contain 8 to 15 digits.")

    return f"+{normalized_digits}"

