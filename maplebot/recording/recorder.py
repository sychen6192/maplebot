"""錄製：一邊正常玩，一邊記下小地圖位置與按了哪些鍵。

按鍵用 GetAsyncKeyState 輪詢，跟 safety.py 的熱鍵同一套做法——不裝鍵盤 hook、
不需要額外套件，也不會被防毒盯上。輪詢頻率跟主迴圈一樣就夠：巡邏點只需要
知道「走到哪裡折返」和「站定按了什麼」，不需要毫秒級的按鍵時序。

錄下來的原始樣本交給 route.compress() 壓成巡邏點。
"""
import sys
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .route import Sample

IS_WINDOWS = sys.platform == "win32"

# key 名稱 -> virtual-key code（只列會用到的；跟 control/input_win.py 的名稱一致）
VK: Dict[str, int] = {
    **{c: 0x30 + i for i, c in enumerate("0123456789")},
    **{c: 0x41 + i for i, c in enumerate("abcdefghijklmnopqrstuvwxyz")},
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "space": 0x20, "ctrl": 0x11, "shift": 0x10, "alt": 0x12,
    "enter": 0x0D, "tab": 0x09, "escape": 0x1B,
    "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}

# 預設監看的鍵：方向鍵（判斷走向）＋ 常見的技能/跳躍鍵
DEFAULT_WATCH: Tuple[str, ...] = (
    "left", "right", "up", "down", "alt", "ctrl", "shift", "space",
    *"0123456789", *"qwerasdfzxcv", "insert", "delete", "home", "end",
    "pageup", "pagedown",
)


class KeyWatcher:
    """回報目前按著哪些鍵。非 Windows 一律回空（測試用 pressed 注入）。"""

    def __init__(self, watch: Sequence[str] = DEFAULT_WATCH):
        self.watch = [k for k in watch if k in VK]
        self._gaks: Optional[Callable[[int], int]] = None
        if IS_WINDOWS:
            import ctypes
            self._gaks = ctypes.windll.user32.GetAsyncKeyState

    def pressed(self) -> Tuple[str, ...]:
        if self._gaks is None:
            return ()
        # 取 MSB（目前按著）而不是 LSB（上次查詢後按過）——LSB 會被別的行程清掉
        return tuple(k for k in self.watch if self._gaks(VK[k]) & 0x8000)


class Recorder:
    """把 capture + perceiver + 鍵盤狀態收成一串 Sample。

    不自己開執行緒也不自己計時：呼叫端（CLI 或 GUI）每個 tick 呼叫一次 step()，
    這樣同一份邏輯在兩邊都能用，離線測試也只要餵假的 perceive 就好。
    """

    def __init__(self, perceive: Callable[[float], Tuple[Optional[int], Optional[int]]],
                 keys: Optional[KeyWatcher] = None):
        self._perceive = perceive
        self._keys = keys or KeyWatcher()
        self.samples: List[Sample] = []
        self.started_at: Optional[float] = None

    def start(self, now: Optional[float] = None) -> None:
        self.samples.clear()
        self.started_at = time.monotonic() if now is None else now

    def step(self, now: Optional[float] = None) -> Sample:
        t = time.monotonic() if now is None else now
        if self.started_at is None:      # 0.0 是合法的起點，不能用 falsy 判斷
            self.started_at = t
        x, y = self._perceive(t)
        s = Sample(t=t - self.started_at, x=x, y=y, keys=self._keys.pressed())
        self.samples.append(s)
        return s

    @property
    def seconds(self) -> float:
        return self.samples[-1].t if self.samples else 0.0

    @property
    def tracked(self) -> int:
        """有認到玩家點的樣本數——太少代表小地圖 ROI 有問題。"""
        return sum(1 for s in self.samples if s.x is not None)
