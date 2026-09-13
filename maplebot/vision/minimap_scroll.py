"""會捲動的小地圖：把「小地圖上的點」換算成「地圖上的位置」。

**問題**（issue #7）：大地圖的小地圖放不下整張圖，只顯示一個視窗、跟著角色
捲動。角色點於是一直待在小地圖中央附近——走過半張地圖，`minimap_xy` 卻幾乎
沒變。

後果全部是**安靜的**，這才是它難查的地方：

  * `route.compress()` 靠 x 走到底折返來找巡邏區間的端點。x 不動就沒有折返，
    錄一整趟出來是空的或只有一個點——回報者說的「錄製是錯的」就是這個
  * 巡邏時 bot 以為自己還沒到目標 x，於是往同一個方向走到撞牆為止
  * `_attack_stalled` 與卡住偵測也都看 `minimap_xy`，會以為角色從來沒動過

**做法**跟 `follower.py` 量鏡頭捲動同一招：對前後兩幀小地圖做相位相關
（phase correlation），拿到的是**地圖內容**的平移量，不需要任何配對。累積下來
就是「小地圖這個視窗滑到地圖的哪裡」，加上角色點在視窗內的位置，就得到穩定、
可以拿來錄路線的地圖座標。

**但角色點一定要先蓋掉**（見 `_blank_dots`）。原本以為那顆點才幾個 px、相關性
會由整片地形主導——實測是錯的：靜止的地圖上它是**唯一**會動的東西，於是相關性
整個跟著它跑。不捲的地圖讓角色來回走 120 幀，原點漂了 8.3px；把點蓋掉之後是
0.00px。一小時的量級就是上千 px。

**不捲的地圖完全不受影響**，而且是量出來的：真實小地圖（fixture）跑 120 幀，
原點漂移 0.00px。這是刻意的——這個修正是為了救一種目前壞掉的地圖，不能讓現在
好好的那些冒險。也因為原點是連續累積的（不是「偵測到才切換」），捲動地圖上的
座標從第一幀就連續，不會有跳點被 `track.py` 的軌跡追蹤當成假候選擋掉。

實測（把 fixture 的小地圖橫向接成大地圖，視窗每幀捲 4px）：30 幀捲了 116px，
最大誤差 2px。單幀位移 1~8px 的量測誤差都在 ±0.05px 以內。

成本：128x58 的小地圖上 0.087 ms/幀（暖機後），可以忽略。
"""
from typing import Optional, Tuple

import cv2
import numpy as np

# 相位相關的信心下限。低於這個值代表前後兩幀根本不像（換圖、讀圖畫面），
# 這時量到的位移沒有意義——重新開始，不要把垃圾累積進原點。
MIN_RESPONSE = 0.15

# 小於這個位移一律當成 0。相位相關在完全靜止的畫面上也會回傳 ±0.1 px 級的
# 雜訊，一秒 8 幀、掛一整晚就是可觀的漂移，而那會發生在**不捲動**的地圖上
# ——等於把好好的地圖弄壞。
DEADBAND_PX = 0.35

# 單幀位移超過這個值就不信。小地圖一幀不可能捲這麼多，那是換圖或畫面重繪。
MAX_STEP_PX = 40.0

# 累積捲動超過這麼多 px 才承認「這張地圖的小地圖會捲」。純粹用來提示使用者，
# 不影響座標換算（換算從第一幀就在做）。
DETECT_PX = 24.0

# 比對之前要把角色點蓋掉多大一塊。
# **這一步是必要的，不是最佳化**：角色點是靜止地圖上唯一會動的東西，於是相位
# 相關會去追它。實測不捲的地圖、角色來回走 120 幀，原點漂了 8.3px；把點拿掉
# 之後是 0.00px。一小時的量級就是上千 px——等於把本來好好的地圖弄壞。
DOT_MASK_PX = 11


class MinimapScroll:
    """追蹤小地圖視窗在地圖上的位置。

    每個 tick 呼叫一次 `update(minimap_bgr, dot_xy)`，拿回穩定的地圖座標。
    """

    def __init__(self, min_response: float = MIN_RESPONSE,
                 deadband: float = DEADBAND_PX,
                 max_step: float = MAX_STEP_PX,
                 detect_px: float = DETECT_PX):
        self.min_response = min_response
        self.deadband = deadband
        self.max_step = max_step
        self.detect_px = detect_px
        self.dot_mask = DOT_MASK_PX
        self._prev: Optional[np.ndarray] = None
        self._prev_dot: Optional[Tuple[int, int]] = None
        self._window: Optional[np.ndarray] = None
        self.origin = (0.0, 0.0)       # 小地圖左上角在地圖座標系的位置
        self.travel = 0.0              # 累積捲動距離（判斷這張圖會不會捲）
        self.resets = 0                # 相關性太低而重來的次數（換圖）
        self.last_shift = (0.0, 0.0)   # debug 用

    @property
    def scrolling(self) -> bool:
        """這張地圖的小地圖會不會捲動。只用來提示，不影響換算。"""
        return self.travel >= self.detect_px

    def reset(self) -> None:
        """換圖時重來。原點歸零——地圖座標本來就只要求「穩定」，不要求絕對值。"""
        self._prev = None
        self._prev_dot = None
        self.origin = (0.0, 0.0)
        self.travel = 0.0

    def _blank_dots(self, gray: np.ndarray, dots) -> np.ndarray:
        """把角色點蓋掉——**前後兩幀都蓋同樣的位置**。

        只蓋當下那一幀沒有用：兩幀被蓋掉的區塊位置不同，那塊差異本身就是一個
        會移動的東西，相關性照樣會去追它。所以要蓋「上一幀的點」與「這一幀的
        點」兩處，讓兩張圖被挖掉的地方完全一致，剩下的差異才純粹是地形。

        填的值用整張圖的平均而不是 0：填 0 會在兩幀同一個位置造出一組高對比
        方塊，那會把相關峰往「位移 0」拉。
        """
        if self.dot_mask <= 0:
            return gray            # 0 = 不遮（測試用來證明遮罩是必要的）
        out = gray
        fill = float(gray.mean())
        r = max(self.dot_mask // 2, 1)
        h, w = gray.shape[:2]
        for d in dots:
            if d is None:
                continue
            x, y = int(d[0]), int(d[1])
            x0, y0 = max(x - r, 0), max(y - r, 0)
            x1, y1 = min(x + r + 1, w), min(y + r + 1, h)
            if x1 <= x0 or y1 <= y0:
                continue
            if out is gray:
                out = gray.copy()
            out[y0:y1, x0:x1] = fill
        return out

    def _prepare(self, minimap_bgr: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(minimap_bgr, cv2.COLOR_BGR2GRAY) \
            if minimap_bgr.ndim == 3 else minimap_bgr
        return gray.astype(np.float32)

    def update(self, minimap_bgr: np.ndarray,
               dot: Optional[Tuple[int, int]]) -> Optional[Tuple[int, int]]:
        """回傳角色的**地圖座標**；dot 是 None 就回 None。

        即使 dot 是 None（這一幀沒認到角色點）也照樣更新原點——地圖還在捲，
        漏掉幾幀不追的話原點就對不上了。
        """
        if minimap_bgr is None or minimap_bgr.size == 0:
            return dot
        gray = self._prepare(minimap_bgr)

        if self._prev is None or self._prev.shape != gray.shape:
            # 第一幀，或小地圖大小變了（展開/收合）——沒有可比對的基準
            self._prev, self._prev_dot = gray, dot
            self._window = cv2.createHanningWindow(
                (gray.shape[1], gray.shape[0]), cv2.CV_32F)
            return self._world(dot)

        pair = (self._prev_dot, dot)
        (dx, dy), response = cv2.phaseCorrelate(
            self._blank_dots(self._prev, pair),
            self._blank_dots(gray, pair), self._window)
        self._prev, self._prev_dot = gray, dot
        self.last_shift = (dx, dy)

        if response < self.min_response or \
                abs(dx) > self.max_step or abs(dy) > self.max_step:
            # 換圖、讀圖黑畫面、或小地圖被蓋住：這一步不可信。累積下去會讓
            # 原點永久偏掉，而那是**不可逆**的錯——寧可重來。
            self.resets += 1
            self.origin = (0.0, 0.0)
            self.travel = 0.0
            return self._world(dot)

        if abs(dx) < self.deadband:
            dx = 0.0
        if abs(dy) < self.deadband:
            dy = 0.0
        # 地圖內容往右滑 = 視窗往左移，所以原點的變化跟內容位移反號
        self.origin = (self.origin[0] - dx, self.origin[1] - dy)
        self.travel += abs(dx) + abs(dy)
        return self._world(dot)

    def _world(self, dot: Optional[Tuple[int, int]]) -> Optional[Tuple[int, int]]:
        if dot is None:
            return None
        return (int(round(dot[0] + self.origin[0])),
                int(round(dot[1] + self.origin[1])))

    def explain(self) -> str:
        if not self.scrolling:
            return "小地圖沒有捲動（座標即小地圖座標）"
        return (f"小地圖會捲動：視窗已滑到 ({self.origin[0]:+.0f}, "
                f"{self.origin[1]:+.0f})，累積 {self.travel:.0f}px"
                + (f"，重來過 {self.resets} 次" if self.resets else ""))
