"""偵錯視窗擺位：找不與遊戲視窗重疊的位置（避免螢幕擷取拍到自己）。"""
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
