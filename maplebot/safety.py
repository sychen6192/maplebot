"""安全機制：緊急停止/暫停熱鍵、異常截圖。

熱鍵用 GetAsyncKeyState 輪詢（不需額外套件、不需鍵盤 hook 權限），
在主迴圈每個 tick 呼叫 poll()。
"""
import os
import sys
import time
from datetime import datetime
from typing import Optional

import cv2
import numpy as np

IS_WINDOWS = sys.platform == "win32"

_VK = {
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}

ANOMALY_DIR = os.path.join("logs", "anomalies")


class Safety:
    def __init__(self, stop_key: str, pause_key: str, logger):
        self.stop = False
        self.paused = False
        self.log = logger
        self._stop_vk = _VK.get(stop_key.lower())
        self._pause_vk = _VK.get(pause_key.lower())
        if IS_WINDOWS:
            import ctypes
            self._gaks = ctypes.windll.user32.GetAsyncKeyState
        else:
            self._gaks = None

    def poll(self) -> None:
        if self._gaks is None:
            return
        # bit 0 = 上次呼叫以來是否被按過
        if self._stop_vk and self._gaks(self._stop_vk) & 0x1:
            self.log.warning("偵測到停止熱鍵，正在結束…")
            self.stop = True
        if self._pause_vk and self._gaks(self._pause_vk) & 0x1:
            self.paused = not self.paused
            self.log.warning("熱鍵切換：%s", "已暫停" if self.paused else "繼續執行")


def save_anomaly(frame: Optional[np.ndarray], reason: str, logger) -> None:
    os.makedirs(ANOMALY_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(ANOMALY_DIR, f"{stamp}.png")
    if frame is not None:
        cv2.imwrite(path, frame)
        logger.warning("異常狀況（%s），畫面已存到 %s", reason, path)
    else:
        logger.warning("異常狀況（%s），但沒有可存的畫面", reason)


class LostPlayerWatchdog:
    """連續一段時間找不到玩家黃點（換圖/斷線/被傳走）就觸發暫停。"""

    def __init__(self, timeout: float):
        self.timeout = timeout
        self._last_seen = time.monotonic()

    def update(self, player_found: bool, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        if player_found:
            self._last_seen = now
            return False
        return (now - self._last_seen) >= self.timeout
