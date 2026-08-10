"""playfield 共用前處理：疊在畫面上的 UI、角色自己那一塊。"""
import numpy as np

from maplebot.vision.mobs import Mob
from maplebot.vision.playfield import (OVERLAY_GRAY, blank_rects, drop_at,
                                       overlay_rects)


def _img(w=300, h=200, fill=40):
    return np.full((h, w, 3), fill, dtype=np.uint8)


def _mob(cx, cy):
    return Mob(cx=cx, cy=cy, w=30, h=30, score=1.0, name="mob")


def test_overlay_rects_translates_minimap_into_playfield_coords():
    regions = {"playfield": (0, 80, 300, 200), "minimap": (10, 90, 60, 40)}
    assert overlay_rects(regions) == [(10, 10, 60, 40)]


def test_overlay_rects_needs_both_regions():
    assert overlay_rects({"playfield": (0, 0, 10, 10)}) == []
    assert overlay_rects({}) == []


def test_blank_rects_paints_grey_and_leaves_the_rest_alone():
    img = _img()
    out = blank_rects(img, [(100, 180, 50, 30)], origin=(100, 160))
    assert (out[20:50, 0:50] == OVERLAY_GRAY).all()
    assert (out[20:50, 60:100] == 40).all()
    assert (img == 40).all()          # 原影像沒被改到


def test_blank_rects_clips_to_the_image():
    """矩形一半在畫面外（搜尋框比它小）也不能爆掉。"""
    out = blank_rects(_img(), [(-20, -20, 60, 60)], origin=(0, 0))
    assert (out[0:40, 0:40] == OVERLAY_GRAY).all()


def test_blank_rects_returns_the_same_object_when_nothing_applies():
    """完全沒重疊時不要白白複製一張影像——每個 tick 都會走到這裡。"""
    img = _img()
    assert blank_rects(img, []) is img
    assert blank_rects(img, [(9000, 9000, 10, 10)], origin=(0, 0)) is img


def test_drop_at_removes_detections_on_the_player():
    mobs = [_mob(150, 100), _mob(260, 100)]
    kept = drop_at(mobs, (150, 100), box=(100, 140))
    assert [(m.cx, m.cy) for m in kept] == [(260, 100)]


def test_drop_at_scales_the_box_with_the_resolution():
    """同一個世界距離，在 2 倍大的視窗上是 2 倍的像素。"""
    mobs = [_mob(220, 100)]
    assert drop_at(mobs, (150, 100), box=(100, 140), scale=1.0) == mobs
    assert drop_at(mobs, (150, 100), box=(100, 140), scale=2.0) == []


def test_drop_at_keeps_everything_when_the_player_is_unknown():
    mobs = [_mob(150, 100)]
    assert drop_at(mobs, None, box=(100, 140)) == mobs
