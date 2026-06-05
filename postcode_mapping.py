# -*- coding: utf-8 -*-
"""台灣 3 碼郵遞區號 (post code) 與縣/市對照表。

依據檔名前三碼 (post code) 判斷所屬縣/市，提供中文名稱與英文檔名。
"""

# (起始郵遞區號, 結束郵遞區號, 中文縣市, 英文名稱)
# 以數值區間判斷，可涵蓋來源資料中的所有 3 碼郵遞區號。
POSTCODE_RANGES = [
    (100, 116, "台北市", "Taipei_City"),
    (200, 206, "基隆市", "Keelung_City"),
    (207, 208, "新北市", "New_Taipei_City"),
    (209, 212, "連江縣", "Lienchiang_County"),
    (220, 253, "新北市", "New_Taipei_City"),
    (260, 272, "宜蘭縣", "Yilan_County"),
    (300, 300, "新竹市", "Hsinchu_City"),
    (302, 315, "新竹縣", "Hsinchu_County"),
    (320, 338, "桃園市", "Taoyuan_City"),
    (350, 369, "苗栗縣", "Miaoli_County"),
    (400, 439, "台中市", "Taichung_City"),
    (500, 530, "彰化縣", "Changhua_County"),
    (540, 558, "南投縣", "Nantou_County"),
    (600, 600, "嘉義市", "Chiayi_City"),
    (602, 625, "嘉義縣", "Chiayi_County"),
    (630, 655, "雲林縣", "Yunlin_County"),
    (700, 745, "台南市", "Tainan_City"),
    (800, 852, "高雄市", "Kaohsiung_City"),
    (880, 885, "澎湖縣", "Penghu_County"),
    (890, 896, "金門縣", "Kinmen_County"),
    (900, 947, "屏東縣", "Pingtung_County"),
    (950, 966, "台東縣", "Taitung_County"),
    (970, 983, "花蓮縣", "Hualien_County"),
]

# 中文縣市 -> 英文名稱 (給前端 / 反查使用)
ZH_TO_EN = {}
for _lo, _hi, _zh, _en in POSTCODE_RANGES:
    ZH_TO_EN.setdefault(_zh, _en)

EN_TO_ZH = {v: k for k, v in ZH_TO_EN.items()}


def lookup(postcode):
    """傳入 3 碼郵遞區號 (字串或數字)，回傳 (中文縣市, 英文名稱)。

    找不到時回傳 (None, None)。
    """
    try:
        code = int(str(postcode).strip()[:3])
    except (ValueError, TypeError):
        return None, None
    for lo, hi, zh, en in POSTCODE_RANGES:
        if lo <= code <= hi:
            return zh, en
    return None, None


def all_counties():
    """回傳所有縣市清單： [(中文, 英文), ...] (去重，保留出現順序)。"""
    seen = []
    for _lo, _hi, zh, en in POSTCODE_RANGES:
        if (zh, en) not in seen:
            seen.append((zh, en))
    return seen


if __name__ == "__main__":
    for zh, en in all_counties():
        print(f"{zh:<6} -> {en}")
