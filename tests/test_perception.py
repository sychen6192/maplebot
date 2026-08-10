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
    frame2[60:68, 35:45] = (60, 60, 60)       # 血條掉到 50%
    st = p.perceive(frame2, now=0.1)
    assert st.hp == pytest.approx(0.5, abs=0.02)


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


# --- 跟隨物過濾（寵物）整合 ---
# 判別靠的是鏡頭捲動，所以測試畫面的 playfield 必須真的跟著角色捲動。

_BG = cv2.GaussianBlur(
    np.random.default_rng(11).integers(0, 255, (200, 300, 3), dtype=np.uint8), (5, 5), 0)
PAN_PER_MINIMAP_PX = 40        # 小地圖走一格 = 畫面捲 40px


def _walking_frame(frame, steps):
    """角色往右走 steps 格：小地圖黃點右移，playfield 背景左移。"""
    f = frame.copy()
    f[28:31, 38:41] = (150, 190, 205)                    # 擦掉原本的黃點
    f[28:31, 38 + steps:41 + steps] = (0, 255, 255)
    f[80:280, 0:300] = np.roll(_BG, -steps * PAN_PER_MINIMAP_PX, axis=1)
    return f


class _FixedDetector:
    """永遠回報同一個「畫面」位置——這正是寵物的行為。"""

    def detect(self, playfield):
        from maplebot.vision.mobs import Mob
        return [Mob(cx=150, cy=100, w=20, h=20, score=1.0, name="m")]


class _SlidingDetector:
    """角色往右走時，站在原地的怪會在畫面上往左滑。"""

    def __init__(self):
        self.step = 0

    def detect(self, playfield):
        from maplebot.vision.mobs import Mob
        self.step += 1
        # 跟背景一樣繞回來（測試用的背景是循環捲動的）
        return [Mob(cx=(280 - self.step * PAN_PER_MINIMAP_PX) % 300, cy=100,
                    w=20, h=20, score=1.0, name="m")]


def test_follower_filter_is_off_by_default(cfg):
    """判別需要鏡頭捲動，窄地圖鏡頭不捲——所以預設不開，避免把怪當寵物。"""
    assert cfg.vision.filter_followers is False


def test_pet_following_the_player_is_filtered_out(cfg, frame):
    cfg.vision.filter_followers = True
    cfg.vision.player_move_px = 1
    p = Perceiver(cfg, _FixedDetector())
    for i in range(14):
        st = p.perceive(_walking_frame(frame, i), now=float(i))
    assert st.mobs == []
    assert len(p.last_followers) == 1


def test_mob_that_slides_past_is_still_attacked(cfg, frame):
    cfg.vision.filter_followers = True
    cfg.vision.player_move_px = 1
    p = Perceiver(cfg, _SlidingDetector())
    for i in range(14):
        st = p.perceive(_walking_frame(frame, i), now=float(i))
    assert len(st.mobs) == 1
    assert not p.last_followers


def test_standing_still_never_filters_anything(cfg, frame):
    """角色沒移動、鏡頭沒捲，就無從判別——這時不能亂排除。"""
    cfg.vision.filter_followers = True
    p = Perceiver(cfg, _FixedDetector())
    for i in range(20):
        st = p.perceive(frame, now=float(i))
    assert len(st.mobs) == 1


def test_hp_flash_does_not_reach_the_decision_layer(cfg, frame):
    """被撞到時血條會閃，那一幀讀成 0%——不能傳到下游去灌藥又停機。"""
    p = Perceiver(cfg, _FixedDetector())
    assert p.perceive(frame, now=0.0).hp == pytest.approx(0.7, abs=0.02)
    flashed = frame.copy()
    flashed[60:68, 10:45] = (60, 60, 60)        # 血條被特效蓋掉
    assert p.perceive(flashed, now=0.1).hp == pytest.approx(0.7, abs=0.02)
    assert p.bar_glitches == 1
    # 恢復之後照常讀
    assert p.perceive(frame, now=0.2).hp == pytest.approx(0.7, abs=0.02)


def test_real_death_still_reaches_the_decision_layer(cfg, frame):
    p = Perceiver(cfg, _FixedDetector())
    p.perceive(frame, now=0.0)
    empty = frame.copy()
    empty[60:68, 10:45] = (60, 60, 60)
    p.perceive(empty, now=0.1)
    assert p.perceive(empty, now=0.2).hp == 0.0


class _BlindDetector:
    """什麼都抓不到——模擬描邊偵測漏掉的情況。"""

    def detect(self, playfield):
        return []


def _with_hp_bar(frame, x=120, y=120):
    """畫一條符合尺寸的怪物血條（這個 fixture 的 playfield 只有 300px 寬，
    所以血條也要照比例縮小）。"""
    from maplebot.vision.mob_hpbar import HP_BAR_BGR
    f = frame.copy()
    cv2.rectangle(f, (x, y), (x + 12, y + 2), HP_BAR_BGR, -1)
    return f


def test_hp_bar_finds_a_mob_the_outline_detector_missed(cfg, frame):
    """描邊漏掉的那幾幀，血條還在——打到一半不會跟丟。"""
    cfg.vision.detect_hp_bars = True
    p = Perceiver(cfg, _BlindDetector())
    st = p.perceive(_with_hp_bar(frame), now=1.0)
    assert len(st.mobs) == 1
    assert st.mobs[0].name == "hpbar"


def test_hp_bar_detection_is_off_by_default(cfg, frame):
    """長草的地圖會把整片草地判成血條，所以要先自檢過再開。"""
    assert cfg.vision.detect_hp_bars is False
    p = Perceiver(cfg, _BlindDetector())
    assert p.perceive(_with_hp_bar(frame), now=1.0).mobs == []


def test_hp_bar_coordinates_are_playfield_relative(cfg, frame):
    """playfield ROI 是 (0,80,300,200)，血條畫在畫面的 y=120 -> playfield 的 y=40。"""
    cfg.vision.detect_hp_bars = True
    p = Perceiver(cfg, _BlindDetector())
    mob = p.perceive(_with_hp_bar(frame, x=120, y=120), now=1.0).mobs[0]
    assert abs(mob.cx - 126) <= 5
    assert 40 < mob.cy < 120        # 在血條下方，且還在 playfield 裡


def test_hp_bar_survives_the_search_box_offset(cfg, frame):
    """只搜角色周圍時，血條的座標也要換算回 playfield。"""
    cfg.vision.detect_hp_bars = True
    cfg.vision.mob_search_box = (200, 120)
    p = Perceiver(cfg, _BlindDetector())
    mobs = p.perceive(_with_hp_bar(frame, x=120, y=170), now=1.0).mobs
    assert len(mobs) == 1
    assert 100 < mobs[0].cx < 200


def _with_party_bar(frame, x=140, y=170):
    """在 playfield 內畫一條組隊紅條（playfield ROI 是 (0,80,300,200)）。"""
    f = frame.copy()
    cv2.rectangle(f, (x, y), (x + 11, y + 2), (0, 0, 255), -1)
    return f


class _MobAtDetector:
    """固定回報指定 playfield 座標的一隻怪。"""

    def __init__(self, cx, cy):
        self.cx, self.cy = cx, cy

    def detect(self, playfield):
        from maplebot.vision.mobs import Mob
        return [Mob(cx=self.cx, cy=self.cy, w=20, h=20, score=1.0, name="m")]


def test_party_bar_gives_the_character_position(cfg, frame):
    p = Perceiver(cfg, _BlindDetector())
    st = p.perceive(_with_party_bar(frame), now=1.0)
    assert st.player_screen is not None
    assert abs(st.player_screen[0] - 145) <= 8      # 血條左上 + 偏移
    assert st.player_screen[1] > 90                 # 角色在血條下面


def test_no_party_bar_leaves_it_unknown(cfg, frame):
    """沒組隊就沒紅條，決策層退回畫面中央（原本的行為）。"""
    assert Perceiver(cfg, _BlindDetector()).perceive(frame, now=1.0).player_screen is None


def test_the_character_itself_is_not_reported_as_a_mob(cfg, frame):
    """描邊偵測是照畫面中央挖掉自己的；角色偏離中央時會把自己當成怪。"""
    shot = _with_party_bar(frame, x=140, y=170)
    p = Perceiver(cfg, _MobAtDetector(cx=145, cy=105))   # 正好在角色身上
    assert p.perceive(shot, now=1.0).mobs == []


def test_a_real_mob_next_to_the_character_still_counts(cfg, frame):
    shot = _with_party_bar(frame, x=140, y=170)
    p = Perceiver(cfg, _MobAtDetector(cx=280, cy=105))
    assert len(p.perceive(shot, now=1.0).mobs) == 1


def test_player_bar_lookup_can_be_turned_off(cfg, frame):
    cfg.vision.locate_player_bar = False
    st = Perceiver(cfg, _BlindDetector()).perceive(_with_party_bar(frame), now=1.0)
    assert st.player_screen is None


def _with_nametag(frame, x=200, y=210):
    """在 playfield 內畫一塊角色名牌（playfield ROI 是 (0,80,300,200)）。"""
    f = frame.copy()
    cv2.rectangle(f, (x, y), (x + 40, y + 10), (35, 35, 35), -1)
    for i in range(4):
        cv2.rectangle(f, (x + 3 + i * 10, y + 2), (x + 9 + i * 10, y + 8),
                      (240, 240, 240), -1)
    return f


def _save_nametag(cfg, frame, x=200, y=210):
    import os
    os.makedirs(cfg.vision.ui_templates_dir, exist_ok=True)
    tagged = _with_nametag(frame, x, y)
    px, py = cfg.regions["playfield"][:2]
    cv2.imwrite(os.path.join(cfg.vision.ui_templates_dir, "player_nametag.png"),
                tagged[y:y + 10, x:x + 40])
    return tagged


def test_nametag_locates_the_character(cfg, frame):
    shot = _save_nametag(cfg, frame)
    st = Perceiver(cfg, _BlindDetector()).perceive(shot, now=1.0)
    assert st.player_screen is not None
    assert abs(st.player_screen[0] - 220) <= 3     # 名牌中心 x


def test_nametag_wins_over_the_party_bar(cfg, frame):
    """名牌不用進遊戲組隊就有，兩個都抓得到時以它為準。"""
    shot = _with_party_bar(_save_nametag(cfg, frame, x=60, y=210), x=250, y=170)
    st = Perceiver(cfg, _BlindDetector()).perceive(shot, now=1.0)
    assert st.player_screen is not None
    assert abs(st.player_screen[0] - 80) <= 3      # 名牌那邊，不是紅條那邊


def test_party_bar_still_used_without_a_nametag_template(cfg, frame):
    st = Perceiver(cfg, _BlindDetector()).perceive(_with_party_bar(frame), now=1.0)
    assert st.player_screen is not None


def test_ui_overlays_are_not_searched_for_mobs(cfg, frame):
    """小地圖面板疊在主畫面上，它的標題文字有黑色描邊會被當成怪。"""
    cfg.vision.mob_exclude = [(0, 80, 120, 60)]    # client 座標，蓋住 playfield 左上
    seen = {}

    class _Recorder:
        def detect(self, playfield):
            seen["roi"] = playfield.copy()
            return []

    Perceiver(cfg, _Recorder()).perceive(frame, now=1.0)
    assert (seen["roi"][0:60, 0:120] == 128).all()     # 被塗掉了
    assert not (seen["roi"][0:60, 130:200] == 128).all()  # 框外沒被動到
