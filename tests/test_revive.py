"""死亡復活對話框偵測：抓得到並排的兩顆確定/取消鈕，正常畫面不誤報。"""
import numpy as np

from maplebot.vision.revive import find_confirm_button


def _dialog(w=790, h=520, btn=(28, 10), gap=14, cy_ratio=0.55, one_button=False):
    """畫一張中央有復活對話框的畫面：一對並排橘鈕（確定＋取消）。"""
    img = np.full((h, w, 3), 90, dtype=np.uint8)     # 藍灰底
    img[:, :, 0] = 150
    bw, bh = btn
    cx, cy = w // 2, int(h * cy_ratio)
    # 確定（左）、取消（右）——BGR 橘 (94,149,222)
    left = cx - gap // 2 - bw
    for i, x in enumerate((left, cx + gap // 2)):
        if one_button and i == 1:
            break
        img[cy:cy + bh, x:x + bw] = (94, 149, 222)
        # 白色文字把中間挖空一點（模擬「確定」兩字）
        img[cy + 2:cy + bh - 2, x + bw // 2 - 1:x + bw // 2 + 1] = (240, 240, 240)
    return img, (left + bw // 2, cy + bh // 2)


def test_finds_confirm_button_in_death_dialog():
    img, (bx, by) = _dialog()
    got = find_confirm_button(img, scale=1.0)
    assert got is not None
    assert abs(got[0] - bx) <= 6 and abs(got[1] - by) <= 6


def test_returns_the_left_button_not_the_right():
    img, (bx, by) = _dialog()
    got = find_confirm_button(img, scale=1.0)
    assert got[0] < img.shape[1] // 2      # 確定在中線左邊


def test_normal_frame_has_no_dialog():
    img = np.full((520, 790, 3), 90, dtype=np.uint8)
    img[:, :, 0] = 150
    assert find_confirm_button(img, scale=1.0) is None


def test_a_single_orange_blob_is_not_a_dialog():
    """單顆橘塊（場景美術）不算——要成對才是對話框。"""
    img, _ = _dialog(one_button=True)
    assert find_confirm_button(img, scale=1.0) is None


def test_scales_with_resolution():
    img, (bx, by) = _dialog(w=2560, h=1440, btn=(90, 32), gap=45)
    got = find_confirm_button(img, scale=2560 / 790)
    assert got is not None
    assert abs(got[0] - bx) <= 12 and abs(got[1] - by) <= 12


def test_empty_frame_is_safe():
    assert find_confirm_button(np.zeros((0, 0, 3), dtype=np.uint8)) is None


def test_orange_only_at_top_edge_is_ignored():
    """頂部公告 UI 的橘色（偏離中央）不該被當成對話框。"""
    img = np.full((520, 790, 3), 90, dtype=np.uint8)
    img[:, :, 0] = 150
    # 畫面最上方一對橘塊（模擬任務指引/公告），在垂直搜尋範圍外
    for x in (300, 340):
        img[6:16, x:x + 28] = (94, 149, 222)
    assert find_confirm_button(img, scale=1.0) is None
