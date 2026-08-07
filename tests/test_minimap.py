"""小地圖偵測：用合成小地圖驗證玩家黃點與其他玩家紅點。"""
import numpy as np

from maplebot.config import VisionCfg
from maplebot.vision.minimap import find_others, find_player


def _minimap(w=130, h=60):
    mm = np.full((h, w, 3), (150, 190, 205), dtype=np.uint8)  # 淡土色背景
    mm[40:42, :] = (90, 140, 170)  # 平台線（不該被誤判）
    return mm


def test_find_player_dot():
    mm = _minimap()
    mm[30:33, 50:53] = (0, 255, 255)  # BGR 黃點
    cfg = VisionCfg()
    pos = find_player(mm, cfg)
    assert pos is not None
    assert abs(pos[0] - 51) <= 1 and abs(pos[1] - 31) <= 1


def test_no_player_returns_none():
    assert find_player(_minimap(), VisionCfg()) is None


def test_single_noise_pixel_ignored():
    mm = _minimap()
    mm[10, 10] = (0, 255, 255)  # 只有 1px，低於 min_dot_pixels
    assert find_player(mm, VisionCfg(min_dot_pixels=3)) is None


def test_terrain_blob_not_mistaken_for_player():
    """自由市場這類小地圖的地板本身就是黃色——大面積色塊要被排除。"""
    mm = _minimap()
    mm[50:56, 10:120] = (0, 255, 255)  # 660px 的黃色地形帶
    mm[20:23, 70:73] = (0, 255, 255)   # 真正的玩家點（9px）
    pos = find_player(mm, VisionCfg())
    assert pos is not None
    assert abs(pos[0] - 71) <= 1 and abs(pos[1] - 21) <= 1


def test_player_template_beats_color(tmp_path):
    import cv2
    rng = __import__("numpy").random.default_rng(5)
    mm = _minimap()
    tpl_patch = rng.integers(0, 255, (7, 7), dtype="uint8")
    mm[30:37, 90:97] = tpl_patch[:, :, None]  # 玩家圖示（非黃色也能找到）
    pos = find_player(mm, VisionCfg(), template=tpl_patch)
    assert pos == (93, 33)


def test_find_two_other_players():
    mm = _minimap()
    mm[20:23, 30:33] = (0, 0, 255)   # 紅點 1
    mm[50:53, 100:103] = (0, 0, 255)  # 紅點 2
    others = find_others(mm, VisionCfg())
    assert len(others) == 2
    xs = sorted(p[0] for p in others)
    assert abs(xs[0] - 31) <= 1 and abs(xs[1] - 101) <= 1


def test_others_empty_when_clean():
    assert find_others(_minimap(), VisionCfg()) == []
