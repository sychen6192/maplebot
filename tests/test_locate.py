"""小地圖自動定位：合成畫面上貼角落圖案，驗證找得回正確 ROI。"""
import numpy as np
import pytest

from maplebot.vision.locate import find_minimap


def _corner(seed):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (12, 12), dtype=np.uint8)


@pytest.fixture
def scene():
    rng = np.random.default_rng(3)
    frame = rng.integers(90, 130, (400, 600, 3), dtype=np.uint8)
    tl = _corner(1)
    br = _corner(2)
    # 小地圖外框：左上角在 (50, 40)，右下角圖案的右下端在 (250, 150)
    frame[40:52, 50:62] = tl[:, :, None]
    frame[138:150, 238:250] = br[:, :, None]
    return frame, tl, br


def test_find_minimap(scene):
    frame, tl, br = scene
    region = find_minimap(frame, tl, br, border=4)
    assert region == (54, 44, 192, 102)  # (50+4, 40+4, 246-54, 146-44)


def test_missing_corners_returns_none(scene):
    frame, tl, _ = scene
    stranger = _corner(9)  # 畫面上不存在的圖案
    assert find_minimap(frame, tl, stranger, border=4) is None


def test_degenerate_region_rejected():
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    tl = _corner(1)
    br = _corner(2)
    # 兩個角貼在幾乎同一個位置 -> 區域太小要拒絕
    frame[10:22, 10:22] = tl[:, :, None]
    frame[14:26, 18:30] = br[:, :, None]
    assert find_minimap(frame, tl, br, border=4) is None
