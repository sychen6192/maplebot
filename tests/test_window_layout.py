"""偵錯視窗擺位：找不與遊戲視窗重疊的位置（避免螢幕擷取拍到自己）。"""
import logging

import numpy as np

from maplebot.config import AppCfg
from maplebot.window import _overlaps, pick_free_position

SCREEN = (0, 0, 1920, 1080)


def test_places_to_the_right_of_game():
    game = (0, 0, 800, 600)
    assert pick_free_position(game, (400, 300), SCREEN) == (808, 0)


def test_falls_back_to_top_left_when_no_room_on_right():
    game = (1200, 400, 700, 600)          # 靠右下，右邊放不下
    spot = pick_free_position(game, (600, 400), SCREEN)
    assert spot == (0, 0)
    assert not _overlaps((*spot, 600, 400), game)


def test_uses_second_monitor_space():
    screen = (0, 0, 3840, 1080)           # 雙螢幕併排
    game = (0, 0, 1900, 1040)
    spot = pick_free_position(game, (1280, 800), screen)
    assert spot is not None and spot[0] >= 1908


def test_returns_none_when_game_fills_screen():
    game = (0, 0, 1920, 1080)
    assert pick_free_position(game, (800, 600), SCREEN) is None


def test_result_never_overlaps_game():
    game = (300, 200, 900, 700)
    spot = pick_free_position(game, (500, 400), SCREEN)
    assert spot is not None
    assert not _overlaps((*spot, 500, 400), game)


def test_overlap_helper():
    assert _overlaps((0, 0, 10, 10), (5, 5, 10, 10)) is True
    assert _overlaps((0, 0, 10, 10), (10, 0, 10, 10)) is False   # 邊界相接不算


def test_runner_resizes_the_window_back_to_the_calibrated_size():
    """ROI 是照某個視窗大小量的，尺寸一變全部錯位。

    與其開場報錯要使用者自己去把視窗拉回去（拉不準就一直錯），程式自己調。
    """
    calls = []

    class _Capture:
        size = (1366, 768)

        def __init__(self):
            self._frame = np.zeros((768, 1366, 3), dtype=np.uint8)

        def grab(self, region=None):
            return self._frame

        def resize_client(self, w, h):
            calls.append((w, h))
            self._frame = np.zeros((h, w, 3), dtype=np.uint8)
            return (w, h)

    from maplebot.runner import Runner

    runner = object.__new__(Runner)
    runner.cfg = AppCfg()
    runner.cfg.calibrated_for = (1920, 1080)
    runner.capture = _Capture()
    runner.log = logging.getLogger("test-resize")

    assert runner._match_calibrated_size((1366, 768)) == (1920, 1080)
    assert calls == [(1920, 1080)]


def test_no_resize_hook_still_reports_the_mismatch():
    """靜態圖片來源沒有視窗可以調，維持原本的「報錯不要跑」。"""
    from maplebot.runner import Runner

    class _ImageSource:
        def grab(self, region=None):
            return np.zeros((600, 800, 3), dtype=np.uint8)

    runner = object.__new__(Runner)
    runner.cfg = AppCfg()
    runner.cfg.calibrated_for = (1920, 1080)
    runner.capture = _ImageSource()
    runner.log = logging.getLogger("test-resize")

    assert runner._match_calibrated_size((800, 600)) == (800, 600)
