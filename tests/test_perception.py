"""感知層整合測試：合成一張完整畫面，驗證 GameState 各欄位。"""
import cv2
import numpy as np
import pytest

from maplebot.config import AppCfg
from maplebot.perception import Perceiver
from maplebot.vision.mobs import TemplateMobDetector


def _sprite():
    rng = np.random.default_rng(42)
    sprite = rng.integers(0, 255, (24, 30, 3), dtype=np.uint8)
    cv2.circle(sprite, (15, 12), 9, (30, 200, 60), -1)
    cv2.rectangle(sprite, (5, 5), (25, 19), (200, 50, 180), 2)
    return sprite


@pytest.fixture
def cfg():
    c = AppCfg()
    c.regions = {
        "minimap": (10, 10, 60, 40),
        "hp_bar": (10, 60, 50, 8),
        "mp_bar": (70, 60, 50, 8),
        "playfield": (0, 80, 300, 200),
    }
    return c


@pytest.fixture
def frame():
    rng = np.random.default_rng(7)
    img = rng.integers(0, 50, (300, 300, 3), dtype=np.uint8)
    img[10:50, 10:70] = (150, 190, 205)          # minimap 背景
    img[28:31, 38:41] = (0, 255, 255)            # 玩家黃點（minimap 內 (28,18)）
    img[60:68, 10:45] = (0, 0, 230)              # HP 紅條 35/50 = 70%
    img[60:68, 70:120] = (200, 60, 0)            # MP 藍條 100%
    img[120:144, 50:80] = _sprite()              # 怪物（playfield 內中心 (65, 52)）
    return img


def test_perceive_full_state(cfg, frame, tmp_path):
    d = tmp_path / "mobs"
    d.mkdir()
    cv2.imwrite(str(d / "m_01.png"), _sprite())
    det = TemplateMobDetector(str(d), threshold=0.8)

    st = Perceiver(cfg, det).perceive(frame, now=1.0)

    assert st.player is not None
    assert abs(st.player[0] - 29) <= 1 and abs(st.player[1] - 19) <= 1
    assert st.hp == pytest.approx(0.7, abs=0.02)
    assert st.mp == pytest.approx(1.0, abs=0.02)
    assert st.exp is None                       # 沒設定 exp_bar 區域
    assert len(st.mobs) == 1
    assert (st.mobs[0].cx, st.mobs[0].cy) == (65, 52)
    assert st.vision_ok


def test_region_out_of_bounds_gives_none(cfg, frame, tmp_path):
    cfg.regions["hp_bar"] = (280, 290, 50, 8)   # 超出 300x300 畫面
    cfg.regions["playfield"] = (0, 80, 400, 400)
    det = TemplateMobDetector(str(tmp_path), threshold=0.8)

    st = Perceiver(cfg, det).perceive(frame, now=1.0)

    assert st.hp is None
    assert st.mobs == []
    assert not st.vision_ok
