"""主迴圈碼表：分段耗時、實際 FPS、超支判定與建議。

時鐘是注入的，所以測試不用真的 sleep——這也是把計時邏輯抽出來的主因。
"""
import pytest

from maplebot.metrics import LoopMetrics, Series


class FakeClock:
    """手動推進的時鐘。t += n 就是「過了 n 秒」。"""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def test_stage_records_elapsed_time():
    clock = FakeClock()
    m = LoopMetrics(8.0, clock=clock)
    with m.stage("perceive"):
        clock.advance(0.030)
    with m.stage("perceive"):
        clock.advance(0.050)
    assert m.stages["perceive"].avg_ms == pytest.approx(40.0)
    assert m.stages["perceive"].max_ms == pytest.approx(50.0)


def test_fps_uses_wall_clock_not_work_time():
    """實際頻率要用 tick 之間的真實間隔算。

    用「耗時倒數」會得到一個永遠達標的漂亮數字——sleep 補足預算的那段
    時間被忽略了，而那正是使用者感受到的頻率。
    """
    clock = FakeClock()
    m = LoopMetrics(8.0, clock=clock)
    for _ in range(5):
        m.tick(0.010)          # 只做了 10ms 的事
        clock.advance(0.125)   # 但每 125ms 才跑一輪
    assert 7.5 < m.fps < 8.5


def test_overrun_ignores_blocking_execute_time():
    """execute 是刻意的等待（按住技能鍵），不該算成效能問題。"""
    m = LoopMetrics(8.0, clock=FakeClock())
    for _ in range(10):
        m.tick(0.600, working_seconds=0.020)   # 總共 600ms，但實作業只有 20ms
    assert m.overruns == 0

    m2 = LoopMetrics(8.0, clock=FakeClock())
    for _ in range(10):
        m2.tick(0.600, working_seconds=0.400)  # 實作業就 400ms，這才是超支
    assert m2.overruns == 10
    assert m2.overrun_ratio == 1.0


def test_busiest_stage_excludes_execute():
    clock = FakeClock()
    m = LoopMetrics(8.0, clock=clock)
    with m.stage("execute"):
        clock.advance(0.500)
    with m.stage("perceive"):
        clock.advance(0.080)
    with m.stage("capture"):
        clock.advance(0.005)
    assert m.busiest().name == "perceive"


def test_advice_stays_quiet_when_keeping_up():
    clock = FakeClock()
    m = LoopMetrics(8.0, clock=clock)
    for _ in range(50):
        with m.stage("perceive"):
            clock.advance(0.010)
        m.tick(0.010, 0.010)
    assert m.advice() == ""


def test_advice_names_the_slow_stage_and_how_to_fix_it():
    clock = FakeClock()
    m = LoopMetrics(8.0, clock=clock)
    for _ in range(50):
        with m.stage("perceive"):
            clock.advance(0.300)
        m.tick(0.300, 0.300)
    advice = m.advice()
    assert "perceive" in advice
    assert "mob_search_box" in advice     # 要講「該去動哪裡」，不是只說很慢
    assert "loop.fps" in advice


def test_advice_needs_enough_samples():
    """跑幾個 tick 就下結論會被啟動時的暖機成本騙到。"""
    clock = FakeClock()
    m = LoopMetrics(8.0, clock=clock)
    for _ in range(5):
        with m.stage("perceive"):
            clock.advance(0.300)
        m.tick(0.300, 0.300)
    assert m.advice() == ""


def test_percentile_catches_the_long_tail():
    clock = FakeClock()
    m = LoopMetrics(8.0, clock=clock)
    for i in range(100):
        with m.stage("capture"):
            clock.advance(0.500 if i == 99 else 0.001)
    st = m.stages["capture"]
    assert st.avg_ms < 10                          # 平均被 99 筆快的壓住
    assert st.max_ms == pytest.approx(500.0)       # 但長尾看得到


def test_snapshot_is_json_friendly():
    import json
    clock = FakeClock()
    m = LoopMetrics(8.0, clock=clock)
    with m.stage("decide"):
        clock.advance(0.001)
    m.tick(0.020, 0.020)
    json.dumps(m.snapshot())       # 不該丟例外


# ---- Series ----

def test_series_throttles_to_its_interval():
    s = Series(interval=10.0)
    assert s.maybe_add(100.0, 100.0, hp=1.0) is True
    assert s.maybe_add(105.0, 100.0, hp=0.9) is False    # 還沒到 10 秒
    assert s.maybe_add(110.0, 100.0, hp=0.8) is True
    assert len(s.samples) == 2


def test_series_interval_zero_disables_sampling():
    s = Series(interval=0.0)
    assert s.maybe_add(100.0, 100.0, hp=1.0) is False
    assert s.rows() == []


def test_series_time_is_relative_to_start():
    s = Series(interval=1.0)
    s.maybe_add(160.0, 100.0, hp=0.5)
    assert s.rows()[0]["t"] == 60.0


def test_series_caps_memory_and_counts_drops():
    s = Series(interval=0.0001, cap=3)
    for i in range(6):
        s.maybe_add(100.0 + i, 100.0, mobs=i)
    assert len(s.samples) == 3
    assert s.dropped == 3
    assert [r["mobs"] for r in s.rows()] == [3, 4, 5]   # 留最新的
