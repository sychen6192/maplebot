"""UI 模板比對：彩色、限定 ROI、分數方向一致。

這一層存在的理由是「遊戲自己畫的 UI 要比顏色」——灰階會把黃點跟青點壓成
同一個值，而小地圖上那正是玩家與其他玩家的差別。
"""
import cv2
import numpy as np
import pytest

from maplebot.vision import match


def _scene():
    """淡色底，上面放一顆黃點跟一顆青點——灰階下兩者亮度幾乎一樣。"""
    img = np.full((80, 120, 3), (120, 120, 120), dtype=np.uint8)
    img[20:26, 30:36] = _yellow_tpl()      # 黃
    img[50:56, 90:96] = _cyan_tpl()        # 青（灰階下亮度幾乎一樣）
    return img


def _yellow_tpl():
    """黃色菱形 + 深色描邊——真實的小地圖玩家點就長這樣，不是純色方塊。"""
    t = np.full((6, 6, 3), (40, 40, 40), dtype=np.uint8)
    t[1:5, 1:5] = (0, 255, 255)
    t[0, 0] = t[0, 5] = t[5, 0] = t[5, 5] = (20, 20, 20)
    return t


def _cyan_tpl():
    t = _yellow_tpl().copy()
    t[1:5, 1:5] = (255, 255, 0)
    return t


def test_colour_tells_yellow_from_cyan():
    """這就是改用彩色的整個理由。"""
    img = _scene()
    hit = match.match_center(img, _yellow_tpl(), threshold=0.5, use_color=True)
    assert hit == (33, 23)                 # 黃點，不是青點


def _separation(img, tpl, use_color):
    """命中分數與「另一個顏色那顆點」的分數差多少。

    差距才是能不能分辨的指標，絕對分數不是——nametag.py 已經為這件事踩過
    一次坑（遮罩版分數 0.98 但整張圖到處都是 0.96）。
    """
    hit = match.match(img, tpl, use_color=use_color)
    other = match.match(img, tpl, roi=(84, 44, 24, 24), use_color=use_color)
    return hit[0] - other[0]


def test_colour_separates_far_better_than_greyscale():
    """對照組：灰階分得出來，但差距小得多——這是改用彩色的量化理由。

    灰階把 (0,255,255) 壓成 226、(255,255,0) 壓成 179，還有一點差別，
    所以不是「完全分不出」；但顏色本身的差異被壓掉大半。
    """
    img = _scene()
    tpl = _yellow_tpl()
    colour = _separation(img, tpl, use_color=True)
    grey = _separation(img, tpl, use_color=False)
    assert colour > grey * 1.5, f"彩色 {colour:.3f} 應明顯勝過灰階 {grey:.3f}"


def test_roi_limits_the_search():
    """對應商業版的 td_detect_in_roi：位置已知就不必掃全畫面。"""
    img = _scene()
    # 只看右半邊：黃點在左邊，所以應該找不到夠像的
    assert match.match_center(img, _yellow_tpl(), threshold=0.9,
                              roi=(60, 0, 60, 80)) is None
    # 框住黃點就找得到，而且座標是**原圖**的，不是 ROI 內的
    assert match.match_center(img, _yellow_tpl(), threshold=0.5,
                              roi=(20, 10, 40, 40)) == (33, 23)


def test_roi_outside_the_image_is_survived():
    assert match.match(_scene(), _yellow_tpl(), roi=(500, 500, 10, 10)) is None


def test_template_bigger_than_image_is_survived():
    big = np.zeros((200, 200, 3), dtype=np.uint8)
    assert match.match(_scene(), big) is None


def test_a_flat_colour_template_does_not_divide_by_zero():
    """純色模板的 CCOEFF 要除以模板標準差，而那是 0。

    OpenCV 不報錯，回一整片高分、命中點落在 (0,0)——看起來像比中了。
    這種模板必須自動改走絕對差。
    """
    img = _scene()
    flat = np.full((6, 6, 3), (0, 255, 255), dtype=np.uint8)
    hit = match.match(img, flat)
    assert hit is not None and hit[1] != (0, 0)
    # 6x6 的純色模板蓋在 4x4 的黃色內部上，位置不會剛好對齊，但一定落在
    # 黃點附近而不是畫面角落
    assert abs(hit[1][0] - 31) <= 3 and abs(hit[1][1] - 21) <= 3


def test_score_is_always_bigger_is_better():
    """sqdiff 天生越小越像，這裡要翻成跟 ccoeff 同一個方向。

    兩種方向混在一起遲早有人把門檻比反，那種 bug 從症狀完全看不出來。
    """
    img = _scene()
    tpl = _yellow_tpl()
    for m in ("ccoeff", "sqdiff"):
        hit = match.match(img, tpl, method=m)
        assert hit is not None
        assert hit[0] > 0.9, f"{m} 完全一樣的地方應該接近 1.0"
        assert hit[1] == (30, 20)


def test_unknown_method_is_rejected_loudly():
    with pytest.raises(ValueError, match="未知的比對方法"):
        match.match(_scene(), _yellow_tpl(), method="ssd")


def test_a_greyscale_template_still_works():
    """舊模板（或測試）給灰階圖時要自動兩邊都走灰階，不能炸掉。"""
    img = _scene()
    gt = cv2.cvtColor(_yellow_tpl(), cv2.COLOR_BGR2GRAY)
    assert match.match(img, gt) is not None
