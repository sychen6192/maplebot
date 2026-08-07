"""HP/MP/EXP 條辨識：合成影像 + 真實截圖雙重驗證。"""
import numpy as np
import pytest

from maplebot.vision.status import bar_ratio

# 對 tests/fixtures/mapleaga_800x600.jpg 校正的 ROI（同 config/default.yaml）
HP_ROI = (223, 609, 105, 10)
MP_ROI = (332, 609, 103, 10)
EXP_ROI = (444, 609, 115, 10)


def _crop(img, roi):
    x, y, w, h = roi
    return img[y:y + h, x:x + w]


def _synthetic_bar(fill: float, color_bgr, bg=(60, 60, 60), w=120, h=10):
    bar = np.full((h, w, 3), bg, dtype=np.uint8)
    bar[:, : int(w * fill)] = color_bgr
    return bar


def test_synthetic_red_60_percent():
    bar = _synthetic_bar(0.6, (0, 0, 230))
    assert bar_ratio(bar, "red") == pytest.approx(0.6, abs=0.02)


def test_synthetic_empty_and_full():
    assert bar_ratio(_synthetic_bar(0.0, (0, 0, 230)), "red") == 0.0
    assert bar_ratio(_synthetic_bar(1.0, (200, 60, 0)), "blue") == pytest.approx(1.0, abs=0.02)


def test_synthetic_text_overlay_does_not_break_ratio():
    bar = _synthetic_bar(0.7, (0, 0, 230))
    bar[2:8, 30:34] = (255, 255, 255)  # 模擬覆蓋在條上的白色數字
    bar[2:8, 40:42] = (255, 255, 255)
    assert bar_ratio(bar, "red") == pytest.approx(0.7, abs=0.03)


def test_fixture_hp_full(fixture_frame):
    ratio = bar_ratio(_crop(fixture_frame, HP_ROI), "red")
    assert ratio == pytest.approx(1.0, abs=0.03)  # 畫面顯示 HP[1395/1395]


def test_fixture_mp_full(fixture_frame):
    ratio = bar_ratio(_crop(fixture_frame, MP_ROI), "blue")
    assert ratio == pytest.approx(1.0, abs=0.03)  # 畫面顯示 MP[8413/8413]


def test_fixture_exp_matches_onscreen_value(fixture_frame):
    ratio = bar_ratio(_crop(fixture_frame, EXP_ROI), "yellow")
    assert ratio == pytest.approx(0.5989, abs=0.03)  # 畫面顯示 EXP 59.89%


def test_unknown_color_raises():
    with pytest.raises(ValueError):
        bar_ratio(_synthetic_bar(0.5, (0, 0, 230)), "purple")
