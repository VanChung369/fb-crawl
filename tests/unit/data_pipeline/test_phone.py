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

