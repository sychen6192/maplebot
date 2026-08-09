"""靠怪物頭上的綠色血條找怪（借自 MapleStoryAutoLevelUp 的 with_enemy_hp_bar）。

跟描邊偵測互補：描邊是猜的（門檻調錯就漏），血條是遊戲畫的 UI，
只在怪被打過之後出現，但一出現就一定有怪。
"""
import cv2
import numpy as np

from maplebot.vision.mob_hpbar import HP_BAR_BGR, find_hp_bars, merge
from maplebot.vision.mobs import Mob


def _scene(bars=((200, 100),), w=790, h=520, bar_w=40, bar_h=4, color=HP_BAR_BGR):
    img = np.full((h, w, 3), 90, dtype=np.uint8)
    for x, y in bars:
        cv2.rectangle(img, (x, y), (x + bar_w, y + bar_h), color, -1)
    return img


def test_one_hp_bar_becomes_one_mob():
    mobs = find_hp_bars(_scene([(200, 100)]))
    assert len(mobs) == 1
    assert abs(mobs[0].cx - 220) <= 4          # 血條中心的正下方
    assert mobs[0].cy > 100                    # 身體在血條下面
    assert mobs[0].name == "hpbar"


def test_several_bars_several_mobs():
    assert len(find_hp_bars(_scene([(100, 80), (400, 200), (600, 150)]))) == 3


def test_plain_scene_finds_nothing():
    assert find_hp_bars(np.full((200, 400, 3), 90, dtype=np.uint8)) == []


def test_grass_green_is_not_a_health_bar():
    """實際踩到的：草地綠跟血條綠只差 17~24 階，容差 25 就把整片草地判成怪。

    這個訊號唯一的價值就是「那個綠只有血條會出現」，所以預設精確比色。
    """
    for grass in [(95, 205, 80), (88, 214, 72), (70, 180, 60)]:
        assert find_hp_bars(_scene([(200, 100)], color=grass)) == [], grass


def test_a_textured_green_patch_is_not_a_health_bar():
    """草地是有紋理的，血條是實心矩形。"""
    rng = np.random.default_rng(3)
    img = np.full((520, 790, 3), 90, dtype=np.uint8)
    patch = rng.integers(0, 2, (60, 300)).astype(bool)      # 半數像素剛好同色
    region = img[300:360, 200:500]
    region[patch] = HP_BAR_BGR
    assert find_hp_bars(img) == []


def test_a_long_green_strip_is_not_a_health_bar():
    """草地邊緣是一長條，血條只有幾十 px 寬。"""
    assert find_hp_bars(_scene([(50, 300)], bar_w=700, bar_h=5)) == []


def test_a_completely_different_green_is_ignored():
    assert find_hp_bars(_scene([(200, 100)], color=(20, 255, 20))) == []


def test_vertical_green_strips_are_ignored():
    """血條是橫的。直的綠色色塊是別的東西（草、UI 邊框）。"""
    assert find_hp_bars(_scene([(200, 100)], bar_w=4, bar_h=40)) == []


def test_tiny_specks_are_ignored():
    assert find_hp_bars(_scene([(200, 100)], bar_w=1, bar_h=1)) == []


def test_a_tall_green_block_is_ignored():
    """血條很扁。高度超過範圍的一律不算。"""
    assert find_hp_bars(_scene([(200, 100)], bar_w=40, bar_h=40)) == []


def test_boxes_scale_with_the_window():
    small = find_hp_bars(_scene([(200, 100)]), scale=1.0)[0]
    big = find_hp_bars(_scene([(200, 100)], w=1900, h=1250), scale=1900 / 790)[0]
    assert big.w > small.w * 2


def test_merge_keeps_the_outline_box_when_they_overlap():
    """描邊框是量出來的，比猜的準——重疊時留描邊那個。"""
    outline = Mob(cx=200, cy=150, w=60, h=50, score=1.0, name="mob")
    hpbar = Mob(cx=205, cy=155, w=70, h=60, score=1.0, name="hpbar")
    merged = merge([outline], [hpbar])
    assert [m.name for m in merged] == ["mob"]


def test_merge_adds_the_ones_outline_missed():
    outline = Mob(cx=200, cy=150, w=60, h=50, score=1.0, name="mob")
    missed = Mob(cx=600, cy=300, w=70, h=60, score=1.0, name="hpbar")
    merged = merge([outline], [missed])
    assert sorted(m.name for m in merged) == ["hpbar", "mob"]


def test_merge_with_nothing_extra_is_a_passthrough():
    outline = [Mob(cx=1, cy=2, w=3, h=4, score=1.0, name="mob")]
    assert merge(outline, []) == outline
