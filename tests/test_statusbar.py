"""自動找 HP/MP/EXP 條：要認得出整條（含空的那段），也不能被彩色按鈕騙走。"""
import numpy as np

from maplebot.vision.statusbar import find_status_bars
from maplebot.vision.status import bar_ratio

FILL = {"red": (0, 0, 220), "blue": (220, 60, 0), "yellow": (0, 200, 220)}   # BGR


def _ui(w=1366, h=768, bar_y=700, bar_h=14, width=100, fills=(0.6, 1.0, 0.5),
        gap=10, x0=500):
    """畫一條狀態列：三條 bar 並排，各自有深色外框與灰色空白段。"""
    img = np.full((h, w, 3), 40, dtype=np.uint8)
    boxes = {}
    x = x0
    for (name, color), frac in zip((("hp_bar", "red"), ("mp_bar", "blue"),
                                    ("exp_bar", "yellow")), fills):
        img[bar_y - 1:bar_y + bar_h + 1, x - 1:x + width + 1] = (10, 10, 10)  # 外框
        img[bar_y:bar_y + bar_h, x:x + width] = (150, 150, 150)               # 空段
        img[bar_y:bar_y + bar_h, x:x + int(width * frac)] = FILL[color]
        boxes[name] = (x, bar_y, width, bar_h)
        x += width + gap
    return img, boxes


def test_finds_all_three_bars():
    img, boxes = _ui()
    found = find_status_bars(img)
    assert found is not None
    for name, (bx, by, bw, bh) in boxes.items():
        fx, fy, fw, fh = found[name]
        assert abs(fx - bx) <= 2 and abs(fy - by) <= 2, (name, found[name])
        assert abs(fw - bw) <= 3, (name, fw, bw)


def test_found_roi_reads_the_right_ratio():
    """只框有顏色的那段的話，血剩 60% 會被當成整條，永遠讀成 100%。"""
    img, _ = _ui(fills=(0.6, 1.0, 0.35))
    found = find_status_bars(img)
    assert abs(bar_ratio(img[found["hp_bar"][1]:found["hp_bar"][1] + found["hp_bar"][3],
                             found["hp_bar"][0]:found["hp_bar"][0] + found["hp_bar"][2]],
                         "red") - 0.6) < 0.05


def test_not_fooled_by_coloured_buttons():
    """右下角的購物商場/拍賣/目錄按鈕也是紅藍黃——第一版就是被它們騙走的。

    按鈕接近正方形、彼此 y 也對不齊，靠「又扁又長 + 同一排」擋掉。
    """
    img, boxes = _ui()
    for i, color in enumerate(("red", "blue", "yellow")):    # 三顆方形彩色按鈕
        bx = 1150 + i * 60
        img[690:730, bx:bx + 45] = FILL[color]
    found = find_status_bars(img)
    assert found is not None
    assert abs(found["hp_bar"][0] - boxes["hp_bar"][0]) <= 2, found


def test_no_status_bar_returns_none():
    assert find_status_bars(np.full((768, 1366, 3), 40, dtype=np.uint8)) is None


def test_empty_frame():
    assert find_status_bars(np.zeros((0, 0, 3), dtype=np.uint8)) is None


def test_works_at_another_resolution():
    img, boxes = _ui(w=1920, h=1080, bar_y=1000, bar_h=20, width=140, x0=700)
    found = find_status_bars(img)
    assert found is not None
    assert abs(found["exp_bar"][0] - boxes["exp_bar"][0]) <= 2
