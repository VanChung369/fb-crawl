from __future__ import annotations

import re
import unicodedata
from datetime import date
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from fb_crawl.adapters.http.contact_parser import extract_phone_numbers
from fb_crawl.core.models import ProfileDetails, ProfileField
from fb_crawl.core.urls import FACEBOOK_HOSTS


PERSONAL_HEADINGS = frozenset(
    {
        "basic info",
        "personal information",
        "thong tin ca nhan",
        "thong tin co ban",
    }
)
CONTACT_HEADINGS = frozenset(
    {
        "contact info",
        "contact and basic info",
        "thong tin lien he",
        "thong tin lien he va co ban",
    }
)
LINK_HEADINGS = frozenset(
    {
        "link",
        "links",
        "lien ket",
    }
)
ADDRESS_HEADINGS = frozenset(
    {
        "address",
        "dia chi",
    }
)
PHONE_HEADINGS = frozenset(
    {
        "mobile",
        "phone",
        "phone number",
        "dien thoai",
        "so dien thoai",
    }
)

CURRENT_CITY_PREFIXES = (
    "lives in ",
    "song o ",
)
HOMETOWN_PREFIXES = (
    "from ",
    "den tu ",
)
ADDRESS_PREFIXES = (
    "address:",
    "address ",
    "dia chi:",
    "dia chi ",
)

VIETNAMESE_DATE = re.compile(
    r"\b(?P<day>\d{1,2})\s+thang\s+(?P<month>\d{1,2})\s*,?\s*"
    r"(?P<year>\d{4})\b",
    re.IGNORECASE,
)
ENGLISH_DATE = re.compile(
    r"\b(?P<month>january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\s+"
    r"(?P<day>\d{1,2})\s*,?\s*(?P<year>\d{4})\b",
    re.IGNORECASE,
)
YEAR_ONLY = re.compile(r"\b(?:born|nam sinh|sinh nam)\D*(?P<year>\d{4})\b", re.I)

MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}


def _visible_text(node) -> str:
    return " ".join(node.stripped_strings).strip()


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return " ".join(without_marks.casefold().replace("đ", "d").split())


def _requested(
    requested_fields: tuple[ProfileField, ...],
) -> frozenset[ProfileField]:
    return (
        frozenset(requested_fields)
        if requested_fields
        else frozenset(ProfileField)
    )


def _section_name(container, soup: BeautifulSoup) -> str:
    labelled_by = str(container.get("aria-labelledby") or "").strip()

    if labelled_by:
        heading = soup.find(id=labelled_by)
        if heading is not None:
            return _fold(_visible_text(heading))

    heading = container.find_previous(("h1", "h2", "h3", "h4"))
    return _fold(_visible_text(heading)) if heading is not None else ""


def _value_after_prefix(value: str, prefixes: tuple[str, ...]) -> str | None:
    folded = _fold(value)

    for prefix in prefixes:
        if folded.startswith(prefix):
            words = value.split()
            prefix_word_count = len(prefix.split())
            result = " ".join(words[prefix_word_count:]).strip(" :-")
            return result or None

    return None


def _birthday(value: str) -> tuple[str | None, int | None]:
    folded = _fold(value)
    match = VIETNAMESE_DATE.search(folded)

    if match is not None:
        month = int(match.group("month"))
    else:
        match = ENGLISH_DATE.search(folded)
        if match is None:
            year_match = YEAR_ONLY.search(folded)
            if year_match is None:
                return None, None
            year = int(year_match.group("year"))
            return (None, year) if 1900 <= year <= date.today().year else (None, None)

        month = MONTHS[match.group("month").casefold()]

    year = int(match.group("year"))
    day = int(match.group("day"))

    if not 1900 <= year <= date.today().year:
        return None, None

    try:
        parsed = date(year, month, day)
    except ValueError:
        return None, None

    return parsed.isoformat(), parsed.year


def _external_website(value: str) -> str | None:
    parsed = urlparse(value)
    host = parsed.netloc.casefold().split(":")[0]

    if parsed.scheme not in {"http", "https"} or not host:
        return None

    if host in FACEBOOK_HOSTS:
        return None

    query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if key.casefold() not in {"fbclid", "__tn__"}
        ]
    )
    return urlunparse(parsed._replace(query=query, fragment=""))


class ProfileParser:
    def parse(
        self,
        html: str,
        *,
        source_url: str,
        requested_fields: tuple[ProfileField, ...] = (),
    ) -> ProfileDetails:
        soup = BeautifulSoup(html, "html.parser")
        requested = _requested(requested_fields)

        phones: dict[str, str] = {}
        website: str | None = None
        address: str | None = None
        current_city: str | None = None
        hometown: str | None = None
        birth_date: str | None = None
        birth_year: int | None = None

        if ProfileField.PHONE in requested:
            for anchor in soup.find_all("a", href=True):
                href = str(anchor.get("href") or "")
                if href.casefold().startswith("tel:"):
                    for phone in extract_phone_numbers(unquote(href[4:])):
                        phones.setdefault(re.sub(r"\D", "", phone), phone)

        for container in soup.find_all(attrs={"role": "list"}):
            section = _section_name(container, soup)
            is_personal = section in PERSONAL_HEADINGS
            is_contact = section in CONTACT_HEADINGS
            is_link = section in LINK_HEADINGS
            is_address = section in ADDRESS_HEADINGS
            is_phone = section in PHONE_HEADINGS

            if not any((is_personal, is_contact, is_link, is_address, is_phone)):
                continue

            items = container.find_all(attrs={"role": "listitem"})

            for item in items:
                text = _visible_text(item)

                if is_personal:
                    if ProfileField.CURRENT_CITY in requested and current_city is None:
                        current_city = _value_after_prefix(text, CURRENT_CITY_PREFIXES)

                    if ProfileField.HOMETOWN in requested and hometown is None:
                        hometown = _value_after_prefix(text, HOMETOWN_PREFIXES)

                    if ProfileField.BIRTH_DATE in requested and birth_year is None:
                        birth_date, birth_year = _birthday(text)

                if is_contact or is_phone:
                    if ProfileField.PHONE in requested:
                        for phone in extract_phone_numbers(text):
                            phones.setdefault(re.sub(r"\D", "", phone), phone)

                if is_contact:
                    if ProfileField.ADDRESS in requested and address is None:
                        address = _value_after_prefix(text, ADDRESS_PREFIXES)

                if (
                    is_address
                    and ProfileField.ADDRESS in requested
                    and address is None
                ):
                    address = text or None

                if (
                    (is_contact or is_link)
                    and ProfileField.WEBSITE in requested
                    and website is None
                ):
                    for anchor in item.find_all("a", href=True):
                        website = _external_website(str(anchor.get("href") or ""))
                        if website is not None:
                            break

        phone_values = tuple(phones.values())
        return ProfileDetails(
            phone_numbers=phone_values,
            phone_sources=("facebook:profile_contact",) if phone_values else (),
            website=website,
            address=address,
            current_city=current_city,
            hometown=hometown,
            birth_date=birth_date,
            birth_year=birth_year,
        )
