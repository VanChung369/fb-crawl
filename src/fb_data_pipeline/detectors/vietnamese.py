from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def strip_accents(text: str) -> str:
    """Strip Vietnamese diacritics for flexible fuzzy prefix/text matching."""
    text = text.replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


@dataclass(frozen=True, slots=True)
class ProvinceInfo:
    name: str
    aliases: tuple[str, ...]
    population_weight: int  # Tỷ trọng dân số / lượng người dùng Facebook ước lượng


# 63 Tỉnh / Thành phố Việt Nam (sắp xếp theo trọng số dân số / FB)
VIETNAM_PROVINCES: tuple[ProvinceInfo, ...] = (
    ProvinceInfo("TP. Hồ Chí Minh", ("TP. Hồ Chí Minh", "Hồ Chí Minh", "Ho Chi Minh", "TP.HCM", "TPHCM", "Sài Gòn", "Sai Gon", "Saigon", "Ho Chi Minh City", "Ho Chi Minh City, Vietnam", "Saigon, Vietnam", "Ho Chi Minh, Vietnam"), 950),
    ProvinceInfo("Hà Nội", ("Hà Nội", "Ha Noi", "Hanoi", "HN", "Hanoi, Vietnam", "Ha Noi, Vietnam"), 850),
    ProvinceInfo("Thanh Hóa", ("Thanh Hóa", "Thanh Hoa", "Thanhhoa", "Thanh Hoa, Vietnam"), 370),
    ProvinceInfo("Nghệ An", ("Nghệ An", "Nghe An", "Nghean", "Vinh", "Nghe An, Vietnam"), 340),
    ProvinceInfo("Đồng Nai", ("Đồng Nai", "Dong Nai", "Dongnai", "Bien Hoa", "Bienhoa", "Dong Nai, Vietnam"), 320),
    ProvinceInfo("Bình Dương", ("Bình Dương", "Binh Duong", "Binhduong", "Binh Duong, Vietnam"), 280),
    ProvinceInfo("Hải Phòng", ("Hải Phòng", "Hai Phong", "Haiphong", "Hai Phong, Vietnam", "Haiphong, Vietnam"), 210),
    ProvinceInfo("Hải Dương", ("Hải Dương", "Hai Duong", "Haiduong", "Hai Duong, Vietnam"), 195),
    ProvinceInfo("An Giang", ("An Giang", "Angiang", "Long Xuyen", "An Giang, Vietnam"), 190),
    ProvinceInfo("Bắc Giang", ("Bắc Giang", "Bac Giang", "Bacgiang", "Bac Giang, Vietnam"), 190),
    ProvinceInfo("Đắk Lắk", ("Đắk Lắk", "Dak Lak", "Daklak", "Buon Ma Thuot", "Buonmathuot", "Dak Lak, Vietnam"), 190),
    ProvinceInfo("Thái Bình", ("Thái Bình", "Thai Binh", "Thaibinh", "Thai Binh, Vietnam"), 187),
    ProvinceInfo("Nam Định", ("Nam Định", "Nam Dinh", "Namdinh", "Nam Dinh, Vietnam"), 180),
    ProvinceInfo("Tiền Giang", ("Tiền Giang", "Tien Giang", "Tiengiang", "My Tho", "Tien Giang, Vietnam"), 178),
    ProvinceInfo("Kiên Giang", ("Kiên Giang", "Kien Giang", "Kiengiang", "Rach Gia", "Phu Quoc", "Kien Giang, Vietnam"), 175),
    ProvinceInfo("Long An", ("Long An", "Longan", "Tan An", "Long An, Vietnam"), 170),
    ProvinceInfo("Đồng Tháp", ("Đồng Tháp", "Dong Thap", "Dongthap", "Cao Lanh", "Sa Dec", "Dong Thap, Vietnam"), 160),
    ProvinceInfo("Gia Lai", ("Gia Lai", "Gialai", "Pleiku", "Gia Lai, Vietnam"), 160),
    ProvinceInfo("Bắc Ninh", ("Bắc Ninh", "Bac Ninh", "Bacninh", "Bac Ninh, Vietnam"), 150),
    ProvinceInfo("Bình Định", ("Bình Định", "Binh Dinh", "Binhdinh", "Quy Nhon", "Binh Dinh, Vietnam"), 150),
    ProvinceInfo("Phú Thọ", ("Phú Thọ", "Phu Tho", "Phutho", "Viet Tri", "Phu Tho, Vietnam"), 150),
    ProvinceInfo("Quảng Nam", ("Quảng Nam", "Quang Nam", "Quangnam", "Hoi An", "Tam Ky", "Quang Nam, Vietnam"), 150),
    ProvinceInfo("Quảng Ninh", ("Quảng Ninh", "Quang Ninh", "Quangninh", "Ha Long", "Halong", "Quang Ninh, Vietnam"), 135),
    ProvinceInfo("Thái Nguyên", ("Thái Nguyên", "Thai Nguyen", "Thainguyen", "Thai Nguyen, Vietnam"), 132),
    ProvinceInfo("Cần Thơ", ("Cần Thơ", "Can Tho", "Cantho", "Can Tho, Vietnam"), 130),
    ProvinceInfo("Bến Tre", ("Bến Tre", "Ben Tre", "Bentre", "Ben Tre, Vietnam"), 130),
    ProvinceInfo("Bình Thuận", ("Bình Thuận", "Binh Thuan", "Binhthuan", "Phan Thiet", "Binh Thuan, Vietnam"), 130),
    ProvinceInfo("Hà Tĩnh", ("Hà Tĩnh", "Ha Tinh", "Hatinh", "Ha Tinh, Vietnam"), 130),
    ProvinceInfo("Hưng Yên", ("Hưng Yên", "Hung Yen", "Hungyen", "Hung Yen, Vietnam"), 130),
    ProvinceInfo("Lâm Đồng", ("Lâm Đồng", "Lam Dong", "Đà Lạt", "Da Lat", "Dalat", "Lam Dong, Vietnam"), 130),
    ProvinceInfo("Sơn La", ("Sơn La", "Son La", "Sonla", "Son La, Vietnam"), 128),
    ProvinceInfo("Khánh Hòa", ("Khánh Hòa", "Khanh Hoa", "Nha Trang", "Nhatrang", "Khanh Hoa, Vietnam"), 125),
    ProvinceInfo("Quảng Ngãi", ("Quảng Ngãi", "Quang Ngai", "Quangngai", "Quang Ngai, Vietnam"), 125),
    ProvinceInfo("Bà Rịa - Vũng Tàu", ("Bà Rịa - Vũng Tàu", "Bà Rịa", "Vũng Tàu", "Vung Tau", "Vungtau", "Ba Ria - Vung Tau", "Vung Tau, Vietnam"), 120),
    ProvinceInfo("Cà Mau", ("Cà Mau", "Ca Mau", "Camau", "Ca Mau, Vietnam"), 120),
    ProvinceInfo("Đà Nẵng", ("Đà Nẵng", "Da Nang", "Danang", "Da Nang, Vietnam", "Danang, Vietnam"), 120),
    ProvinceInfo("Sóc Trăng", ("Sóc Trăng", "Soc Trang", "Soctrang", "Soc Trang, Vietnam"), 120),
    ProvinceInfo("Tây Ninh", ("Tây Ninh", "Tay Ninh", "Tayninh", "Tay Ninh, Vietnam"), 118),
    ProvinceInfo("Vĩnh Phúc", ("Vĩnh Phúc", "Vinh Phuc", "Vinhphuc", "Vinh Yen", "Vinh Phuc, Vietnam"), 118),
    ProvinceInfo("Thừa Thiên Huế", ("Thừa Thiên Huế", "Huế", "Hue", "Thua Thien Hue", "Hue, Vietnam"), 116),
    ProvinceInfo("Hà Nam", ("Hà Nam", "Ha Nam", "Hanam", "Phu Ly", "Ha Nam, Vietnam"), 88),
    ProvinceInfo("Vĩnh Long", ("Vĩnh Long", "Vinh Long"), 103),
    ProvinceInfo("Trà Vinh", ("Trà Vinh", "Tra Vinh"), 101),
    ProvinceInfo("Bình Phước", ("Bình Phước", "Binh Phuoc"), 100),
    ProvinceInfo("Ninh Bình", ("Ninh Bình", "Ninh Binh"), 100),
    ProvinceInfo("Bạc Liêu", ("Bạc Liêu", "Bac Lieu"), 92),
    ProvinceInfo("Quảng Bình", ("Quảng Bình", "Quang Binh"), 91),
    ProvinceInfo("Hà Giang", ("Hà Giang", "Ha Giang", "Hagiang", "Ha Giang, Vietnam"), 89),
    ProvinceInfo("Phú Yên", ("Phú Yên", "Phu Yen", "Tuy Hoa", "Phu Yen, Vietnam"), 88),
    ProvinceInfo("Hòa Bình", ("Hòa Bình", "Hoa Binh"), 87),
    ProvinceInfo("Yên Bái", ("Yên Bái", "Yen Bai"), 83),
    ProvinceInfo("Lạng Sơn", ("Lạng Sơn", "Lang Son"), 80),
    ProvinceInfo("Tuyên Quang", ("Tuyên Quang", "Tuyen Quang"), 80),
    ProvinceInfo("Lào Cai", ("Lào Cai", "Lao Cai"), 74),
    ProvinceInfo("Hậu Giang", ("Hậu Giang", "Hau Giang"), 73),
    ProvinceInfo("Đắk Nông", ("Đắk Nông", "Dak Nong"), 68),
    ProvinceInfo("Điện Biên", ("Điện Biên", "Dien Bien"), 64),
    ProvinceInfo("Quảng Trị", ("Quảng Trị", "Quang Tri"), 64),
    ProvinceInfo("Ninh Thuận", ("Ninh Thuận", "Ninh Thuan"), 60),
    ProvinceInfo("Kon Tum", ("Kon Tum",), 55),
    ProvinceInfo("Cao Bằng", ("Cao Bằng", "Cao Bang"), 54),
    ProvinceInfo("Lai Châu", ("Lai Châu", "Lai Chau"), 48),
    ProvinceInfo("Bắc Kạn", ("Bắc Kạn", "Bac Kan"), 32),
)

# Từ điển Tên gọi chính (Given name) đặc trưng Nam (có dấu)
MALE_GIVEN_NAMES: frozenset[str] = frozenset({
    "toàn", "hùng", "dũng", "cường", "thắng", "tuấn", "vinh", "phong", "long",
    "khoa", "đạt", "quân", "kiên", "bách", "huy", "việt", "đức", "trung",
    "tùng", "hoàng", "sơn", "thành", "hiếu", "thịnh", "trí", "tài", "quang",
    "tiến", "bảo", "phúc", "hải", "lâm", "khang", "nguyên", "nam", "chiến",
    "nghĩa", "trọng", "hưng", "duy", "chí", "luân", "nhật", "đăng", "thái",
    "quyết", "vũ", "bằng", "khôi", "định", "hào", "thiện", "đại", "nhân",
    "thế", "bửu", "chánh", "danh", "doanh", "hiệp", "hoàn", "khoát", "lực",
    "mạnh", "phi", "phú", "phước", "sáng", "tân", "thạch", "thụ", "thuận",
    "triều", "triệu", "tự", "vượng", "xuân", "hưởng", "quân", "bình",
})

# Từ điển Tên gọi chính đặc trưng Nữ (có dấu)
FEMALE_GIVEN_NAMES: frozenset[str] = frozenset({
    "hoa", "lan", "hương", "mai", "linh", "trang", "thảo", "hà", "ngân",
    "nhung", "thu", "thủy", "thúy", "hằng", "hiền", "hạnh", "dung", "yến",
    "trâm", "quỳnh", "diệp", "chi", "thư", "huyền", "vy", "oanh", "đào",
    "loan", "cúc", "trúc", "my", "nhi", "bích", "tuyết", "ngọc", "ly",
    "duyên", "nguyệt", "phượng", "nga", "liên", "nhài", "vân", "thơm", "thoa",
    "mơ", "hường", "thương", "sen", "nhâm", "gấm", "lụa", "diễm", "kiều",
    "trinh", "hân", "nương", "thắm", "mến", "mận", "thục", "dịu", "dương",
    "xuyến", "hợi", "tươi", "vui", "mùi", "khuyên", "giao",
})

# Tên lưỡng tính (Unisex)
UNISEX_GIVEN_NAMES: frozenset[str] = frozenset({
    "anh", "khánh", "thanh", "giang", "phương", "bình", "châu", "tú", "an",
})

# Tên đệm thiên hướng Nam
MALE_MIDDLE_NAMES: frozenset[str] = frozenset({
    "văn", "tuấn", "đức", "quang", "hoàng", "hữu", "đình", "xuân",
    "minh", "quốc", "trọng", "duy", "tiến", "thế", "hải", "phú",
    "bá", "trí", "công", "việt", "thành", "vĩnh", "mạnh", "trung",
})

# Tên đệm thiên hướng Nữ
FEMALE_MIDDLE_NAMES: frozenset[str] = frozenset({
    "thị", "mai", "ngọc", "thùy", "thúy", "hà", "kim", "thu",
    "phương", "hồng", "diệu", "khánh", "bảo", "ái", "mỹ", "tuyết",
    "bích", "thảo", "diễm", "nhã",
})

# Danh sách các Họ phổ biến tại Việt Nam
VIETNAMESE_SURNAMES: frozenset[str] = frozenset({
    "nguyễn", "trần", "lê", "phạm", "hoàng", "huỳnh", "phan", "vũ", "võ",
    "đặng", "bùi", "đỗ", "hồ", "ngô", "dương", "lý", "đinh", "đoàn",
    "trịnh", "đào", "cao", "lưu", "lương", "tạ", "phùng", "tô", "vương",
    "quách", "giáp", "thân", "vi", "nông", "la", "trương",
})


def _clean_text(value: str | None) -> str:
    return str(value or "").strip()


def infer_vietnamese_gender(name: str, current_gender: str = "") -> str:
    """Suy đoán giới tính Nam/Nữ từ họ tên tiếng Việt đầy đủ."""
    curr = _clean_text(current_gender)
    curr_norm = curr.casefold()

    # Nếu đã có giới tính chuẩn xác không bị mask, giữ nguyên giá trị gốc
    if "*" not in curr and curr_norm in {"nam", "male", "m", "nữ", "nu", "female", "f"}:
        return curr

    raw_name = _clean_text(name)
    if not raw_name:
        return "" if curr in {"*", "-", "—", "unknown", "khác", "none"} else curr

    # Bóc tách ngoặc bao ngoài nếu có: e.g. [Dương Ngọc Toàn] -> Dương Ngọc Toàn
    name_clean = raw_name.strip("[](){}\"' ")
    # Bỏ phần ngoặc phụ bên trong (ví dụ: Dương Ngọc Toàn (Toan Duong) -> Dương Ngọc Toàn)
    stripped = re.sub(r"\([^\)]*\)|\[[^\]]*\]", " ", name_clean)
    if stripped.strip():
        name_clean = stripped
    cleaned_name = re.sub(r"[0-9_\W]+", " ", name_clean)
    words = [w.casefold() for w in cleaned_name.split() if w]

    if not words:
        return ""

    # Tách họ, tên đệm, tên chính
    given_name = words[-1]
    given_name_no_accent = strip_accents(given_name)

    middle_words = words[1:-1] if len(words) > 2 else (
        [words[0]] if len(words) == 2 else []
    )
    penultimate = words[-2] if len(words) >= 2 else ""
    penultimate_no_acc = strip_accents(penultimate)

    # Quy tắc 1: Tên đệm có chữ "Thị" -> Nữ 100%
    if any(w in {"thị", "thi"} for w in middle_words):
        return "Nữ"

    # Quy tắc 2: Tên đệm có chữ "Văn" -> Nam ~99%
    if any(w in {"văn", "van"} for w in middle_words):
        return "Nam"

    # Quy tắc 3: Tên gọi thuộc nhóm Unisex (Anh, Khánh, Thanh...) -> xét tên đệm kề cuối
    if given_name in UNISEX_GIVEN_NAMES or given_name_no_accent in UNISEX_GIVEN_NAMES:
        if penultimate in MALE_MIDDLE_NAMES or penultimate_no_acc in {strip_accents(m) for m in MALE_MIDDLE_NAMES}:
            return "Nam"
        if penultimate in FEMALE_MIDDLE_NAMES or penultimate_no_acc in {strip_accents(f) for f in FEMALE_MIDDLE_NAMES}:
            return "Nữ"
        # Nếu penultimate chưa rõ, tìm trong tất cả tên đệm
        if any(w in MALE_MIDDLE_NAMES for w in middle_words):
            return "Nam"
        if any(w in FEMALE_MIDDLE_NAMES for w in middle_words):
            return "Nữ"

    # Quy tắc 4: Tra từ điển tên chính có dấu trước
    if given_name in MALE_GIVEN_NAMES:
        return "Nam"
    if given_name in FEMALE_GIVEN_NAMES:
        return "Nữ"

    # Fallback tra từ điển không dấu nếu tên không có dấu
    male_no_acc = {strip_accents(m) for m in MALE_GIVEN_NAMES}
    female_no_acc = {strip_accents(f) for f in FEMALE_GIVEN_NAMES}
    if given_name == given_name_no_accent:
        if given_name_no_accent in male_no_acc and given_name_no_accent not in female_no_acc:
            return "Nam"
        if given_name_no_accent in female_no_acc and given_name_no_accent not in male_no_acc:
            return "Nữ"

    # Quy tắc 5: Nếu tên chính là tên lạ nhưng có tên đệm đặc trưng nam/nữ
    if any(w in {"đức", "huu", "hữu", "quang", "đình", "dinh", "quốc", "quoc"} for w in middle_words):
        return "Nam"
    if any(w in {"thị", "thi", "thùy", "thuy", "mai", "nga"} for w in middle_words):
        return "Nữ"

    # Quy tắc 6: Định dạng tên đảo ngược trên Facebook (FirstName LastName, ví dụ: Hoa Nguyễn, Toàn Dương)
    first_word = words[0].casefold()
    if len(words) >= 2 and any(w in VIETNAMESE_SURNAMES for w in words[1:]):
        if first_word in MALE_GIVEN_NAMES:
            return "Nam"
        if first_word in FEMALE_GIVEN_NAMES:
            return "Nữ"

    return ""


def infer_vietnamese_address(raw_address: str) -> str:
    """Khôi phục tên Tỉnh / Thành phố Việt Nam từ chuỗi địa chỉ bị che mask hoặc viết tắt."""
    text = _clean_text(raw_address)
    if not text:
        return ""

    has_mask = "*" in text or "…" in text or "..." in text

    # Nếu không bị che mask, giữ nguyên địa chỉ gốc (trừ khi là các viết tắt rất ngắn như HN, TPHCM)
    if not has_mask:
        text_lower = text.casefold()
        if text_lower in {"hn", "ha noi", "hanoi"}:
            return "Hà Nội"
        if text_lower in {"tphcm", "tp.hcm", "tp hcm", "sai gon", "sài gòn", "saigon"}:
            return "TP. Hồ Chí Minh"
        return text

    # Khi chuỗi BỊ MASK (ví dụ: "Thá********", "Hà ***", "Han***********", "Đà N***")
    # Lấy phần tiền tố rõ nét trước ký tự mask đầu tiên
    first_star_idx = re.search(r"[\*\.…_]", text)
    if not first_star_idx:
        return text

    prefix = text[: first_star_idx.start()].strip()
    if not prefix:
        return text

    prefix_lower = prefix.casefold()
    prefix_no_acc = strip_accents(prefix_lower)
    prefix_no_space = prefix_no_acc.replace(" ", "").replace("-", "")
    prefix_no_space_lower = prefix_lower.replace(" ", "").replace("-", "")
    raw_length = len(text)
    prefix_has_diacritics = prefix_lower != prefix_no_acc

    # Lọc danh sách các tỉnh có tên hoặc bí danh bắt đầu bằng prefix
    matched_candidates: list[tuple[ProvinceInfo, int]] = []
    for prov in VIETNAM_PROVINCES:
        best_alias_len: int | None = None
        for alias in prov.aliases:
            alias_lower = alias.casefold()
            alias_no_acc = strip_accents(alias_lower)
            alias_no_space = alias_no_acc.replace(" ", "").replace("-", "")
            alias_no_space_lower = alias_lower.replace(" ", "").replace("-", "")

            # Nếu prefix có dấu, BẮT BUỘC alias phải khớp có dấu chính xác
            if prefix_has_diacritics:
                is_match = alias_lower.startswith(prefix_lower) or (
                    bool(prefix_no_space_lower) and alias_no_space_lower.startswith(prefix_no_space_lower)
                )
            else:
                # Nếu prefix không dấu (ví dụ "Ha ***", "Han***", "Da N***"), đối chiếu không dấu
                is_match = alias_no_acc.startswith(prefix_no_acc) or (
                    bool(prefix_no_space) and alias_no_space.startswith(prefix_no_space)
                )

            if is_match:
                # Lấy alias có độ dài sát nhất với raw_length
                if best_alias_len is None or abs(len(alias) - raw_length) < abs(best_alias_len - raw_length):
                    best_alias_len = len(alias)

        if best_alias_len is not None:
            matched_candidates.append((prov, best_alias_len))

    if not matched_candidates:
        return text

    if len(matched_candidates) == 1:
        return matched_candidates[0][0].name

    # Nếu có nhiều ứng cử viên (ví dụ: "Hà " -> Hà Nội, Hà Nam, Hà Tĩnh, Hà Giang;
    # hoặc "Han" -> Hà Nội, Hà Nam...):
    # Tính độ lệch độ dài so với chuỗi mask
    candidates_with_diff = [
        (abs(prov_len - raw_length), prov)
        for prov, prov_len in matched_candidates
    ]
    min_diff = min(diff for diff, _ in candidates_with_diff)

    # Lấy các ứng cử viên có độ lệch độ dài sát nhau (trong khoảng chênh lệch <= 2 ký tự)
    close_candidates = [
        prov for diff, prov in candidates_with_diff if diff <= min_diff + 2
    ]

    # Trong nhóm ứng cử viên sát độ dài nhất, chọn tỉnh có độ phổ biến / dân số cao nhất
    close_candidates.sort(key=lambda p: p.population_weight, reverse=True)
    return close_candidates[0].name


def infer_profile_attributes(
    name: str,
    raw_address: str,
    raw_gender: str,
) -> tuple[str, str]:
    """Hàm tổng hợp suy đoán địa chỉ và giới tính cho hồ sơ người dùng."""
    inferred_gender = infer_vietnamese_gender(name, raw_gender)
    inferred_address = infer_vietnamese_address(raw_address)
    return inferred_address, inferred_gender
