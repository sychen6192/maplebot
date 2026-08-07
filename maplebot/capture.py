"""畫面擷取：遊戲視窗即時擷取（mss）或靜態圖片（離線測試/開發用）。

所有 grab() 都回傳 BGR 的 numpy 陣列；region 一律是 client 區座標 [x, y, w, h]。
"""
from typing import Optional

import cv2
import numpy as np

from .config import Region
from .window import GameWindow, find_game_window


class CaptureError(Exception):
    pass


class WindowCapture:
    def __init__(self, title_substr: str):
        import mss  # Windows 以外的環境可能沒有顯示裝置，延後 import/初始化

        self._title = title_substr
        self._sct = mss.mss()
        self._win: Optional[GameWindow] = None
        self.refresh()

    def refresh(self) -> GameWindow:
        win = find_game_window(self._title)
        if win is None:
            raise CaptureError(f"找不到標題含「{self._title}」的視窗，請先開啟遊戲")
        self._win = win
        return win

    @property
    def size(self):
        assert self._win is not None
        return self._win.size

    def grab(self, region: Optional[Region] = None) -> np.ndarray:
        assert self._win is not None
        ox, oy = self._win.origin
        if region is None:
            x, y, (w, h) = 0, 0, self._win.size
        else:
            x, y, w, h = region
        shot = self._sct.grab({"left": ox + x, "top": oy + y, "width": w, "height": h})
        return np.asarray(shot)[:, :, :3].copy()  # BGRA -> BGR


class ImageCapture:
    """用一張截圖模擬整個遊戲畫面，讓辨識與決策可以完全離線開發。"""

    def __init__(self, path: str):
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            raise CaptureError(f"讀不到圖片: {path}")
        self._img = img

    @property
    def size(self):
        h, w = self._img.shape[:2]
        return (w, h)

    def grab(self, region: Optional[Region] = None) -> np.ndarray:
        if region is None:
            return self._img.copy()
        x, y, w, h = region
        ih, iw = self._img.shape[:2]
        if x < 0 or y < 0 or x + w > iw or y + h > ih:
            raise CaptureError(f"region {region} 超出圖片範圍 {iw}x{ih}")
        return self._img[y:y + h, x:x + w].copy()
