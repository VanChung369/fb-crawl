import pytest

from fb_data_pipeline.core.phone import InvalidPhoneNumber, normalize_phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0912 345 678", "+84912345678"),
        ("84 912 345 678", "+84912345678"),
        ("+84 912 345 678", "+84912345678"),
        ("0084 912 345 678", "+84912345678"),
    ],
)
def test_normalize_phone_preserves_one_text_key(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


def test_normalize_phone_rejects_implausible_values() -> None:
    with pytest.raises(InvalidPhoneNumber):
        normalize_phone("123")


@pytest.mark.parametrize("raw", [
    "0966777*", "096300527*", "096300527x", "096300527_", "096300527•",
    "096300527…", "096...3005272", "+84966777", "0966777",
    "+84243825678", "0243825678", "+849630052721",
])
def test_normalize_phone_never_turns_masked_or_truncated_vietnamese_numbers_into_contacts(raw: str) -> None:
    with pytest.raises(InvalidPhoneNumber):
        normalize_phone(raw)


@pytest.mark.parametrize(("raw", "expected"), [
    ("0963005272", "+84963005272"),
    ("096.300.5272", "+84963005272"),
    ("(024) 3825 6789", "+842438256789"),
    ("+1 (212) 555-0123", "+12125550123"),
])
def test_normalize_phone_retains_complete_formatted_numbers(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected

