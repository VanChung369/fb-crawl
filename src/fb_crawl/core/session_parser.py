"""Utility to parse cookies from raw string, JSON, or multi-pipe Facebook account formats."""

from __future__ import annotations

import json
from typing import Any


def parse_cookie_input(raw_input: str | list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Parse cookie input into (account_name_or_uid, list_of_cookie_dicts).

    Supports:
    1. JSON array string or list of dicts.
    2. Raw cookie string: 'c_user=...;xs=...;datr=...;fr=...'
    3. Multi-pipe format: 'UID|Pass|2FA|Cookie|Token|Email...'
    """
    if isinstance(raw_input, list):
        uid = None
        for c in raw_input:
            if isinstance(c, dict) and c.get("name") == "c_user":
                uid = str(c.get("value"))
        return uid, raw_input

    if not isinstance(raw_input, str):
        return None, []

    text = raw_input.strip()
    if not text:
        return None, []

    # 1. Try parsing as JSON array
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                uid = None
                for c in parsed:
                    if isinstance(c, dict) and c.get("name") == "c_user":
                        uid = str(c.get("value"))
                return uid, parsed
        except Exception:
            pass

    # 2. Check for multi-pipe format: UID|Pass|2FA|Cookie|Token...
    inferred_uid = None
    cookie_str = text
    if "|" in text:
        parts = [p.strip() for p in text.split("|")]
        if len(parts) >= 2 and parts[0].isdigit() and len(parts[0]) >= 8:
            inferred_uid = parts[0]
        # Search for part containing c_user=
        for part in parts:
            if "c_user=" in part or "xs=" in part:
                cookie_str = part
                break

    # 3. Parse semicolon separated name=value pairs
    cookies: list[dict[str, Any]] = []
    for chunk in cookie_str.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        name, val = chunk.split("=", 1)
        name = name.strip()
        val = val.strip()
        if not name or not val:
            continue
        if name == "c_user":
            inferred_uid = val

        cookies.append({
            "name": name,
            "value": val,
            "domain": ".facebook.com",
            "path": "/",
            "secure": True,
            "httpOnly": name in {"xs", "datr", "fr", "sb"},
        })

    return inferred_uid, cookies
