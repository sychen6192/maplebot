"""角色名牌定位：換背景還認得出自己，別人的名字不會被誤認。"""
import cv2
import numpy as np
import pytest

from maplebot.vision.nametag import NametagLocator, load_locator

TAG_W, TAG_H = 90, 22


def _tag(seed: int, bg=(40, 40, 40)) -> np.ndarray:
    """做一塊名牌：半透明暗底 + 亮色筆畫。seed 不同 = 不同名字。"""
    tag = np.full((TAG_H, TAG_W, 3), bg, dtype=np.uint8)
    rng = np.random.default_rng(seed)
    for i in range(6):
        x = 6 + i * 14
        y = rng.integers(4, 8)
        cv2.rectangle(tag, (x, y), (x + 9, y + 10), (235, 235, 235), -1)
        cv2.line(tag, (x, y + int(rng.integers(2, 8))), (x + 9, y + 5),
                 (200, 200, 200), 1)
    return tag


def _scene(tag, at=(300, 260), bg_value=90, size=(400, 700)):
    h, w = size
    img = np.full((h, w, 3), bg_value, dtype=np.uint8)
    x, y = at
    # 底是半透明的：畫面背景會透出來，所以用混合而不是直接蓋上去
    patch = img[y:y + TAG_H, x:x + TAG_W].astype(np.float32)
    img[y:y + TAG_H, x:x + TAG_W] = (patch * 0.35 + tag.astype(np.float32) * 0.65) \
        .astype(np.uint8)
    return img


def test_finds_the_character_from_its_nametag():
    tag = _tag(1)
    loc = NametagLocator(tag, offset=(0, -24))
    found = loc.locate(_scene(tag, at=(300, 260)), scale=1.0)
    assert found is not None
    assert abs(found[0] - (300 + TAG_W // 2)) <= 2
    assert abs(found[1] - (260 + TAG_H // 2 - 24)) <= 2


@pytest.mark.parametrize("bg", [30, 90, 150, 210])
def test_survives_a_different_background(bg):
    """名牌底是半透明的，背景會透出來。

    直接比整塊在換背景時分數會掉一大截（實測 1.00 -> 0.86），所以實作只比
    文字筆畫。這個測試就是在釘住那件事：同一張模板走過草地、乾草、天空
    都還要認得出來。
    """
    tag = _tag(1)
    loc = NametagLocator(tag, offset=(0, -24))
    assert loc.locate(_scene(tag, at=(220, 300), bg_value=bg), scale=1.0) is not None
    # 實拍畫面最低 0.969；這裡的合成底比實際更極端，只要求穩穩過門檻
    assert loc.last_score >= loc.threshold + 0.01


def test_another_players_nametag_is_not_me():
    """名牌上是自己的角色名，別人的名字不一樣——這招天生只會找到自己。"""
    loc = NametagLocator(_tag(1), offset=(0, -24))
    assert loc.locate(_scene(_tag(99), at=(300, 260)), scale=1.0) is None


def test_nothing_found_when_the_tag_is_absent():
    loc = NametagLocator(_tag(1), offset=(0, -24))
    blank = np.full((400, 700, 3), 90, dtype=np.uint8)
    assert loc.locate(blank, scale=1.0) is None


def test_offset_scales_with_the_window():
    """offset 是以 790px 寬為基準的，畫面放大兩倍時位移也要放大兩倍。"""
    tag = _tag(1)
    loc = NametagLocator(tag, offset=(0, -20))
    found = loc.locate(_scene(tag, at=(300, 260)), scale=2.0)
    assert found is not None
    assert found[1] == 260 + TAG_H // 2 - 40


def test_missing_template_file_is_not_an_error(tmp_path):
    """沒截模板的人要照樣能跑（退回組隊紅條 / 畫面中央）。"""
    assert load_locator(str(tmp_path)) is None


def test_loads_a_saved_template(tmp_path):
    cv2.imwrite(str(tmp_path / "player_nametag.png"), _tag(1))
    loc = load_locator(str(tmp_path), offset=(0, -24))
    assert loc is not None
    assert loc.locate(_scene(_tag(1), at=(300, 260)), scale=1.0) is not None


def test_second_frame_uses_local_search_and_tracks_movement():
    """第二幀走局部搜尋（上次位置附近）也要找得到、座標要跟著角色走。"""
    tag = _tag(1)
    loc = NametagLocator(tag, offset=(0, 0))
    p1 = loc.locate(_scene(tag, at=(300, 260)), scale=1.0)
    p2 = loc.locate(_scene(tag, at=(322, 266)), scale=1.0)   # 走了一步
    assert p1 is not None and p2 is not None
    assert abs(p2[0] - (322 + TAG_W // 2)) <= 2
    assert abs(p2[1] - (266 + TAG_H // 2)) <= 2


def test_local_miss_falls_back_to_full_search():
    """名牌大幅跳離上次位置（換頻道、被擋一陣子）時要退回全域搜尋找到它。"""
    tag = _tag(1)
    loc = NametagLocator(tag, offset=(0, 0))
    assert loc.locate(_scene(tag, at=(150, 100)), scale=1.0) is not None
    far = loc.locate(_scene(tag, at=(480, 280)), scale=1.0)  # 跳很遠
    assert far is not None
    assert abs(far[0] - (480 + TAG_W // 2)) <= 2


def test_local_state_clears_when_tag_disappears():
    """名牌整幀不見（死亡/讀圖）後再出現，仍要能重新找到。"""
    tag = _tag(1)
    loc = NametagLocator(tag, offset=(0, 0))
    assert loc.locate(_scene(tag, at=(300, 260)), scale=1.0) is not None
    blank = np.full((400, 700, 3), 90, dtype=np.uint8)
    assert loc.locate(blank, scale=1.0) is None
    again = loc.locate(_scene(tag, at=(200, 180)), scale=1.0)
    assert again is not None
