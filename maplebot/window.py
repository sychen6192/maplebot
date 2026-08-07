"""Windows 遊戲視窗定位：找視窗、取得 client 區的螢幕座標。"""
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    def _set_dpi_aware() -> None:
        # 螢幕縮放 (DPI scaling) 會讓擷取座標整組偏移，必須先宣告 DPI aware
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass

    _set_dpi_aware()


@dataclass
class GameWindow:
    hwnd: int
    title: str
    origin: Tuple[int, int]  # client 區左上角的螢幕座標
    size: Tuple[int, int]    # client 區寬高


def list_windows() -> List[Tuple[int, str]]:
    if not IS_WINDOWS:
        return []
    results: List[Tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                results.append((hwnd, buf.value))
        return True

    user32.EnumWindows(_cb, 0)
    return results


def find_game_window(title_substr: str) -> Optional[GameWindow]:
    if not IS_WINDOWS:
        return None
    needle = title_substr.lower()
    for hwnd, title in list_windows():
        if needle in title.lower():
            rect = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            pt = wintypes.POINT(0, 0)
            user32.ClientToScreen(hwnd, ctypes.byref(pt))
            return GameWindow(
                hwnd=hwnd,
                title=title,
                origin=(pt.x, pt.y),
                size=(rect.right - rect.left, rect.bottom - rect.top),
            )
    return None


def focus(win: GameWindow) -> None:
    if IS_WINDOWS:
        user32.SetForegroundWindow(win.hwnd)
