"""軌跡追蹤：把單幀猜測串成連續軌跡，擋掉跳點。

這一層要保證的是「跟丟」與「跳到別的地方」被分開處理——跟丟會叫人
（watchdog 暫停 + 警報），跳錯會安靜地錯一整晚。
"""
from maplebot.vision.track import PointTracker


def test_first_frame_takes_the_strongest_candidate():
    """還沒有軌跡可比對時，只能信分數——等同追蹤前的舊行為。"""
    t = PointTracker(max_jump=10)
    assert t.update([(5, 5, 1.0), (80, 80, 9.0)]) == (80, 80)


def test_nothing_at_all_stays_unknown():
    assert PointTracker().update([]) is None


def test_it_follows_a_walking_target():
    t = PointTracker(max_jump=10)
    t.update([(50, 50, 1.0)])
    for x in range(53, 71, 3):
        assert t.update([(x, 50, 1.0)]) == (x, 50)
    assert t.confidence == 1.0


def test_a_far_candidate_does_not_steal_the_track():
    """這就是修掉的那個 bug 的形狀：另一個候選分數更高，但它在半張地圖外。

    fixture 上的實例——角色點被黃地板吞掉之後，剩下的最大候選是小地圖標題
    文字的碎片，距離 40 px 以上。分數比它高沒有意義，位置不可能是真的。
    """
    t = PointTracker(max_jump=10)
    t.update([(50, 50, 1.0)])
    assert t.update([(95, 11, 99.0)]) == (50, 50)      # 沿用，不改口
    assert t.jumps == 1


def test_it_coasts_then_admits_it_lost_the_target():
    t = PointTracker(max_jump=10, max_coast=2)
    t.update([(50, 50, 1.0)])
    assert t.update([]) == (50, 50)        # 第 1 幀沿用
    assert t.update([]) == (50, 50)        # 第 2 幀沿用
    assert t.update([]) is None            # 超過 max_coast：承認跟丟
    assert t.last is None


def test_confidence_falls_while_coasting():
    """沿用來的位置愈舊愈不該被當成事實——決策層靠這個分得出來。"""
    t = PointTracker(max_jump=10, max_coast=3)
    t.update([(50, 50, 1.0)])
    assert t.confidence == 1.0
    first = t.update([]) and t.confidence
    t.update([])
    assert 0.0 < t.confidence < first


def test_a_recovered_target_resets_the_confidence():
    t = PointTracker(max_jump=10, max_coast=3)
    t.update([(50, 50, 1.0)])
    t.update([])
    assert t.confidence < 1.0
    t.update([(52, 50, 1.0)])
    assert t.confidence == 1.0


def test_after_losing_it_reacquires_freely():
    """換圖之後舊位置沒有參考價值，必須能重新鎖定。"""
    t = PointTracker(max_jump=10, max_coast=1)
    t.update([(50, 50, 1.0)])
    t.update([])                  # coast
    t.update([])                  # 超過 -> 放棄
    assert t.update([(200, 200, 1.0)]) == (200, 200)


def test_max_coast_zero_means_no_coasting():
    t = PointTracker(max_jump=10, max_coast=0)
    t.update([(50, 50, 1.0)])
    assert t.update([]) is None


def test_the_nearest_candidate_wins_not_the_strongest():
    """一排等距的候選裡，該跟的是離上一幀最近的那個。"""
    t = PointTracker(max_jump=10)
    t.update([(50, 50, 1.0)])
    assert t.update([(52, 50, 1.0), (58, 50, 50.0)]) == (52, 50)
