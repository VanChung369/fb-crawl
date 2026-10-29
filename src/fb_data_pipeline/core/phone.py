from __future__ import annotations

import re


class InvalidPhoneNumber(ValueError):
    pass


def normalize_phone(value: str, *, default_country_code: str = "84") -> str:
    """Return a conservative E.164 key while keeping all values as text."""
    raw = str(value or "").strip()
    # Only discard formatting, never redaction markers or letters. Otherwise
    # e.g. 0966777* becomes an invented (and previously accepted) +84966777.
    if re.fullmatch(r"\+?[0-9\s().-]+", raw) is None or re.search(r"\.{2,}", raw):
        raise InvalidPhoneNumber("Phone number contains masked or invalid characters.")
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

    if normalized_digits.startswith("84"):
        national = normalized_digits[2:]
        lengths = (9, 10)
        if national.startswith("2"):
            lengths = (10,)
        elif national.startswith(("3", "5", "7", "8", "9")):
            lengths = (9,)
        if len(national) not in lengths:
            raise InvalidPhoneNumber("Vietnamese phone number is incomplete or invalid.")

    return f"+{normalized_digits}"

