"""安全機制：緊急停止/暫停熱鍵、異常截圖。

熱鍵用 GetAsyncKeyState 輪詢（不需額外套件、不需鍵盤 hook 權限），
在主迴圈每個 tick 呼叫 poll()。
"""
import os
import sys
import threading
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
    """暫停與停止的旗標，外加「為什麼」。

    paused / stop 維持純 bool：主迴圈與既有測試直接讀它們。原因另外放在
    pause_reason / stop_reason——同一個「已暫停」可能是使用者按了熱鍵、
    也可能是 watchdog 判定異常，回來看的人要知道是哪一種才知道該不該看畫面。

    寫入的人有三個執行緒（主迴圈的熱鍵與 watchdog、督導層、GUI），讀的人
    （Status、GUI）不上鎖，所以順序有講究：先寫原因再設 paused=True、先清
    paused 再清原因。這樣不上鎖的讀者看到 paused=True 時原因一定已經在了。
    """

    def __init__(self, stop_key: str, pause_key: str, logger):
        self.stop = False
        self.paused = False
        self.pause_reason = ""
        self.paused_by_user = False
        self.stop_reason = ""
        self.log = logger
        self._stop_vk = _VK.get(stop_key.lower())
        self._pause_vk = _VK.get(pause_key.lower())
        # 原因給人看，用遊戲裡鍵帽上的寫法（F9），不是設定檔裡的 f9
        self._stop_name = stop_key.upper()
        self._pause_name = pause_key.upper()
        self._prev_down = {"stop": False, "pause": False}
        # 只擋寫入者之間的「檢查後設定」競態；讀者靠上面的寫入順序
        self._lock = threading.Lock()
        if IS_WINDOWS:
            import ctypes
            self._gaks = ctypes.windll.user32.GetAsyncKeyState
        else:
            self._gaks = None

    def pause(self, reason: str, by_user: bool = False) -> bool:
        """暫停並記下原因。已經在暫停就什麼都不做、回 False。

        不覆蓋第一個原因：watchdog 剛暫停、使用者的點擊緊接著落下時，
        要留下的是 watchdog 的理由——那才是回來的人需要看的。
        """
        with self._lock:
            if self.paused:
                return False
            self.pause_reason = reason
            self.paused_by_user = by_user
            self.paused = True
            return True

    def resume(self) -> bool:
        """解除暫停。本來就沒暫停回 False。"""
        with self._lock:
            if not self.paused:
                return False
            self.paused = False
            self.pause_reason = ""
            self.paused_by_user = False
            return True

    def request_stop(self, reason: str) -> None:
        """要求主迴圈結束。原因以第一次為準（停機後使用者又按 F12 不該蓋掉）。"""
        with self._lock:
            if not self.stop_reason:
                self.stop_reason = reason
            self.stop = True

    def _rising_edge(self, name: str, vk: Optional[int]) -> bool:
        # GetAsyncKeyState 的 LSB（上次呼叫以來按過）會被其他行程清掉，
        # 不可靠；改用 0x8000（目前按住）自己做邊緣偵測。
        if not vk:
            return False
        down = bool(self._gaks(vk) & 0x8000)
        fired = down and not self._prev_down[name]
        self._prev_down[name] = down
        return fired

    def poll(self) -> None:
        if self._gaks is None:
            return
        if self._rising_edge("stop", self._stop_vk):
            self.log.warning("偵測到停止熱鍵，正在結束…")
            self.request_stop(f"你按了 {self._stop_name}")
        if self._rising_edge("pause", self._pause_vk):
            if self.paused:
                self.resume()
            else:
                self.pause(f"你按了 {self._pause_name}", by_user=True)
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


def is_black_screen(frame: np.ndarray, dark_level: int = 15,
                    dark_fraction: float = 0.9) -> bool:
    """整個畫面幾乎全黑 = 斷線/換頻道/讀圖中（參考 auto-maple 的
    room-change 偵測）。取 1/4 取樣算就夠準。"""
    sample = frame[::4, ::4]
    dark = (sample.max(axis=2) < dark_level).mean()
    return bool(dark >= dark_fraction)


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
