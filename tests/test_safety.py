"""安全機制：黑屏偵測與 watchdog 計時。"""
import numpy as np

from maplebot.safety import LostPlayerWatchdog, is_black_screen


def test_black_screen_detected():
    frame = np.zeros((300, 400, 3), dtype=np.uint8)
    assert is_black_screen(frame) is True


def test_dim_but_not_black_frame():
    rng = np.random.default_rng(1)
    frame = rng.integers(30, 90, (300, 400, 3), dtype=np.uint8)
    assert is_black_screen(frame) is False


def test_black_with_small_ui_still_counts():
    frame = np.zeros((300, 400, 3), dtype=np.uint8)
    frame[280:, :] = 120  # 底部殘留一條 UI（約 6.7% 面積）
    assert is_black_screen(frame) is True


def test_watchdog_triggers_after_timeout():
    wd = LostPlayerWatchdog(timeout=5.0)
    assert wd.update(True, now=100.0) is False
    assert wd.update(False, now=103.0) is False
    assert wd.update(False, now=105.1) is True
    assert wd.update(True, now=106.0) is False  # 找回來就重置
