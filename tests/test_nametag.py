"""角色名牌定位：換背景還認得出自己，別人的名字不會被誤認。"""
import cv2
import numpy as np
import pytest

from maplebot.vision.nametag import NametagLocator, load_locator

TAG_W, TAG_H = 90, 22


def _tag(seed: int, bg=(40, 40, 40)) -> np.ndarray:
    """做一塊名牌：半透明暗底 + 亮色筆畫。seed 不同 = 不同名字。

    不同的名字要真的長得不一樣（字數不同、筆畫位置不同）。原本每個 seed 都
    畫六個等寬方塊、只有高度差一點，兩個「名字」相關性高到分不開——那是
    素材做得不像，不是認人的邏輯有問題：實拍畫面裡自己的名牌 0.784、
    旁邊那位路人的連 0.537 都不到。
    """
    tag = np.full((TAG_H, TAG_W, 3), bg, dtype=np.uint8)
    rng = np.random.default_rng(seed)
    x = 4
    for _ in range(int(rng.integers(3, 7))):          # 字數不一樣
        cw = int(rng.integers(8, 16))
        if x + cw >= TAG_W - 2:
            break
        for _ in range(int(rng.integers(2, 5))):      # 每個字的筆畫也不一樣
            y1 = int(rng.integers(3, TAG_H - 6))
            cv2.line(tag, (x, y1), (x + cw - 1, int(rng.integers(3, TAG_H - 4))),
                     (235, 235, 235), 1)
        cv2.rectangle(tag, (x, 4), (x + cw - 2, TAG_H - 6), (210, 210, 210), 1)
        x += cw + int(rng.integers(1, 4))
    return tag


def _scene(tag, at=(300, 260), bg_value=90, size=(400, 700)):
    h, w = size
    img = np.full((h, w, 3), bg_value, dtype=np.uint8)
    x, y = at
    # 底是半透明的：畫面背景會透出來，所以用混合而不是直接蓋上去
    th, tw = tag.shape[:2]      # 用這塊圖自己的大小，縮放過的名牌才貼得進去
    patch = img[y:y + th, x:x + tw].astype(np.float32)
    img[y:y + th, x:x + tw] = (patch * 0.35 + tag.astype(np.float32) * 0.65) \
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
    """名牌底是半透明的，背景會透出來，分數會跟著掉（實測 1.00 -> 0.86）。

    分數低沒關係，**跟背景分得開**才要緊：實拍畫面裡自己的名牌 0.784、
    整張圖其他地方最高 0.545。門檻就是照這個差距訂的。
    """
    tag = _tag(1)
    loc = NametagLocator(tag, offset=(0, -24))
    assert loc.locate(_scene(tag, at=(220, 300), bg_value=bg), scale=1.0) is not None
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


def test_matches_after_the_window_resolution_changed():
    """楓谷的 UI 跟著視窗縮放：1920 截的模板放到 1366 的畫面上會大 1.4 倍。

    實測沒處理的話最佳分數只有 0.506，而且還不在名牌上——等於完全認不得。
    """
    tag = _tag(1)
    small = cv2.resize(tag, None, fx=0.7, fy=0.7, interpolation=cv2.INTER_AREA)
    scene = _scene(small, at=(300, 260))
    loc = NametagLocator(tag, offset=(0, -24))     # 模板還是原本的大小
    found = loc.locate(scene, scale=1.0)
    assert found is not None, loc.last_score
    assert abs(loc.scale_used - 0.7) < 0.1, loc.scale_used
    assert abs(found[0] - (300 + small.shape[1] // 2)) <= 3


def test_uses_the_recorded_capture_width_when_it_is_known(tmp_path):
    """模板旁邊記了截圖當下的 playfield 寬度，就不用自己掃。"""
    from maplebot.vision.nametag import save_width

    tag = _tag(1)
    path = str(tmp_path / "player_feature.png")
    cv2.imwrite(path, tag)
    save_width(path, 1000)
    loc = load_locator(str(tmp_path), offset=(0, -24))
    assert loc.template_width == 1000

    small = cv2.resize(tag, None, fx=0.7, fy=0.7, interpolation=cv2.INTER_AREA)
    scene = _scene(small, at=(200, 200), size=(400, 700))
    loc.locate(scene, scale=1.0)
    assert abs(loc.scale_used - 0.7) < 0.01      # 700/1000，直接算出來


def test_a_rescale_only_happens_once():
    tag = _tag(1)
    loc = NametagLocator(tag, offset=(0, -24))
    scene = _scene(tag, at=(300, 260))
    loc.locate(scene, scale=1.0)
    first = loc._tpl
    loc.locate(scene, scale=1.0)
    assert loc._tpl is first


def test_finds_the_character_at_the_edge_of_the_screen():
    """走到地圖邊緣時鏡頭會停住不跟了，角色就是會跑到畫面邊邊去——

    那正是這個功能存在的理由。曾經為了省時間只搜尋中央 70%，剛好把這個情況
    挖掉：實測角色在 1366 寬的畫面上跑到 x=205（離左緣 15%）就找不到，
    於是又被自己的描邊偵測當成一隻怪。
    """
    tag = _tag(1)
    for at in ((6, 300), (700 - TAG_W - 6, 300), (300, 6), (300, 400 - TAG_H - 6)):
        loc = NametagLocator(tag, offset=(0, 0))
        found = loc.locate(_scene(tag, at=at), scale=1.0)
        assert found is not None, (at, loc.last_score)
        assert abs(found[0] - (at[0] + TAG_W // 2)) <= 3, (at, found)
