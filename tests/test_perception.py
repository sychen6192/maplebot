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


class _CountingDetector:
    """記錄被呼叫幾次，用來驗證降頻有生效。"""

    def __init__(self):
        self.calls = 0

    def detect(self, playfield):
        self.calls += 1
        from maplebot.vision.mobs import Mob
        return [Mob(cx=self.calls, cy=1, w=2, h=2, score=0.9, name="m")]


def test_mob_interval_reuses_last_result(cfg, frame):
    cfg.vision.mob_interval = 0.5
    det = _CountingDetector()
    p = Perceiver(cfg, det)

    s1 = p.perceive(frame, now=10.0)          # 第一次一定要偵測
    s2 = p.perceive(frame, now=10.2)          # 還沒到間隔，沿用
    s3 = p.perceive(frame, now=10.6)          # 超過 0.5s，重新偵測

    assert det.calls == 2
    assert s1.mobs[0].cx == 1 and s2.mobs[0].cx == 1   # 沿用同一份結果
    assert s3.mobs[0].cx == 2


def test_mob_interval_zero_detects_every_tick(cfg, frame):
    cfg.vision.mob_interval = 0.0
    det = _CountingDetector()
    p = Perceiver(cfg, det)
    for i in range(3):
        p.perceive(frame, now=10.0 + i * 0.01)
    assert det.calls == 3


def test_hp_still_read_every_tick_while_mobs_throttled(cfg, frame):
    """降頻只影響怪物偵測；HP 這類攸關安全的辨識照樣每 tick 更新。"""
    cfg.vision.mob_interval = 10.0
    p = Perceiver(cfg, _CountingDetector())
    p.perceive(frame, now=0.0)
    frame2 = frame.copy()
    frame2[60:68, 10:45] = (60, 60, 60)       # 血條清空
    st = p.perceive(frame2, now=0.1)
    assert st.hp == 0.0


class _RecordingDetector:
    """記錄收到的影像尺寸，並固定回報影像中心的一隻怪。"""

    def __init__(self):
        self.seen_shape = None

    def detect(self, playfield):
        from maplebot.vision.mobs import Mob
        self.seen_shape = playfield.shape[:2]
        h, w = playfield.shape[:2]
        return [Mob(cx=w // 2, cy=h // 2, w=10, h=10, score=1.0, name="m")]


def test_search_box_crops_and_restores_coordinates(cfg, frame):
    """只搜角色周圍，但回報的座標要是 playfield 座標。"""
    cfg.regions["playfield"] = (0, 80, 300, 200)
    cfg.vision.mob_search_box = (100, 60)
    det = _RecordingDetector()

    st = Perceiver(cfg, det).perceive(frame, now=1.0)

    assert det.seen_shape == (60, 100)          # 偵測器只看到小框
    # 小框中心 (50,30) + 偏移 (100,70) = playfield 的中心 (150,100)
    assert (st.mobs[0].cx, st.mobs[0].cy) == (150, 100)


def test_no_search_box_uses_whole_playfield(cfg, frame):
    cfg.regions["playfield"] = (0, 80, 300, 200)
    cfg.vision.mob_search_box = None
    det = _RecordingDetector()
    st = Perceiver(cfg, det).perceive(frame, now=1.0)
    assert det.seen_shape == (200, 300)
    assert (st.mobs[0].cx, st.mobs[0].cy) == (150, 100)


def test_search_box_larger_than_playfield_is_clamped(cfg, frame):
    cfg.regions["playfield"] = (0, 80, 300, 200)
    cfg.vision.mob_search_box = (9999, 9999)
    det = _RecordingDetector()
    Perceiver(cfg, det).perceive(frame, now=1.0)
    assert det.seen_shape == (200, 300)


def test_region_out_of_bounds_gives_none(cfg, frame, tmp_path):
    cfg.regions["hp_bar"] = (280, 290, 50, 8)   # 超出 300x300 畫面
    cfg.regions["playfield"] = (0, 80, 400, 400)
    det = TemplateMobDetector(str(tmp_path), threshold=0.8)

    st = Perceiver(cfg, det).perceive(frame, now=1.0)

    assert st.hp is None
    assert st.mobs == []
    assert not st.vision_ok


def _move_player(frame, dx):
    """把小地圖上的玩家黃點往右挪 dx px（模擬角色走路）。"""
    f = frame.copy()
    f[28:31, 38:41] = (150, 190, 205)              # 擦掉原本的點
    f[28:31, 38 + dx:41 + dx] = (0, 255, 255)
    return f


class _FixedDetector:
    """永遠回報同一個「畫面」位置——這正是寵物的行為。"""

    def detect(self, playfield):
        from maplebot.vision.mobs import Mob
        return [Mob(cx=150, cy=100, w=20, h=20, score=1.0, name="m")]


class _SlidingDetector:
    """角色往右走時，站著不動的怪會在畫面上往左滑。"""

    def __init__(self):
        self.step = 0

    def detect(self, playfield):
        from maplebot.vision.mobs import Mob
        self.step += 1
        return [Mob(cx=250 - self.step * 45, cy=100, w=20, h=20, score=1.0, name="m")]


def test_pet_following_the_player_is_filtered_out(cfg, frame):
    """角色走了 4 步，牠在畫面上完全沒動 -> 判定成寵物，不列入怪物清單。"""
    cfg.vision.follower_hits = 3
    p = Perceiver(cfg, _FixedDetector())
    for i in range(5):
        st = p.perceive(_move_player(frame, i * 3), now=float(i))
    assert st.mobs == []
    assert len(p.last_followers) == 1


def test_mob_that_slides_past_is_still_attacked(cfg, frame):
    cfg.vision.follower_hits = 3
    p = Perceiver(cfg, _SlidingDetector())
    for i in range(5):
        st = p.perceive(_move_player(frame, i * 3), now=float(i))
    assert len(st.mobs) == 1
    assert not p.last_followers


def test_standing_still_never_filters_anything(cfg, frame):
    """角色沒移動就無從判別，這時不能亂排除。"""
    cfg.vision.follower_hits = 3
    p = Perceiver(cfg, _FixedDetector())
    for i in range(20):
        st = p.perceive(frame, now=float(i))
    assert len(st.mobs) == 1


def test_follower_filter_can_be_turned_off(cfg, frame):
    cfg.vision.filter_followers = False
    p = Perceiver(cfg, _FixedDetector())
    for i in range(5):
        st = p.perceive(_move_player(frame, i * 3), now=float(i))
    assert len(st.mobs) == 1
