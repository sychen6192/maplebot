"""經驗值追蹤：效率換算、升級回捲、死亡扣經驗、停滯偵測。"""
import pytest

from maplebot.progress import ExpTracker


def test_accumulates_gain():
    t = ExpTracker()
    t.update(0.10, 0.0)
    t.update(0.15, 60.0)
    t.update(0.20, 120.0)
    assert t.gained() == pytest.approx(0.10)


def test_level_up_wraps_and_counts():
    """升級時 EXP 從 99% 掉回 1%，要算成進度而不是倒退。"""
    t = ExpTracker()
    t.update(0.95, 0.0)
    t.update(0.99, 10.0)
    t.update(0.01, 20.0)          # 升級
    t.update(0.05, 30.0)
    assert t.levels == 1
    assert t.deaths == 0
    assert t.gained() == pytest.approx(1 + 0.05 - 0.95)


def test_small_drop_counts_as_death_not_level_up():
    """楓谷死亡會扣經驗——小幅倒退不能誤判成升級。"""
    t = ExpTracker()
    t.update(0.50, 0.0)
    t.update(0.42, 10.0)
    assert t.deaths == 1 and t.levels == 0


def test_per_hour_rate():
    t = ExpTracker()
    t.update(0.00, 0.0)
    t.update(0.25, 1800.0)        # 半小時賺 25%
    assert t.per_hour(1800.0) == pytest.approx(0.5)   # 每小時半等


def test_stall_detected_after_timeout():
    t = ExpTracker(stall_seconds=600)
    t.update(0.10, 0.0)
    t.update(0.10, 300.0)
    assert t.stalled(300.0) is False
    assert t.stalled(601.0) is True


def test_gain_resets_stall_timer():
    t = ExpTracker(stall_seconds=600)
    t.update(0.10, 0.0)
    t.update(0.11, 500.0)         # 有進帳
    assert t.stalled(1000.0) is False     # 從 500 起算還沒到 600
    assert t.stalled(1101.0) is True


def test_stall_disabled_when_zero():
    t = ExpTracker(stall_seconds=0)
    t.update(0.10, 0.0)
    assert t.stalled(99999.0) is False


def test_unreadable_exp_is_ignored():
    """EXP 條讀不到時不要污染統計，也不要誤判成停滯。"""
    t = ExpTracker(stall_seconds=600)
    t.update(0.10, 0.0)
    for i in range(10):
        t.update(None, 100.0 + i)
    assert t.last_exp == 0.10
    assert t.gained() == 0.0
    t.update(0.20, 200.0)
    assert t.gained() == pytest.approx(0.10)


def test_summary_before_any_reading():
    assert "未讀到" in ExpTracker().summary(0.0)


def test_summary_mentions_rate_and_levels():
    t = ExpTracker()
    t.update(0.90, 0.0)
    t.update(0.10, 3600.0)        # 一小時升一級
    s = t.summary(3600.0)
    assert "等/小時" in s and "升級 1 次" in s
