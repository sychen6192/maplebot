"""跟隨物過濾：寵物跟著角色跑，怪不會。

最重要的不是「認得出寵物」，而是**不要把怪認成寵物**——判錯的代價是整場
都不攻擊，比判不出寵物嚴重得多。所以大半的測試在驗證各種「看起來像但其實
不是」的情況會安全地放棄判斷。
"""
import cv2
import numpy as np

from maplebot.vision.follower import FollowerFilter
from maplebot.vision.mobs import Mob

W, H = 800, 300
STEP = 160          # 每次計分之間鏡頭捲動的量（> min_shift_px）

_RNG = np.random.default_rng(3)
_BASE = cv2.GaussianBlur(
    _RNG.integers(0, 255, (H, W, 3), dtype=np.uint8), (5, 5), 0)


def _frame(pan: int) -> np.ndarray:
    """鏡頭往右捲 pan px：背景在畫面上往左移。"""
    return np.roll(_BASE, -pan, axis=1)


def _mob(cx, cy=150):
    return Mob(cx=int(cx), cy=int(cy), w=30, h=24, score=0.9, name="m")


def _f(**kw):
    kw.setdefault("hits_needed", 3)
    return FollowerFilter(**kw)


def test_pet_is_found_once_the_camera_actually_scrolls():
    """鏡頭在捲、怪跟著背景往左滑，只有寵物待在原地。"""
    f = _f()
    pet = 430
    for i in range(6):
        kept, followers = f.filter([_mob(pet), _mob(700 - i * STEP, cy=90)],
                                   _frame(i * STEP))
    assert [m.cx for m in followers] == [pet]
    assert len(kept) == 1


def test_needs_the_full_streak():
    f = _f()
    for i in range(3):
        kept, followers = f.filter([_mob(430), _mob(700 - i * STEP, cy=90)],
                                   _frame(i * STEP))
        assert not followers


def test_static_camera_classifies_nothing():
    """窄地圖鏡頭卡住不捲時，怪在畫面上也不會滑——完全不能下判斷。

    這就是「整場都不攻擊」的成因：所有東西看起來都跟著角色。
    """
    f = _f()
    mobs = [_mob(200), _mob(430), _mob(600)]
    for _ in range(20):
        kept, followers = f.filter(mobs, _frame(0), player_moved=True)
    assert len(kept) == 3 and not followers


def test_evenly_spaced_mobs_are_not_mistaken_for_followers():
    """一排等距的怪跟著鏡頭滑動後，正好落在彼此原本的位置上。

    「它沒動」和「隔壁那隻滑過來了」兩種解釋都成立 -> 不計分。
    舊版用最近鄰配對就是在這裡把整排怪判成寵物的。
    """
    f = _f()
    for i in range(8):
        row = [120 + j * STEP - i * STEP for j in range(-2, 8)]
        mobs = [_mob(x) for x in row if 0 <= x < W]
        kept, followers = f.filter(mobs, _frame(i * STEP))
    assert not followers


def test_a_crowd_of_verdicts_is_thrown_away():
    """寵物只有一隻。一次判出四隻代表判斷失準，寧可全部放行。"""
    f = _f(max_followers=2)
    still = [_mob(60), _mob(300), _mob(540), _mob(780)]
    for i in range(8):
        kept, followers = f.filter(still + [_mob(760 - i * STEP, cy=40)],
                                   _frame(i * STEP))
    assert not followers
    assert len(kept) == 5


def test_confirmed_follower_is_released_when_it_starts_sliding():
    """寵物收起來後、原地剛好站了一隻真的怪，也不會一直冤枉牠。"""
    f = _f()
    for i in range(6):
        f.filter([_mob(430), _mob(700 - i * STEP, cy=90)], _frame(i * STEP))
    assert f.filter([_mob(430)], _frame(6 * STEP))[1]      # 目前判定為跟隨物

    for i in range(6, 16):            # 那個位置的東西開始跟著鏡頭滑
        kept, followers = f.filter([_mob(430 - (i - 6) * STEP % W, cy=150)],
                                   _frame(i * STEP))
    assert not followers


def test_standing_still_never_classifies():
    f = _f()
    for i in range(10):
        kept, followers = f.filter([_mob(430)], _frame(0), player_moved=False)
    assert len(kept) == 1 and not followers


def test_without_a_frame_no_new_verdict():
    """遠端偵測失敗之類的情況沒有畫面可比，就別亂猜。"""
    f = _f()
    for _ in range(10):
        kept, followers = f.filter([_mob(430)], None)
    assert len(kept) == 1 and not followers


def test_flat_frames_are_survived():
    """全黑畫面（斷線/讀圖）相位相關會退化，不能炸也不能亂判。"""
    f = _f()
    blank = np.zeros((H, W, 3), dtype=np.uint8)
    for _ in range(5):
        kept, followers = f.filter([_mob(430)], blank)
    assert len(kept) == 1 and not followers


def test_scene_change_resets_the_anchor():
    """換地圖/換頻道後畫面完全不同，舊錨點沒有參考價值。"""
    f = _f()
    f.filter([_mob(430)], _frame(0))
    other = cv2.GaussianBlur(
        np.random.default_rng(99).integers(0, 255, (H, W, 3), dtype=np.uint8), (5, 5), 0)
    kept, followers = f.filter([_mob(430)], other)
    assert len(kept) == 1 and not followers
