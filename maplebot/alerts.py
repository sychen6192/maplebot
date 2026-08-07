"""聲音警報（參考 auto-maple 的 notifier 設計，改用 winsound 免音檔）。

danger 類事件（Panic、黑屏、watchdog）長響，提醒人回來看；
info 類事件（其他玩家出現）短響一聲。非 Windows 或關閉時靜音。
"""
import sys
import threading
from typing import Dict, List, Tuple

IS_WINDOWS = sys.platform == "win32"

# (頻率 Hz, 長度 ms) 序列
PATTERNS: Dict[str, List[Tuple[int, int]]] = {
    "panic": [(1400, 350), (900, 350), (1400, 350), (900, 350), (1400, 500)],
    "warn": [(1000, 250), (700, 350)],
    "ding": [(1200, 150)],
}


class Alerts:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled and IS_WINDOWS

    def ping(self, kind: str) -> None:
        if not self.enabled or kind not in PATTERNS:
            return
        threading.Thread(target=self._play, args=(PATTERNS[kind],),
                         daemon=True, name=f"alert-{kind}").start()

    @staticmethod
    def _play(pattern: List[Tuple[int, int]]) -> None:
        import winsound
        try:
            for freq, ms in pattern:
                winsound.Beep(freq, ms)
        except RuntimeError:
            pass
