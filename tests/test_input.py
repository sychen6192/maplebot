"""鍵盤層：用 NullBackend 驗證按下/放開順序與 release_all。"""
import pytest

from maplebot.control.input_win import Keyboard, NullBackend, SCANCODES


def test_tap_sends_down_then_up():
    backend = NullBackend()
    kb = Keyboard(backend)
    kb.tap("x", seconds=0)
    scan = SCANCODES["x"][0]
    assert backend.history == [("down", scan), ("up", scan)]


def test_release_all_releases_held_keys():
    backend = NullBackend()
    kb = Keyboard(backend)
    kb.press("left")
    kb.press("x")
    kb.release_all()
    ups = [scan for kind, scan in backend.history if kind == "up"]
    assert SCANCODES["left"][0] in ups and SCANCODES["x"][0] in ups


def test_unknown_key_raises():
    kb = Keyboard(NullBackend())
    with pytest.raises(KeyError):
        kb.tap("notakey")


class _BlockedBackend:
    """模擬 SendInput 被 UIPI 擋掉：回傳 0（我們的介面是 False）。"""
    last_error = 5

    def send(self, scan, extended, keyup):
        return False


def test_blocked_send_is_counted_not_silent():
    """按鍵被擋掉時要留下紀錄，不能讓 log 顯示成功、遊戲卻沒反應。"""
    kb = Keyboard(_BlockedBackend())
    kb.tap("x", seconds=0)
    assert kb.sent == 2 and kb.failures == 2      # down + up 都失敗
    assert kb.last_error() == 5                   # ERROR_ACCESS_DENIED


def test_successful_send_records_no_failures():
    kb = Keyboard(NullBackend())
    kb.tap("x", seconds=0)
    assert kb.sent == 2 and kb.failures == 0


def test_arrow_keys_are_extended():
    for key in ("left", "right", "up", "down", "pageup", "pagedown"):
        assert SCANCODES[key][1] is True
