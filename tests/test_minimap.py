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


def test_real_frame_player_dot_not_on_yellow_floor(fixture_frame):
    """真實截圖：黃色地板不可以贏過玩家點。

    合成版的 test_terrain_blob_not_mistaken_for_player 沒抓到這個 bug，因為
    它把地形畫成一整塊乾淨的黃色——那種地形 max_dot_pixels 本來就擋得掉。
    真實畫面的地板有紋理，嚴格比色會把它打碎成 89 個 1~21px 的小塊，每一塊
    都「剛好像一個點」，而真正的玩家點只有 5px。於是面積上限一次都沒生效，
    「取最大的」必然挑到地板：修好之前這裡回報 (11, 45)，人其實在 (95, 11)。
    """
    from maplebot.config import load_config

    cfg = load_config("config/default.yaml", local_path="/nonexistent")
    x, y, w, h = cfg.regions["minimap"]
    mm = fixture_frame[y:y + h, x:x + w]

    pos = find_player(mm, cfg.vision)
    assert pos is not None
    # 玩家點是小地圖右上那顆亮黃菱形
    assert abs(pos[0] - 95) <= 2 and abs(pos[1] - 11) <= 2, \
        f"玩家點抓到 {pos}，應該在 (95, 11) 附近"


def test_real_frame_others_are_not_terrain(fixture_frame):
    """同一張圖的紅點：只有一個真的其他玩家，不能因為地形變出一堆。

    這裡誤判的代價比黃點更直接——safety.pause_when_players 預設開著，
    多冒出一個紅點就是整晚停在那裡等一個不存在的人走開。
    """
    from maplebot.config import load_config

    cfg = load_config("config/default.yaml", local_path="/nonexistent")
    x, y, w, h = cfg.regions["minimap"]
    mm = fixture_frame[y:y + h, x:x + w]

    others = find_others(mm, cfg.vision)
    assert len(others) == 1, f"紅點抓到 {others}"
    assert abs(others[0][0] - 93) <= 2 and abs(others[0][1] - 37) <= 2


def test_merge_gap_off_restores_old_behaviour(fixture_frame):
    """minimap_merge_gap <= 1 要能關掉合併——留給「新做法在我的圖上反而更差」的人。"""
    from dataclasses import replace

    from maplebot.config import load_config

    cfg = load_config("config/default.yaml", local_path="/nonexistent")
    x, y, w, h = cfg.regions["minimap"]
    mm = fixture_frame[y:y + h, x:x + w]

    off = replace(cfg.vision, minimap_merge_gap=1)
    assert find_player(mm, off) == (11, 45)     # 舊的錯誤答案
