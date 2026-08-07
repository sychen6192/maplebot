"""Windows 遊戲視窗定位與直接擷取。

兩種取得畫面的方式：
- PrintWindow：直接向視窗要 client 區內容，**被其他視窗蓋住也拿得到**
- 螢幕座標擷取（mss，見 capture.py）：抓螢幕上那塊區域，會拍到擋在前面的視窗
"""
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

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

    # 64 位元下若不指定 restype，控制代碼會被截成 32 位元而失效
    user32.GetWindowDC.restype = wintypes.HDC
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrintWindow.restype = wintypes.BOOL
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]

    PW_CLIENTONLY = 0x00000001
    PW_RENDERFULLCONTENT = 0x00000002
    BI_RGB = 0
    DIB_RGB_COLORS = 0

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class _BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", _BITMAPINFOHEADER),
                    ("bmiColors", wintypes.DWORD * 3)]

    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                                wintypes.UINT, ctypes.c_void_p,
                                ctypes.POINTER(_BITMAPINFO), wintypes.UINT]


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


def grab_client(win: GameWindow) -> Optional[np.ndarray]:
    """用 PrintWindow 取得 client 區畫面（BGR）。

    直接向視窗要內容，所以其他視窗蓋在遊戲上面也不會拍到。
    部分 DirectX 客戶端不支援，會回傳全黑——呼叫端要自行判斷後改用螢幕擷取。
    """
    if not IS_WINDOWS:
        return None
    w, h = win.size
    if w <= 0 or h <= 0:
        return None

    hwnd_dc = user32.GetWindowDC(win.hwnd)
    if not hwnd_dc:
        return None
    mem_dc = bitmap = None
    try:
        mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
        bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
        if not mem_dc or not bitmap:
            return None
        gdi32.SelectObject(mem_dc, bitmap)
        ok = user32.PrintWindow(win.hwnd, mem_dc,
                                PW_CLIENTONLY | PW_RENDERFULLCONTENT)
        if not ok:
            return None

        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = w
        info.bmiHeader.biHeight = -h        # 負值 = top-down，省一次翻轉
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB

        buf = (ctypes.c_ubyte * (w * h * 4))()
        if gdi32.GetDIBits(mem_dc, bitmap, 0, h, buf,
                           ctypes.byref(info), DIB_RGB_COLORS) == 0:
            return None
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
        return arr[:, :, :3].copy()          # BGRA -> BGR
    finally:
        # GDI 物件不釋放會在迴圈裡累積到耗盡，必須每次清乾淨
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if mem_dc:
            gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(win.hwnd, hwnd_dc)


def focus(win: GameWindow) -> None:
    if IS_WINDOWS:
        user32.SetForegroundWindow(win.hwnd)
