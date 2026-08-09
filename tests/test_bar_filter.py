"""血條讀值的去雜訊。

實際踩到的狀況：被怪撞到 -> 血條閃一下 -> 那一幀顏色遮罩抓不到 -> 讀成 0%
-> 灌藥 + 判定瀕死停機。掛整晚就結束在第一次被撞到。
"""
from maplebot.vision.status import BarFilter


def _run(f, values):
    return [f.update(v) for v in values]


def test_a_single_flash_to_zero_is_ignored():
    f = BarFilter(max_drop=0.35, confirm=2)
    out = _run(f, [1.0, 1.0, 0.0, 1.0, 1.0])
    assert out == [1.0, 1.0, 1.0, 1.0, 1.0]
    assert f.suppressed == 1


def test_real_damage_still_gets_through():
    """真的被打掉一半，晚一幀也要報出來——不然不會喝藥。"""
    f = BarFilter(max_drop=0.35, confirm=2)
    out = _run(f, [1.0, 0.4, 0.4, 0.4])
    assert out == [1.0, 1.0, 0.4, 0.4]      # 只延遲一幀


def test_dying_for_real_reaches_zero():
    f = BarFilter(max_drop=0.35, confirm=2)
    assert _run(f, [1.0, 0.0, 0.0, 0.0])[-1] == 0.0


def test_small_drops_are_immediate():
    """一般被打掉一點點不該有延遲，喝藥門檻就在那附近。"""
    f = BarFilter(max_drop=0.35, confirm=2)
    assert _run(f, [1.0, 0.8, 0.6, 0.45]) == [1.0, 0.8, 0.6, 0.45]
    assert f.suppressed == 0


def test_recovery_is_never_delayed():
    f = BarFilter(max_drop=0.35, confirm=2)
    assert _run(f, [0.2, 1.0]) == [0.2, 1.0]


def test_repeated_flashes_never_stack_into_a_verdict():
    """閃、恢復、再閃……不能因為累積次數就被當成真的沒血。"""
    f = BarFilter(max_drop=0.35, confirm=2)
    out = _run(f, [1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0])
    assert out == [1.0] * 7


def test_longer_flashes_need_a_bigger_confirm():
    """閃兩幀的話 confirm=2 會誤判，這正是那個旋鈕存在的理由。"""
    assert _run(BarFilter(confirm=2), [1.0, 0.0, 0.0, 1.0]) == [1.0, 1.0, 0.0, 1.0]
    assert _run(BarFilter(confirm=3), [1.0, 0.0, 0.0, 1.0]) == [1.0, 1.0, 1.0, 1.0]


def test_first_reading_is_taken_as_is():
    assert BarFilter().update(0.42) == 0.42


def test_unreadable_frame_reuses_the_last_value():
    """ROI 超出畫面之類的情況回 None，不要把 None 往下游丟。"""
    f = BarFilter()
    f.update(0.9)
    assert f.update(None) == 0.9


def test_unreadable_before_any_reading_stays_none():
    assert BarFilter().update(None) is None


def test_reset_forgets_history():
    f = BarFilter()
    f.update(1.0)
    f.reset()
    assert f.update(0.0) == 0.0        # 沒有歷史就沒有可比對的基準
