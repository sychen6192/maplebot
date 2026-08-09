"""用組隊紅條找出角色在畫面上的精確位置。

「角色永遠在畫面正中央」是錯的：鏡頭有跟隨延遲、走到地圖邊緣還會卡住。
實測 1920 視窗上角色可以偏離中心 200px 以上，結果是角色自己被當成怪打、
攻擊範圍框也沒對準角色。
"""
import cv2
import numpy as np

from maplebot.vision.player_bar import LOWER_RED, find_player_bar

W, H = 790, 520
RED = (0, 0, 255)          # BGR 純紅，落在 HSV 遮罩範圍內


def _scene(bars=((400, 240, 30, 4),), w=W, h=H, color=RED):
    img = np.full((h, w, 3), 90, dtype=np.uint8)
    for x, y, bw, bh in bars:
        cv2.rectangle(img, (x, y), (x + bw, y + bh), color, -1)
    return img


def test_finds_the_party_bar():
    pos = find_player_bar(_scene([(400, 240, 30, 4)]))
    assert pos is not None
    assert abs(pos[0] - 412) <= 4      # 血條左上角 + 偏移
    assert abs(pos[1] - 280) <= 4


def test_no_party_means_no_position():
    """沒組隊就沒有紅條——回 None，決策層退回畫面中央（原本的行為）。"""
    assert find_player_bar(_scene([])) is None


def test_red_scenery_is_not_a_party_bar():
    """紅蘑菇、紅蝸牛殼都是紅的，但不是又扁又細的實心條。"""
    img = np.full((H, W, 3), 90, dtype=np.uint8)
    cv2.circle(img, (400, 240), 20, RED, -1)          # 圓的
    cv2.rectangle(img, (200, 300), (260, 340), RED, -1)   # 方塊
    assert find_player_bar(img) is None


def test_a_long_red_line_is_not_a_party_bar():
    assert find_player_bar(_scene([(50, 240, 400, 4)])) is None


def test_a_hollow_red_rectangle_is_rejected():
    """實心才算。空心框（UI 外框）不算。"""
    img = np.full((H, W, 3), 90, dtype=np.uint8)
    cv2.rectangle(img, (400, 240), (430, 245), RED, 1)
    assert find_player_bar(img) is None


def test_something_far_from_the_centre_is_not_trusted():
    """楓谷的鏡頭不會把角色甩到畫面邊緣，這種結果幾乎確定是誤判。"""
    assert find_player_bar(_scene([(20, 30, 30, 4)])) is None


def test_the_minimap_is_masked_out():
    """小地圖上其他玩家的紅點也是紅的，要先蓋掉。"""
    img = _scene([(60, 40, 20, 4)])          # 畫在小地圖區域內
    assert find_player_bar(img, mask_out=[(0, 0, 200, 120)]) is None


def test_scales_with_the_window():
    """1900px 寬的畫面上，同一條血條的像素尺寸是 2.4 倍。"""
    s = 1900 / 790
    big = _scene([(int(400 * s), int(240 * s), int(30 * s), int(4 * s))],
                 w=1900, h=int(H * s))
    pos = find_player_bar(big, scale=s)
    assert pos is not None
    assert abs(pos[0] - int(412 * s)) <= 10


def test_the_biggest_bar_wins():
    """自己一個人組隊只會有一條；真的有隊友時取最大的那條（HP 最滿）。"""
    pos = find_player_bar(_scene([(380, 240, 10, 4), (420, 250, 30, 4)]))
    assert pos is not None
    assert abs(pos[0] - 432) <= 4


def test_empty_frame_is_survived():
    assert find_player_bar(np.zeros((0, 0, 3), dtype=np.uint8)) is None


def test_hsv_range_is_the_reference_projects():
    """換算自 MapleStoryAutoLevelUp 的 [0,60,60]~[0,100,100]（0~360/0~100 制）。"""
    assert LOWER_RED == (0, 153, 153)
