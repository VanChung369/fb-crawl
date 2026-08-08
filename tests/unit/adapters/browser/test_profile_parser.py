from pathlib import Path

import pytest

from fb_crawl.adapters.browser.profile_parser import ProfileParser
from fb_crawl.core.models import ProfileDetails, ProfileField


FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "authenticated"


def test_vietnamese_personal_info_parses_location_and_birthday_only() -> None:
    details = ProfileParser().parse(
        (FIXTURES / "profile_about_vi.html").read_text(encoding="utf-8"),
        source_url="https://www.facebook.com/synthetic.user/about",
    )

    assert details.current_city == "Thành phố Ví Dụ"
    assert details.hometown == "Tỉnh Ví Dụ"
    assert details.birth_date == "1990-01-02"
    assert details.birth_year == 1990
    assert details.phone_numbers == ()


def test_english_contact_info_parses_deduplicated_contact_values() -> None:
    details = ProfileParser().parse(
        (FIXTURES / "profile_contact_en.html").read_text(encoding="utf-8"),
        source_url=(
            "https://www.facebook.com/synthetic.user"
            "/about_contact_and_basic_info"
        ),
    )

    assert len(details.phone_numbers) == 1
    assert details.phone_sources == ("facebook:profile_contact",)
    assert details.website == "https://profile.example.test/about"
    assert details.address == "123 Synthetic Street"
    assert details.current_city is None


def test_requested_fields_filter_unselected_personal_data() -> None:
    details = ProfileParser().parse(
        (FIXTURES / "profile_about_vi.html").read_text(encoding="utf-8"),
        source_url="https://www.facebook.com/synthetic.user/about",
        requested_fields=(ProfileField.CURRENT_CITY,),
    )

    assert details.current_city == "Thành phố Ví Dụ"
    assert details.hometown is None
    assert details.birth_date is None
    assert details.birth_year is None


def test_work_and_education_years_do_not_become_birth_year() -> None:
    html = """
    <h2 id="work">Work</h2>
    <div role="list" aria-labelledby="work">
      <div role="listitem">January 2, 2022 - Present</div>
      <div role="listitem">Graduated in 2019</div>
    </div>
    """

    assert ProfileParser().parse(
        html,
        source_url="https://www.facebook.com/synthetic.user/about",
    ) == ProfileDetails()


def test_valid_empty_about_page_is_successful_empty_details() -> None:
    assert ProfileParser().parse(
        "<html><body><h2>About</h2></body></html>",
        source_url="https://www.facebook.com/synthetic.user/about",
    ) == ProfileDetails()


def test_directory_link_and_address_sections_are_parsed_conservatively() -> None:
    html = """
    <h2 id="links">Liên kết</h2>
    <div role="list" aria-labelledby="links">
      <div role="listitem">
        <a href="https://profile.example.test/?fbclid=synthetic">Website</a>
      </div>
    </div>
    <h2 id="address">Địa chỉ</h2>
    <div role="list" aria-labelledby="address">
      <div role="listitem">123 Đường Ví Dụ</div>
    </div>
    """

    details = ProfileParser().parse(
        html,
        source_url="https://www.facebook.com/synthetic.user/directory_links",
    )

    assert details.website == "https://profile.example.test/"
    assert details.address == "123 Đường Ví Dụ"


def test_current_labelled_dom_parses_personal_details_without_role_list() -> None:
    html = """
    <section>
      <h2>Location</h2>
      <div role="button">
        <a href="https://www.facebook.com/synthetic-city">Synthetic City</a>
        <span>Current city</span>
      </div>
      <h2>Birthday</h2>
      <div>
        <span>November 30, 1997</span>
        <span>Birthday</span>
      </div>
    </section>
    """

    details = ProfileParser().parse(
        html,
        source_url=(
            "https://www.facebook.com/synthetic.user/directory_personal_details"
        ),
    )

    assert details.current_city == "Synthetic City"
    assert details.birth_date == "1997-11-30"
    assert details.birth_year == 1997


def test_current_labelled_dom_parses_contact_values_without_role_list() -> None:
    html = """
    <section>
      <div><span>+1 202-555-0147</span><span>Mobile</span></div>
      <div><span>123 Synthetic Street</span><span>Address</span></div>
      <div>
        <a href="https://profile.example.test/?fbclid=synthetic">Profile site</a>
        <span>Website</span>
      </div>
    </section>
    """

    details = ProfileParser().parse(
        html,
        source_url="https://www.facebook.com/synthetic.user/directory_links",
    )

    assert details.phone_numbers == ("+1 202-555-0147",)
    assert details.address == "123 Synthetic Street"
    assert details.website == "https://profile.example.test/"


@pytest.mark.parametrize(
    ("value", "expected_date", "expected_year"),
    [
        ("January 2, 1990", "1990-01-02", 1990),
        ("Born 1988", None, 1988),
        ("February 31, 1990", None, None),
    ],
)
def test_english_birth_values_are_conservative(
    value: str,
    expected_date: str | None,
    expected_year: int | None,
) -> None:
    html = f"""
    <h2 id="basic">Basic info</h2>
    <div role="list" aria-labelledby="basic">
      <div role="listitem">{value}</div>
    </div>
    """
    details = ProfileParser().parse(
        html,
        source_url="https://www.facebook.com/synthetic.user/about",
    )

    assert details.birth_date == expected_date
    assert details.birth_year == expected_year
