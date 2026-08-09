"""點一下欄位、按下按鍵就綁定：tkinter 的 keysym 要換成送得出去的名稱。"""
import pytest

from maplebot.control.input_win import SCANCODES
from maplebot.gui.keys import keysym_to_key


@pytest.mark.parametrize("keysym,expected", [
    ("a", "a"),
    ("z", "z"),
    ("A", "a"),                 # 按著 shift 時 keysym 是大寫
    ("5", "5"),
    ("F9", "f9"),
    ("space", "space"),
    ("Up", "up"),
    ("Left", "left"),
    ("Home", "home"),
    ("Delete", "delete"),
])
def test_names_that_only_need_lowercasing(keysym, expected):
    assert keysym_to_key(keysym) == expected


@pytest.mark.parametrize("keysym,expected", [
    ("Prior", "pageup"),        # tkinter 不叫 PageUp
    ("Next", "pagedown"),
    ("Return", "enter"),
    ("KP_Enter", "enter"),
    ("BackSpace", "backspace"),
    ("Control_L", "ctrl"),
    ("Control_R", "ctrl"),      # 左右 ctrl 綁出來要是同一個
    ("Shift_R", "shift"),
    ("Alt_L", "alt"),
    ("equal", "equals"),
])
def test_names_that_differ_between_tkinter_and_us(keysym, expected):
    assert keysym_to_key(keysym) == expected


@pytest.mark.parametrize("keysym", ["KP_5", "Caps_Lock", "Num_Lock", "F13",
                                    "Super_L", "", "??"])
def test_keys_we_cannot_send_are_rejected(keysym):
    """送不出去的鍵要當場擋掉。默默寫進一個 SCANCODES 查不到的名字，
    會等到 bot 真的去按它的那一刻才爆掉。"""
    assert keysym_to_key(keysym) is None


def test_everything_it_returns_is_actually_sendable():
    for keysym in ("a", "F12", "Prior", "Control_L", "equal", "space", "Next"):
        name = keysym_to_key(keysym)
        assert name in SCANCODES, (keysym, name)
