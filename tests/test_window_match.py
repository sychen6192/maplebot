"""遊戲視窗比對：子字串照舊，但不能比中自己行程開的視窗。

使用者填 'maple' 這種短標題時，控制台自己的「mapleai 控制台」也比得中，
而按下開始那一刻它正好在 z-order 最上層——bot 會去截控制台、把按鍵送給 Tk。
"""
import sys

import pytest

from maplebot import window
from maplebot.window import _first_match

OWN = 4242


def test_skips_a_window_owned_by_this_process_and_takes_the_next_match():
    wins = [(1, "mapleai 控制台", OWN), (2, "MapleStory", 7)]
    assert _first_match(wins, "maple", OWN) == (2, "MapleStory")


def test_only_own_windows_match_means_no_game_window():
    wins = [(1, "mapleai 控制台", OWN), (2, "Discord", 7)]
    assert _first_match(wins, "maple", OWN) is None


def test_substring_match_is_case_insensitive_and_keeps_z_order():
    wins = [(1, "Discord", 7), (2, "新楓之谷：經典版 MapleStory", 8), (3, "maple wiki", 9)]
    assert _first_match(wins, "MAPLE", OWN) == (2, "新楓之谷：經典版 MapleStory")
    assert _first_match(wins, "楓之谷", OWN) == (2, "新楓之谷：經典版 MapleStory")


def test_no_match_returns_none():
    assert _first_match([(1, "Discord", 7)], "楓之谷", OWN) is None
    assert _first_match([], "楓之谷", OWN) is None


def test_another_process_with_the_same_title_still_matches():
    """排除的是**自己的行程**，不是標題長得像控制台的視窗。"""
    wins = [(1, "mapleai 控制台", 99)]
    assert _first_match(wins, "maple", OWN) == (1, "mapleai 控制台")


@pytest.mark.skipif(sys.platform == "win32", reason="只驗非 Windows 的退路")
def test_off_windows_there_is_nothing_to_list_or_find():
    assert window.other_window_titles() == []
    assert window.find_game_window("maple") is None
