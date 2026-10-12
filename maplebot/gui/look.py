"""控制台的配色、字型與尺寸換算。不碰 tkinter：數值與規則放這裡才測得到。

用 ttk 'clam' 主題（Windows 的 'vista' 不吃 TButton 背景、TEntry 底色，
也畫不出自訂顏色的進度條——主要按鈕、錯誤欄位、狀態色全都會默默消失）。
顏色一律從這裡取，app.py 不寫死色碼。
"""
import re
from typing import Iterable, Optional, Tuple

# 底色、卡片、文字
PALETTE = {
    "bg": "#F5F6F8",               # 視窗底色
    "card": "#FFFFFF",
    "border": "#D7DBE0",
    "text": "#1F2328",
    "muted": "#5B6470",            # 次要說明、已儲存、灰掉的數值
    "primary": "#2563EB",          # Primary.TButton
    "primary_hover": "#1D4ED8",
    "primary_disabled": "#93B4F5",
    "primary_text": "#FFFFFF",
    "strong_bg": "#E5E7EB",        # Strong.TButton（暫停）：不能看起來像「開始」
    "danger": "#B91C1C",           # Danger.TButton 的字（停止…）
    "danger_border": "#FCA5A5",
    "log_bg": "#FBFBFC",
    "log_warn": "#B45309",
    "log_error": "#B91C1C",        # 粗體
    "log_dim": "#8A929C",          # 工具輸出、tick 行
    "invalid_bg": "#FEF2F2",       # 欄位有錯
    "invalid_border": "#B91C1C",
    "warn_bg": "#FEF9C3",          # 欄位只是提醒
    "dirty": "#B45309",            # 改過還沒存的欄位標籤（前面加 •）
    "lock_fg": "#92400E",          # 執行中鎖住設定的那一條
    "lock_bg": "#FEF3C7",
    "key_bg": "#F9FAFB",           # Key.TEntry
    "key_capture_bg": "#DBEAFE",   # 正在等使用者按鍵
}

# 狀態色：(字色, 底色)。View.tone、Step 圖示、判讀列都用這些 key。
# 'plain' 是一般文字（數值正常、不需要提醒時）
TONES = {
    "ok": ("#15803D", "#DCFCE7"),
    "warn": ("#B45309", "#FEF3C7"),
    "bad": ("#B91C1C", "#FEE2E2"),
    "info": ("#1D4ED8", "#DBEAFE"),
    "tool": ("#6D28D9", "#EDE9FE"),     # 也是 [預演] 標籤
    "muted": ("#374151", "#E5E7EB"),
    "plain": (PALETTE["text"], PALETTE["card"]),
}

# 群組標章：設定寫進哪個檔（settings.scope() 的值）
SCOPES = {
    "profile": ("Profile・這張地圖", "#1D4ED8", "#DBEAFE"),
    "config": ("Config・這台電腦", "#5B21B6", "#EDE9FE"),
}

# 儀表 (填色, 底槽)，照遊戲裡的顏色
METERS = {
    "hp": ("#DC2626", "#FDE2E2"),
    "mp": ("#2563EB", "#DDE7FB"),
    "exp": ("#CA8A04", "#F6ECC8"),
}

# 儀表上的門檻刻度（live.Meter.marks 的第二欄）。2px 寬、比條高 3px
MARKS = {"potion": "#B45309", "critical": "#7F1D1D"}

# 依序找第一個裝了的。沒有的話 zh-TW Tk 會退回新細明體 9pt，又小又糊
FONT_CANDIDATES = (
    "Microsoft JhengHei UI", "Microsoft JhengHei", "PingFang TC",
    "Noto Sans CJK TC", "Noto Sans TC", "WenQuanYi Zen Hei",
)

BASE_SIZE = (1100, 700)     # 邏輯 px（96 DPI）
MIN_SIZE = (900, 600)


def pick_font(families: Iterable[str]) -> Optional[str]:
    """tkfont.families() 裡第一個候選字型；都沒有就回 None（保留 Tk 預設）。"""
    have = set(families)
    for name in FONT_CANDIDATES:
        if name in have:
            return name
    return None


def scale(n: float, s: float) -> int:
    """邏輯 px -> 實際 px。s = winfo_fpixels('1i') / 96。

    Tk 會自己放大點數字型，但像素尺寸（padding、wraplength、canvas）不會，
    150% 的螢幕上不換算的話字變大、框沒變大，整排擠在一起。
    """
    return int(round(n * s))


def initial_geometry(screen_w: int, screen_h: int, s: float) -> str:
    """第一次開啟的大小與位置 'WxH+X+Y'：1100x700 邏輯 px，但不超過螢幕。

    1080p 開 150% 縮放只剩約 1280x672 邏輯 px，照 700 高開會超出螢幕下緣
    （工作列擋住底下的按鈕）。高度只取 90%，放在偏上的位置讓出工作列。
    """
    w = min(scale(BASE_SIZE[0], s), int(screen_w * 0.92))
    h = min(scale(BASE_SIZE[1], s), int(screen_h * 0.90))
    x = max(0, (screen_w - w) // 2)
    y = max(0, (screen_h - h) // 3)
    return f"{w}x{h}+{x}+{y}"


def min_size(screen_w: int, screen_h: int, s: float) -> Tuple[int, int]:
    """minsize：900x600 邏輯 px，但小螢幕上不能比螢幕還大。"""
    return (min(scale(MIN_SIZE[0], s), screen_w), min(scale(MIN_SIZE[1], s), screen_h))


_GEOMETRY = re.compile(r"^=?(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$")

# Windows 回報的最大化視窗會往外多 8px 邊框，不能因此判成「跑出螢幕」
_SLACK = 16


def fits(geometry: str, vscreen: Tuple[int, int, int, int]) -> bool:
    """存下來的 'WxH+X+Y' 是否整個落在目前的虛擬桌面 (x, y, w, h) 裡。

    拔掉副螢幕之後，記住的位置可能整個在不存在的螢幕上——照著還原的話
    視窗開了卻看不到。看不懂的格式（例如從右下角算的 '-X-Y'）一律不還原。
    """
    m = _GEOMETRY.match((geometry or "").strip())
    if not m:
        return False
    w, h, x, y = (int(v) for v in m.groups())
    vx, vy, vw, vh = vscreen
    if w <= 0 or h <= 0:
        return False
    return (x >= vx - _SLACK and y >= vy - _SLACK
            and x + w <= vx + vw + _SLACK and y + h <= vy + vh + _SLACK)
