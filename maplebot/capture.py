"""畫面擷取：遊戲視窗即時擷取或靜態圖片（離線測試/開發用）。

兩種即時擷取方式：
- printwindow：直接向視窗要畫面，**被其他視窗蓋住也正確**（預設優先）
- screen：用 mss 抓螢幕上那塊座標，會拍到擋在遊戲前面的任何視窗
  （包含本程式自己的偵錯視窗——那會造成無限鏡像、畫面看起來一直放大）

method="auto" 會在啟動時實測一次 PrintWindow，拿不到內容（部分 DirectX
客戶端會回全黑）才退回 screen。

所有 grab() 都回傳 BGR 的 numpy 陣列；region 一律是 client 區座標 [x, y, w, h]。
"""
import time
from typing import Optional

import cv2
import numpy as np

from .config import Region
from .window import (GameWindow, find_game_window, grab_client, list_windows,
                     resize_client)

MAX_LISTED_WINDOWS = 12


class CaptureError(Exception):
    pass


def _open_windows_hint() -> str:
    """把目前開著的視窗列出來——比叫使用者「自己去看標題列」快得多，
    尤其客戶端標題有中英文兩種版本的時候。"""
    titles = [t for _, t in list_windows() if t.strip()]
    if not titles:
        return ""
    shown = titles[:MAX_LISTED_WINDOWS]
    more = f"（另有 {len(titles) - len(shown)} 個）" if len(titles) > len(shown) else ""
    listed = "、".join(f"「{t}」" for t in shown)
    return f"\n目前開著的視窗{more}：{listed}\n把其中一段填進 window.title 即可。"


def looks_blank(frame: Optional[np.ndarray], dark_level: int = 8,
                fraction: float = 0.98) -> bool:
    """畫面幾乎全黑 = PrintWindow 對這個客戶端沒作用。"""
    if frame is None or frame.size == 0:
        return True
    sample = frame[::4, ::4]
    return bool((sample.max(axis=2) < dark_level).mean() >= fraction)


def _crop(frame: np.ndarray, region: Optional[Region]) -> np.ndarray:
    if region is None:
        return frame
    x, y, w, h = region
    fh, fw = frame.shape[:2]
    if x < 0 or y < 0 or x + w > fw or y + h > fh:
        raise CaptureError(f"region {region} 超出畫面範圍 {fw}x{fh}")
    return frame[y:y + h, x:x + w].copy()


class WindowCapture:
    def __init__(self, title_substr: str, method: str = "auto", logger=None):
        import mss  # Windows 以外的環境可能沒有顯示裝置，延後 import/初始化

        self._title = title_substr
        self._sct = mss.mss()
        self._win: Optional[GameWindow] = None
        self._log = logger
        self.refresh()
        self.method = method if method in ("printwindow", "screen") else self._probe()
        self.fell_back = False    # printwindow 途中壞掉，已改用螢幕擷取

    def _probe(self) -> str:
        try:
            frame = grab_client(self._win)  # type: ignore[arg-type]
        except Exception:
            frame = None
        return "screen" if looks_blank(frame) else "printwindow"

    def resize_client(self, width: int, height: int):
        """把遊戲視窗調到 client 區剛好是指定大小；回傳調完的實際大小。"""
        assert self._win is not None
        win = resize_client(self._win, width, height)
        if win is not None:
            self._win = win
        return self._win.size

    def refresh(self) -> GameWindow:
        win = find_game_window(self._title)
        if win is None:
            raise CaptureError(
                f"找不到標題含「{self._title}」的視窗，請先開啟遊戲。"
                + _open_windows_hint())
        self._win = win
        return win

    @property
    def size(self):
        assert self._win is not None
        return self._win.size

    @property
    def origin(self):
        assert self._win is not None
        return self._win.origin

    def _grab_screen(self) -> np.ndarray:
        assert self._win is not None
        ox, oy = self._win.origin
        w, h = self._win.size
        shot = self._sct.grab({"left": ox, "top": oy, "width": w, "height": h})
        return np.asarray(shot)[:, :, :3].copy()  # BGRA -> BGR

    def grab(self, region: Optional[Region] = None) -> np.ndarray:
        assert self._win is not None
        if self.method == "printwindow":
            frame = grab_client(self._win)
            if frame is None:
                # PrintWindow 在這個客戶端不是穩定可用的：開場探測過得了關，
                # 跑一跑卻會整個壞掉（實測連續 40 次全失敗，視窗還好好開著、
                # 也沒有最小化）。這種時候拋例外等於讓掛了一整晚的 bot 收工，
                # 退回螢幕擷取至少還能繼續跑——只是遊戲不能被蓋住。
                time.sleep(0.05)
                frame = grab_client(self._win)
            if frame is None:
                self._fall_back_to_screen()
                frame = self._grab_screen()
        else:
            frame = self._grab_screen()
        return _crop(frame, region)

    def _fall_back_to_screen(self) -> None:
        self.method = "screen"
        if self.fell_back:
            return
        self.fell_back = True
        if self._log is not None:
            self._log.warning(
                "PrintWindow 中途失效，已改用螢幕擷取。"
                "從現在起**不要讓任何視窗蓋住遊戲畫面**，否則會拍到別的東西"
                "（血條讀到錯的顏色就會誤判成瀕死而停機）")


class ImageCapture:
    """用一張截圖模擬整個遊戲畫面，讓辨識與決策可以完全離線開發。"""

    def __init__(self, path: str):
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            raise CaptureError(f"讀不到圖片: {path}")
        self._img = img
        self.method = "image"

    @property
    def size(self):
        h, w = self._img.shape[:2]
        return (w, h)

    def grab(self, region: Optional[Region] = None) -> np.ndarray:
        return _crop(self._img, region).copy()
