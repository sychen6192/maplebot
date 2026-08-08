"""濾掉「跟著角色跑」的東西——主要是寵物。

寵物也有黑色描邊，描邊偵測分不出牠跟怪。判別依據是鏡頭：楓谷的鏡頭跟著
角色，所以**鏡頭捲動時世界上的怪會在畫面上滑動**，**寵物卻一直待在角色
旁邊、畫面位置幾乎不變**。

關鍵在於「角色移動」不等於「鏡頭捲動」。拿小地圖的移動當作鏡頭捲動會在
這三種常見情況下把整場的怪都判成寵物（實測就是這樣變成完全不攻擊的）：

  * 小地圖玩家點本身會抖 1~2 px，站著不動也像在走
  * 地圖只有一兩個畫面寬時鏡頭根本不捲，走到邊緣鏡頭也會卡住
  * 畫面上有一排等距的怪時，逐幀最近鄰配對會接錯目標，量出來的位移是 0

所以鏡頭位移改成**直接從畫面量**：對前後兩幀做相位相關（phase correlation），
拿到的是整個背景的平移量，不需要任何配對，也不會被上面三種情況騙。
量不到（相關性太低）或位移太小就整幀不判斷——寧可判不出寵物，也不要把怪
判成寵物：判錯的代價是整場不攻擊，嚴重得多。

有了可信的鏡頭位移 (dx, dy) 之後，判斷就只是一句話：

    目標出現在「上一次的某個目標所在位置」，而且**不是**由某個目標滑過來的
    -> 它沒跟著鏡頭走 -> 跟隨物

兩種解釋都成立（正好有另一隻怪滑到它原本的位置）時就不計分，避免誤判。

最後一道保險：確認的跟隨物超過 follower_max 隻就整組撤銷——寵物只有一隻，
一次冒出四隻代表這套判斷在這張圖上不成立。
"""
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .mobs import Mob


class FollowerFilter:
    def __init__(self, min_shift_px: int = 90, hits_needed: int = 3,
                 tol_px: int = 45, max_followers: int = 2,
                 min_response: float = 0.12, scale_width: int = 256):
        self.min_shift_px = min_shift_px   # 鏡頭至少滑這麼多（原尺寸 px）才敢判斷
        self.hits_needed = hits_needed
        self.tol_px = tol_px               # 兩個位置差多少內算「同一個地方」
        self.max_followers = max_followers
        self.min_response = min_response   # 相位相關的信心值下限
        self.scale_width = scale_width     # 相位相關前先縮到這個寬度（省時間）
        self._anchor: Optional[np.ndarray] = None      # 上次計分那一幀（灰階、縮小）
        self._anchor_dets: List[Tuple[int, int]] = []  # 上次計分那一幀的偵測位置
        self._scale = 1.0
        self._window: Optional[np.ndarray] = None
        self._cands: List[List[int]] = []  # [x, y, hits]，hits 夠了就是跟隨物
        self.last_shift: Tuple[float, float] = (0.0, 0.0)   # debug 用

    def reset(self) -> None:
        self._anchor = None
        self._anchor_dets = []
        self._cands = []

    # ---- 鏡頭位移 ----

    def _prepare(self, playfield: np.ndarray) -> Tuple[np.ndarray, float]:
        h, w = playfield.shape[:2]
        scale = self.scale_width / w if w > self.scale_width else 1.0
        img = cv2.resize(playfield, (int(w * scale), int(h * scale))) \
            if scale < 1.0 else playfield
        if img.ndim == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return img.astype(np.float32), scale

    def _camera_shift(self, gray: np.ndarray) -> Optional[Tuple[float, float]]:
        """回傳背景從錨點幀到現在的平移量（原尺寸 px）。量不準就回 None。"""
        if self._anchor is None or self._anchor.shape != gray.shape:
            return None
        if self._window is None or self._window.shape != gray.shape:
            self._window = cv2.createHanningWindow(
                (gray.shape[1], gray.shape[0]), cv2.CV_32F)
        (dx, dy), response = cv2.phaseCorrelate(self._anchor, gray, self._window)
        if response < self.min_response:
            return None
        return dx / self._scale, dy / self._scale

    # ---- 判斷 ----

    @staticmethod
    def _near(points: Sequence[Tuple[int, int]], x: float, y: float, tol: int) -> bool:
        return any(abs(px - x) <= tol and abs(py - y) <= tol for px, py in points)

    def _bump(self, x: int, y: int) -> None:
        for c in self._cands:
            if abs(c[0] - x) <= self.tol_px and abs(c[1] - y) <= self.tol_px:
                c[0], c[1] = x, y
                c[2] = min(c[2] + 1, self.hits_needed + 2)
                return
        self._cands.append([x, y, 1])

    def _score(self, dets: Sequence[Tuple[int, int]], dx: float, dy: float) -> None:
        seen = []
        for x, y in dets:
            here = self._near(self._anchor_dets, x, y, self.tol_px)
            slid = self._near(self._anchor_dets, x - dx, y - dy, self.tol_px)
            if here and not slid:          # 只有「沒跟著滑」解釋得通
                seen.append((x, y))
        for x, y in seen:
            self._bump(x, y)
        for c in self._cands:              # 這一輪沒觀察到的慢慢退回去
            if not self._near(seen, c[0], c[1], self.tol_px):
                c[2] -= 1
        self._cands = [c for c in self._cands if c[2] > 0]
        if sum(c[2] >= self.hits_needed for c in self._cands) > self.max_followers:
            self._cands = []               # 判出一堆 = 判斷失準，整組撤銷

    def _split(self, mobs: List[Mob]) -> Tuple[List[Mob], List[Mob]]:
        kept: List[Mob] = []
        followers: List[Mob] = []
        confirmed = [c for c in self._cands if c[2] >= self.hits_needed]
        for mob in mobs:
            hit = None
            for c in confirmed:
                if abs(c[0] - mob.cx) <= self.tol_px and abs(c[1] - mob.cy) <= self.tol_px:
                    hit = c
                    break
            if hit is None:
                kept.append(mob)
            else:
                hit[0], hit[1] = mob.cx, mob.cy    # 跟著寵物飄移
                followers.append(mob)
        return kept, followers

    def filter(self, mobs: List[Mob], playfield: Optional[np.ndarray] = None,
               player_moved: bool = True) -> Tuple[List[Mob], List[Mob]]:
        """回傳 (要打的, 判定為跟隨物的)。

        playfield 是這一幀的主畫面（量鏡頭位移用）。沒有畫面就不做任何新判斷，
        只沿用既有結果。player_moved 只是省 CPU 的前置判斷，真正的依據是畫面。
        """
        dets = [(m.cx, m.cy) for m in mobs]
        if playfield is not None and playfield.size:
            gray, scale = self._prepare(playfield)
            if self._anchor is None:
                self._anchor, self._scale, self._anchor_dets = gray, scale, dets
            elif player_moved:
                shift = self._camera_shift(gray)
                if shift is None:
                    # 相關性太低（畫面整個換掉了）——舊錨點沒有參考價值
                    self._anchor, self._scale, self._anchor_dets = gray, scale, dets
                else:
                    self.last_shift = shift
                    dx, dy = shift
                    if abs(dx) + abs(dy) >= self.min_shift_px:
                        self._score(dets, dx, dy)
                        self._anchor, self._scale = gray, scale
                        self._anchor_dets = dets
        return self._split(mobs)
