from __future__ import annotations

import pytest

from fb_data_pipeline.detectors.vietnamese import (
    infer_profile_attributes,
    infer_vietnamese_address,
    infer_vietnamese_gender,
)


class TestVietnameseGenderInference:
    def test_explicit_user_example_male(self) -> None:
        """Kiểm tra ví dụ cụ thể của user: [Dương Ngọc Toàn] -> Nam"""
        assert infer_vietnamese_gender("[Dương Ngọc Toàn]", "*") == "Nam"
        assert infer_vietnamese_gender("Dương Ngọc Toàn", "") == "Nam"
        assert infer_vietnamese_gender("Dương Ngọc Toàn", "-") == "Nam"

    def test_male_given_names(self) -> None:
        assert infer_vietnamese_gender("Nguyễn Hùng", "*") == "Nam"
        assert infer_vietnamese_gender("Trần Mạnh Cường", "*") == "Nam"
        assert infer_vietnamese_gender("Phạm Văn Tuấn", "*") == "Nam"
        assert infer_vietnamese_gender("Hoàng Hải Đăng", "*") == "Nam"
        assert infer_vietnamese_gender("Lê Quốc Thắng", "*") == "Nam"

    def test_female_given_names(self) -> None:
        assert infer_vietnamese_gender("Nguyễn Thị Mai", "*") == "Nữ"
        assert infer_vietnamese_gender("Trần Ngọc Linh", "*") == "Nữ"
        assert infer_vietnamese_gender("Lê Thu Trang", "*") == "Nữ"
        assert infer_vietnamese_gender("Phạm Phương Thảo", "*") == "Nữ"
        assert infer_vietnamese_gender("Đặng Quỳnh Hoa", "*") == "Nữ"

    def test_middle_name_priority(self) -> None:
        # "Thị" luôn luôn là Nữ kể cả tên sau có thể unisex
        assert infer_vietnamese_gender("Nguyễn Thị Bình", "*") == "Nữ"
        assert infer_vietnamese_gender("Trần Thị Anh", "*") == "Nữ"
        # "Văn" luôn là Nam
        assert infer_vietnamese_gender("Nguyễn Văn Bình", "*") == "Nam"
        assert infer_vietnamese_gender("Lê Văn Khánh", "*") == "Nam"

    def test_unisex_names_with_penultimate_word(self) -> None:
        assert infer_vietnamese_gender("Nguyễn Tuấn Anh", "*") == "Nam"
        assert infer_vietnamese_gender("Lê Đức Anh", "*") == "Nam"
        assert infer_vietnamese_gender("Trần Mai Anh", "*") == "Nữ"
        assert infer_vietnamese_gender("Đỗ Ngọc Anh", "*") == "Nữ"
        assert infer_vietnamese_gender("Hoàng Kim Chi", "*") == "Nữ"

    def test_preserves_valid_existing_gender(self) -> None:
        assert infer_vietnamese_gender("Nguyễn Văn A", "Nam") == "Nam"
        assert infer_vietnamese_gender("Nguyễn Văn A", "Nữ") == "Nữ"
        assert infer_vietnamese_gender("Dương Ngọc Toàn", "male") == "male"
        assert infer_vietnamese_gender("Nguyễn Thị B", "female") == "female"

    def test_empty_or_invalid_names(self) -> None:
        assert infer_vietnamese_gender("", "*") == ""
        assert infer_vietnamese_gender("   ", "") == ""


class TestVietnameseAddressInference:
    def test_explicit_user_examples(self) -> None:
        """Kiểm tra ví dụ cụ thể của user: Hà *** -> Hà Nội, Thá******** -> Thái Bình, Han*********** -> Hà Nội"""
        assert infer_vietnamese_address("Hà ***") == "Hà Nội"
        assert infer_vietnamese_address("Thá********") == "Thái Bình"
        assert infer_vietnamese_address("Han***********") == "Hà Nội"
        assert infer_vietnamese_address("Dan***********") == "Đà Nẵng"

    def test_masked_provinces(self) -> None:
        assert infer_vietnamese_address("Đà N***") == "Đà Nẵng"
        assert infer_vietnamese_address("Hải P*****") == "Hải Phòng"
        assert infer_vietnamese_address("Hải D*****") == "Hải Dương"
        assert infer_vietnamese_address("Bình D*****") == "Bình Dương"
        assert infer_vietnamese_address("Cần T***") == "Cần Thơ"
        assert infer_vietnamese_address("Hồ Chí *****") == "TP. Hồ Chí Minh"
        assert infer_vietnamese_address("Bắc G****") == "Bắc Giang"
        assert infer_vietnamese_address("Bắc N***") == "Bắc Ninh"
        assert infer_vietnamese_address("Quảng N***") in {
            "Quảng Nam", "Quảng Ninh", "Quảng Ngãi"
        }

    def test_unmasked_address_normalization(self) -> None:
        assert infer_vietnamese_address("Hà Nội") == "Hà Nội"
        assert infer_vietnamese_address("HN") == "Hà Nội"
        assert infer_vietnamese_address("Sài Gòn") == "TP. Hồ Chí Minh"
        assert infer_vietnamese_address("Quận 1, Hồ Chí Minh") == "Quận 1, Hồ Chí Minh"
        assert infer_vietnamese_address("Cầu Giấy, Hà Nội") == "Cầu Giấy, Hà Nội"

    def test_empty_or_unknown_address(self) -> None:
        assert infer_vietnamese_address("") == ""
        assert infer_vietnamese_address("Unknown Street 123") == "Unknown Street 123"


class TestCombinedProfileInference:
    def test_infer_profile_attributes(self) -> None:
        addr, gender = infer_profile_attributes(
            name="[Dương Ngọc Toàn]",
            raw_address="Thá********",
            raw_gender="*",
        )
        assert addr == "Thái Bình"
        assert gender == "Nam"

    def test_thai_nguyen_specific_prefix(self) -> None:
        assert infer_vietnamese_address("Thái Ng*****") == "Thái Nguyên"

    def test_name_with_emojis_and_titles(self) -> None:
        assert infer_vietnamese_gender("Toàn (CEO) 🚀", "*") == "Nam"
        assert infer_vietnamese_gender("🌸 Hoa Nguyễn (Admin)", "*") == "Nữ"
