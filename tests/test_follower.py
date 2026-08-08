"""跟隨物過濾：寵物會跟著角色跑，怪不會。"""
from maplebot.vision.follower import FollowerFilter
from maplebot.vision.mobs import Mob


def _mob(cx, cy=260):
    return Mob(cx=cx, cy=cy, w=30, h=24, score=0.9, name="m")


def test_thing_that_never_slides_becomes_a_follower():
    """角色一直往右走，牠在畫面上卻始終待在同一處 = 跟著角色跑。

    第一次看到只能立錨點，所以 hits_needed=3 需要 4 幀。
    """
    f = FollowerFilter(drift_px=40, hits_needed=3)
    pet = _mob(430)
    for _ in range(4):
        kept, followers = f.filter([pet], player_moved=True)
    assert followers == [pet]
    assert kept == []


def test_needs_the_full_streak_before_excluding():
    """只跟一兩幀不算——真的怪也可能剛好跟你同向走幾步。"""
    f = FollowerFilter(drift_px=40, hits_needed=3)
    pet = _mob(430)
    for _ in range(3):
        kept, followers = f.filter([pet], player_moved=True)
        assert kept == [pet] and followers == []


def test_real_mob_slides_across_the_screen_and_is_kept():
    """鏡頭跟著角色，所以角色往右走時站著不動的怪會往左滑。"""
    f = FollowerFilter(drift_px=40, hits_needed=3)
    for x in (500, 400, 300, 200):
        kept, followers = f.filter([_mob(x)], player_moved=True)
    assert len(kept) == 1 and followers == []


def test_standing_still_never_classifies():
    """角色沒動的時候，怪和寵物在畫面上都不會滑動，這時不能計分。"""
    f = FollowerFilter(drift_px=40, hits_needed=2)
    for _ in range(10):
        kept, followers = f.filter([_mob(430)], player_moved=False)
    assert len(kept) == 1 and followers == []


def test_first_sighting_is_never_scored():
    """新出現的目標錨點就是當下位置，位移必為 0——不能因此判成跟隨物。"""
    f = FollowerFilter(drift_px=40, hits_needed=1)
    kept, followers = f.filter([_mob(430)], player_moved=True)
    assert kept and not followers


def test_follower_that_starts_sliding_is_released():
    """判定會持續修正：寵物被收起來、原地留下一隻真的怪也不會冤枉牠。"""
    f = FollowerFilter(drift_px=40, hits_needed=2)
    for _ in range(2):
        f.filter([_mob(430)], player_moved=True)
    assert f.filter([_mob(430)], player_moved=True)[1]      # 已被判定
    kept, followers = f.filter([_mob(300)], player_moved=True)
    assert kept and not followers


def test_track_survives_a_dropped_frame():
    """描邊偵測會閃爍。漏個一幀就重新累積的話，寵物永遠判不出來。"""
    f = FollowerFilter(drift_px=40, hits_needed=3, max_misses=2)
    pet = _mob(430)
    f.filter([pet], player_moved=True)
    f.filter([pet], player_moved=True)
    f.filter([], player_moved=True)          # 這幀沒偵測到
    f.filter([pet], player_moved=True)
    assert f.filter([pet], player_moved=True)[1] == [pet]


def test_two_targets_are_judged_independently():
    """寵物跟在旁邊、怪在遠處滑過去：只排除寵物。"""
    f = FollowerFilter(drift_px=40, hits_needed=2)
    pet = _mob(430)
    for x in (600, 500, 400):
        kept, followers = f.filter([pet, _mob(x)], player_moved=True)
    assert followers == [pet]
    assert [m.cx for m in kept] == [400]


def test_tracks_do_not_grow_without_bound():
    f = FollowerFilter(max_tracks=5, max_misses=0)
    for i in range(20):
        f.filter([_mob(100 + i * 200)], player_moved=False)
    assert len(f._tracks) <= 5
