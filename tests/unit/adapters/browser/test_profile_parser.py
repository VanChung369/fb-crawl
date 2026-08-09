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

    details = ProfileParser().parse(
        html,
        source_url="https://www.facebook.com/synthetic.user/about",
    )

    assert details.birth_date is None
    assert details.birth_year is None
    assert details.workplace == "January 2, 2022 - Present"


def test_valid_empty_about_page_is_successful_empty_details() -> None:
    assert ProfileParser().parse(
        "<html><body><h2>About</h2></body></html>",
        source_url="https://www.facebook.com/synthetic.user/about",
    ) == ProfileDetails()


def test_profile_name_is_read_from_main_heading() -> None:
    details = ProfileParser().parse(
        "<main><h1>Synthetic User</h1><h2>About</h2></main>",
        source_url=(
            "https://www.facebook.com/synthetic.user/directory_personal_details"
        ),
    )

    assert details.name == "Synthetic User"


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


def test_directory_links_parses_unlabelled_external_anchor() -> None:
    html = """
    <nav><a href="https://www.facebook.com/home.php">Home</a></nav>
    <main>
      <h2>Links</h2>
      <div><a href="https://social.example.test/user?fbclid=synthetic">Social</a></div>
    </main>
    """

    details = ProfileParser().parse(
        html,
        source_url="https://www.facebook.com/synthetic.user/directory_links",
        requested_fields=(ProfileField.WEBSITE,),
    )

    assert details.website == "https://social.example.test/user"


def test_directory_links_parses_visible_domain_from_role_link() -> None:
    html = """
    <main>
      <h2>Links</h2>
      <span role="link" tabindex="0">social.example.test/user</span>
    </main>
    """

    details = ProfileParser().parse(
        html,
        source_url="https://www.facebook.com/synthetic.user/directory_links",
        requested_fields=(ProfileField.WEBSITE,),
    )

    assert details.website == "https://social.example.test/user"


def test_directory_links_parses_visible_domain_from_nested_text() -> None:
    html = """
    <main>
      <h2>Links</h2>
      <div><span><span>social.example.test/user</span></span></div>
    </main>
    """

    details = ProfileParser().parse(
        html,
        source_url="https://www.facebook.com/synthetic.user/directory_links",
        requested_fields=(ProfileField.WEBSITE,),
    )

    assert details.website == "https://social.example.test/user"


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


def test_enrichment_v2_parses_visible_labelled_fields() -> None:
    html = """
    <main>
      <div><span>Builder and photographer</span><span>Bio</span></div>
      <div><span>Example Company</span><span>Workplace</span></div>
      <div><span>Example University</span><span>Education</span></div>
      <div><span>Male</span><span>Gender</span></div>
      <div><span>Vietnamese, English</span><span>Languages</span></div>
      <div><span>Single</span><span>Relationship status</span></div>
    </main>
    """
    details = ProfileParser().parse(
        html,
        source_url="https://www.facebook.com/synthetic.user/directory_work",
        requested_fields=(
            ProfileField.BIO,
            ProfileField.WORKPLACE,
            ProfileField.EDUCATION,
            ProfileField.GENDER,
            ProfileField.LANGUAGES,
            ProfileField.RELATIONSHIP_STATUS,
        ),
    )

    assert details.bio == "Builder and photographer"
    assert details.workplace == "Example Company"
    assert details.education == "Example University"
    assert details.gender == "Male"
    assert details.languages == ("Vietnamese", "English")
    assert details.relationship_status == "Single"


def test_profile_root_extracts_phone_from_intro_and_visible_post_text() -> None:
    html = """
    <main>
      <section aria-label="Giới thiệu">
        <h2>Giới thiệu</h2>
        <div>Liên hệ Zalo 0912 345 678</div>
      </section>
      <div role="article">
        <div data-ad-preview="message">Hotline +84 987 654 321</div>
        <span>09/08/2026</span>
      </div>
    </main>
    """

    details = ProfileParser().parse(
        html,
        source_url="https://www.facebook.com/synthetic.user",
        requested_fields=(ProfileField.PHONE,),
    )

    assert details.phone_numbers == (
        "0912 345 678",
        "+84 987 654 321",
    )
    assert details.phone_sources == (
        "facebook:profile_intro_text",
        "facebook:post_text",
    )
    assert tuple(
        (
            item.value,
            item.source,
            item.source_url,
            item.confidence,
        )
        for item in details.phone_evidence
    ) == (
        (
            "0912 345 678",
            "facebook:profile_intro_text",
            "https://www.facebook.com/synthetic.user",
            "strong_pattern",
        ),
        (
            "+84 987 654 321",
            "facebook:post_text",
            "https://www.facebook.com/synthetic.user",
            "strong_pattern",
        ),
    )


def test_post_phone_evidence_uses_visible_post_permalink() -> None:
    details = ProfileParser().parse(
        """
        <main>
          <div role="article">
            <a href="https://www.facebook.com/example/posts/123?ref=share">
              2 hours
            </a>
            <div data-ad-preview="message">Hotline 0912 345 678</div>
          </div>
        </main>
        """,
        source_url="https://www.facebook.com/example",
        requested_fields=(ProfileField.PHONE,),
    )

    assert details.phone_evidence[0].source_url == (
        "https://www.facebook.com/example/posts/123"
    )


def test_directory_page_does_not_scan_unlabelled_global_numbers() -> None:
    details = ProfileParser().parse(
        "<main><div>174 friends</div><div>09/08/2026</div></main>",
        source_url=(
            "https://www.facebook.com/synthetic.user"
            "/directory_personal_details"
        ),
        requested_fields=(ProfileField.PHONE,),
    )

    assert details.phone_numbers == ()


def test_profile_intro_heading_finds_nearby_phone_without_aria_label() -> None:
    details = ProfileParser().parse(
        """
        <main>
          <div><h2>Intro</h2><div>Số điện thoại: 0987 654 321</div></div>
        </main>
        """,
        source_url="https://www.facebook.com/synthetic.user",
        requested_fields=(ProfileField.PHONE,),
    )

    assert details.phone_numbers == ("0987 654 321",)
    assert details.phone_sources == ("facebook:profile_intro_text",)
