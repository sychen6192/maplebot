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


def test_arrow_keys_are_extended():
    for key in ("left", "right", "up", "down", "pageup", "pagedown"):
        assert SCANCODES[key][1] is True
