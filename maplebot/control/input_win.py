"""鍵盤輸出層：Windows SendInput（DirectInput scancode）。

楓谷經典客戶端讀 DirectInput，必須用 scancode 而不是 virtual-key，
方向鍵等 extended key 還要多帶 KEYEVENTF_EXTENDEDKEY 旗標。
非 Windows 或 --dry-run 時用 NullBackend（只記錄不送出）。
"""
import random
import sys
import time
from typing import Dict, List, Optional, Set, Tuple

IS_WINDOWS = sys.platform == "win32"

# key 名稱 -> (scancode, is_extended)
SCANCODES: Dict[str, Tuple[int, bool]] = {
    **{c: (sc, False) for c, sc in zip("1234567890", range(0x02, 0x0C))},
    "q": (0x10, False), "w": (0x11, False), "e": (0x12, False), "r": (0x13, False),
    "t": (0x14, False), "y": (0x15, False), "u": (0x16, False), "i": (0x17, False),
    "o": (0x18, False), "p": (0x19, False), "a": (0x1E, False), "s": (0x1F, False),
    "d": (0x20, False), "f": (0x21, False), "g": (0x22, False), "h": (0x23, False),
    "j": (0x24, False), "k": (0x25, False), "l": (0x26, False), "z": (0x2C, False),
    "x": (0x2D, False), "c": (0x2E, False), "v": (0x2F, False), "b": (0x30, False),
    "n": (0x31, False), "m": (0x32, False),
    "escape": (0x01, False), "backspace": (0x0E, False), "tab": (0x0F, False),
    "enter": (0x1C, False), "ctrl": (0x1D, False), "shift": (0x2A, False),
    "alt": (0x38, False), "space": (0x39, False),
    "minus": (0x0C, False), "equals": (0x0D, False),
    "f1": (0x3B, False), "f2": (0x3C, False), "f3": (0x3D, False), "f4": (0x3E, False),
    "f5": (0x3F, False), "f6": (0x40, False), "f7": (0x41, False), "f8": (0x42, False),
    "f9": (0x43, False), "f10": (0x44, False), "f11": (0x57, False), "f12": (0x58, False),
    "up": (0x48, True), "down": (0x50, True), "left": (0x4B, True), "right": (0x4D, True),
    "insert": (0x52, True), "delete": (0x53, True), "home": (0x47, True),
    "end": (0x4F, True), "pageup": (0x49, True), "pagedown": (0x51, True),
}

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008


class NullBackend:
    """不真的送鍵，只留下紀錄——給 --dry-run 與單元測試用。"""

    def __init__(self):
        self.history: List[Tuple[str, int]] = []
        self.clicks: List[Tuple[int, int]] = []

    def send(self, scan: int, extended: bool, keyup: bool) -> bool:
        self.history.append(("up" if keyup else "down", scan))
        return True

    def click(self, x: int, y: int) -> bool:
        self.clicks.append((x, y))
        return True


if IS_WINDOWS:
    import ctypes

    PUL = ctypes.POINTER(ctypes.c_ulong)

    class _KeyBdInput(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                    ("dwExtraInfo", PUL)]

    class _HardwareInput(ctypes.Structure):
        _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_short),
                    ("wParamH", ctypes.c_ushort)]

    class _MouseInput(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                    ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)]

    class _InputUnion(ctypes.Union):
        _fields_ = [("ki", _KeyBdInput), ("mi", _MouseInput), ("hi", _HardwareInput)]

    class _Input(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("ii", _InputUnion)]

    # use_last_error 才能在 SendInput 失敗時拿到真正的錯誤碼
    _user32 = ctypes.WinDLL("user32", use_last_error=True)

    class WindowsBackend:
        """SendInput 被擋掉時會回傳 0（而不是拋例外）。

        最常見的原因是遊戲以系統管理員執行、而 Python 沒有：Windows 的
        UIPI 會擋掉低權限行程送給高權限視窗的輸入，錯誤碼 5（拒絕存取）。
        不檢查回傳值的話，log 會顯示「已送出按鍵」但遊戲毫無反應。
        """

        def send(self, scan: int, extended: bool, keyup: bool) -> bool:
            flags = KEYEVENTF_SCANCODE
            if extended:
                flags |= KEYEVENTF_EXTENDEDKEY
            if keyup:
                flags |= KEYEVENTF_KEYUP
            extra = ctypes.c_ulong(0)
            union = _InputUnion()
            union.ki = _KeyBdInput(0, scan, flags, 0, ctypes.pointer(extra))
            inp = _Input(ctypes.c_ulong(1), union)
            sent = _user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))
            if sent != 1:
                self.last_error = ctypes.get_last_error()
                return False
            return True

        last_error: int = 0

        def click(self, x: int, y: int) -> bool:
            """點擊螢幕絕對座標（用來按死亡復活對話框的「確定」）。

            用 SetCursorPos + mouse_event（絕對像素座標，最直接）。跟 SendInput
            送鍵一樣受 UIPI 管：遊戲提權、我們沒提權時點不動——但那個情況
            開跑時就擋下來了（見 window.input_privilege_gap）。
            """
            if not _user32.SetCursorPos(int(x), int(y)):
                self.last_error = ctypes.get_last_error()
                return False
            MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
            _user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.05)
            _user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            return True


class Keyboard:
    """tap / hold 介面，按鍵時間帶隨機抖動；離開前務必 release_all()。"""

    def __init__(self, backend=None, tap_seconds: Tuple[float, float] = (0.06, 0.12)):
        if backend is None:
            backend = WindowsBackend() if IS_WINDOWS else NullBackend()
        self.backend = backend
        self.tap_seconds = tap_seconds
        self._held: Set[str] = set()
        self.sent = 0
        self.failures = 0       # SendInput 回傳 0 的次數（被 UIPI 擋掉等）

    def _lookup(self, key: str) -> Tuple[int, bool]:
        k = key.lower()
        if k not in SCANCODES:
            raise KeyError(f"不支援的按鍵名稱: {key!r}（可用: {sorted(SCANCODES)}）")
        return SCANCODES[k]

    def _send(self, scan: int, ext: bool, keyup: bool) -> None:
        self.sent += 1
        if self.backend.send(scan, ext, keyup=keyup) is False:
            self.failures += 1

    def press(self, key: str) -> None:
        scan, ext = self._lookup(key)
        self._send(scan, ext, keyup=False)
        self._held.add(key.lower())

    def release(self, key: str) -> None:
        scan, ext = self._lookup(key)
        self._send(scan, ext, keyup=True)
        self._held.discard(key.lower())

    def last_error(self) -> int:
        return getattr(self.backend, "last_error", 0)

    def click(self, x: int, y: int) -> bool:
        """點擊螢幕絕對座標。回傳 False = 沒送出去（被 UIPI 擋等）。"""
        fn = getattr(self.backend, "click", None)
        if fn is None:
            return False
        ok = fn(x, y)
        self.sent += 1
        if ok is False:
            self.failures += 1
        return ok is not False

    def tap(self, key: str, seconds: Optional[float] = None) -> None:
        if seconds is None:
            seconds = random.uniform(*self.tap_seconds)
        self.press(key)
        try:
            time.sleep(seconds)
        finally:
            self.release(key)

    def release_all(self) -> None:
        for key in list(self._held):
            try:
                self.release(key)
            except Exception:
                pass
