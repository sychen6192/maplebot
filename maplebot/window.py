"""Windows 遊戲視窗定位與直接擷取。

兩種取得畫面的方式：
- PrintWindow：直接向視窗要 client 區內容，**被其他視窗蓋住也拿得到**
- 螢幕座標擷取（mss，見 capture.py）：抓螢幕上那塊區域，會拍到擋在前面的視窗
"""
import sys
import time
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


def resize_client(win: GameWindow, width: int, height: int) -> Optional[GameWindow]:
    """把視窗調到 client 區剛好是 width x height，回傳更新後的 GameWindow。

    ROI 是照某一個視窗大小校正的，尺寸一變全部錯位（血條讀成 0%、小地圖框到
    別的地方）。與其開場報錯要使用者自己去拉視窗，不如直接把它調回校正時的
    大小——遊戲視窗大小本來就不是使用者在意的東西。

    SetWindowPos 給的是**整個視窗**的大小，client 區還要扣掉邊框與標題列，
    所以先量出目前兩者的差額再加回去（比 AdjustWindowRectEx 可靠：不必猜
    視窗樣式，DPI 縮放也已經反映在實測值裡）。
    """
    if not IS_WINDOWS:
        return None
    outer = wintypes.RECT()
    if not user32.GetWindowRect(win.hwnd, ctypes.byref(outer)):
        return None
    inner = wintypes.RECT()
    user32.GetClientRect(win.hwnd, ctypes.byref(inner))
    pad_w = (outer.right - outer.left) - (inner.right - inner.left)
    pad_h = (outer.bottom - outer.top) - (inner.bottom - inner.top)

    SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0002, 0x0004, 0x0010
    user32.SetWindowPos(win.hwnd, 0, 0, 0, width + pad_w, height + pad_h,
                        SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)
    return find_game_window(win.title)


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


def window_process_elevated(hwnd: int) -> Optional[bool]:
    """這個視窗所屬的行程有沒有提權（系統管理員）。查不到回 None。"""
    if not IS_WINDOWS:
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                     False, pid.value)
        if not hproc:
            return None
        try:
            TOKEN_QUERY, TokenElevation = 0x0008, 20
            token = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(hproc, TOKEN_QUERY,
                                             ctypes.byref(token)):
                return None
            try:
                elev = wintypes.DWORD()
                ret = wintypes.DWORD()
                if not advapi32.GetTokenInformation(
                        token, TokenElevation, ctypes.byref(elev),
                        ctypes.sizeof(elev), ctypes.byref(ret)):
                    return None
                return bool(elev.value)
            finally:
                kernel32.CloseHandle(token)
        finally:
            kernel32.CloseHandle(hproc)
    except Exception:
        return None


def current_process_elevated() -> Optional[bool]:
    """自己（Python）有沒有提權。"""
    if not IS_WINDOWS:
        return None
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return None


def input_privilege_gap(hwnd: int) -> bool:
    """遊戲提權了、我們沒有——SendInput 會被 UIPI **默默**丟掉的那種組合。

    這是掛機失敗裡最惡毒的一種：SendInput 回報成功、log 一切正常，
    遊戲卻一個鍵都收不到。角色站在原地被怪圍毆，藥也喝不下去。
    """
    return window_process_elevated(hwnd) is True and \
        current_process_elevated() is False


def bring_to_foreground(win: GameWindow) -> bool:
    """把遊戲視窗帶到前景，回報有沒有成功。

    SendInput 打進的是**前景**視窗。bot 從終端機/GUI 啟動時焦點在終端機上，
    所有按鍵會打進終端機而不是遊戲——log 顯示一切正常、角色卻一步都沒動。
    這跟 UIPI 被擋不一樣：SendInput 回報成功、failures 也是 0，完全無聲。

    背景行程直接呼叫 SetForegroundWindow 會被 Windows 擋掉（防偷焦點），
    擋掉時把自己的執行緒跟目前前景視窗的執行緒 AttachThreadInput 綁一起
    再呼叫一次——這是視窗自動化的標準作法。
    """
    if not IS_WINDOWS:
        return True
    if user32.GetForegroundWindow() == win.hwnd:
        return True
    SW_RESTORE = 9
    if user32.IsIconic(win.hwnd):
        user32.ShowWindow(win.hwnd, SW_RESTORE)
    user32.SetForegroundWindow(win.hwnd)
    time.sleep(0.15)
    if user32.GetForegroundWindow() == win.hwnd:
        return True

    fg = user32.GetForegroundWindow()
    kernel32 = ctypes.windll.kernel32
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    my_tid = kernel32.GetCurrentThreadId()
    attached = bool(user32.AttachThreadInput(my_tid, fg_tid, True)) if fg_tid else False
    try:
        user32.BringWindowToTop(win.hwnd)
        user32.SetForegroundWindow(win.hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(my_tid, fg_tid, False)
    time.sleep(0.15)
    return user32.GetForegroundWindow() == win.hwnd


def virtual_screen() -> Tuple[int, int, int, int]:
    """所有螢幕合起來的桌面範圍 (x, y, w, h)，含副螢幕。"""
    if not IS_WINDOWS:
        return (0, 0, 1920, 1080)
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    return (user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))


def _overlaps(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


def pick_free_position(game_rect: Tuple[int, int, int, int],
                       win_size: Tuple[int, int],
                       screen_rect: Tuple[int, int, int, int]) -> Optional[Tuple[int, int]]:
    """找一個放得下、又不會蓋住遊戲視窗的位置。

    螢幕擷取模式下，偵錯視窗一旦蓋在遊戲上就會拍到自己（畫面遞迴疊圖），
    所以寧可放到副螢幕或旁邊；真的沒空位就回 None 讓呼叫端提示使用者。
    """
    gx, gy, gw, gh = game_rect
    ww, wh = win_size
    sx, sy, sw, sh = screen_rect
    candidates = [
        (gx + gw + 8, sy),            # 遊戲右邊（含副螢幕）
        (sx, sy),                     # 桌面左上
        (gx, gy + gh + 8),            # 遊戲下方
        (sx + sw - ww, sy),           # 桌面右上
        (sx, sy + sh - wh),           # 桌面左下
    ]
    for x, y in candidates:
        if x < sx or y < sy or x + ww > sx + sw or y + wh > sy + sh:
            continue
        if _overlaps((x, y, ww, wh), game_rect):
            continue
        return (x, y)
    return None
