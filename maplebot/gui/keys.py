"""tkinter 的 keysym -> 專案的按鍵名稱。

GUI 的按鍵欄位原本要自己打字（"pageup"、"ctrl"），打錯了不會有人告訴你，
要等 bot 跑起來、按鍵沒反應才發現。改成「點一下欄位，按下那個鍵就綁定」，
就需要把 tkinter 回報的 keysym 換成 control/input_win.py 認得的名稱。

兩邊的命名對不太起來（PageUp 在 tkinter 叫 Prior、Enter 叫 Return），
所以要一張對照表。認不得的鍵回 None，呼叫端保留原值並說一聲——不能默默
寫進一個 SCANCODES 查不到的名字，那會在按下去的瞬間才炸。
"""
from typing import Optional

from ..control.input_win import SCANCODES

# 名字對不起來的才列在這裡；其餘（a-z、0-9、F1-F12）用小寫就對得上
_ALIASES = {
    "Prior": "pageup",
    "Next": "pagedown",
    "Return": "enter",
    "KP_Enter": "enter",
    "BackSpace": "backspace",
    "Control_L": "ctrl", "Control_R": "ctrl",
    "Shift_L": "shift", "Shift_R": "shift",
    "Alt_L": "alt", "Alt_R": "alt",
    "equal": "equals",
    "Escape": "escape",
}


def keysym_to_key(keysym: str) -> Optional[str]:
    """回傳 SCANCODES 裡的名稱；這個鍵送不出去就回 None。"""
    if not keysym:
        return None
    name = _ALIASES.get(keysym, keysym.lower())
    return name if name in SCANCODES else None
