"""零設定怪物偵測：靠 sprite 的黑色描邊找怪。

不用模板、不用訓練、不用標註——換地圖換怪都能直接用。

原理：楓谷的角色與怪物 sprite 都有純黑(0,0,0)描邊，而背景地形、天空、
平台幾乎不含純黑像素。所以：
  1. 取出黑色像素遮罩
  2. 把畫面中央（玩家自己）挖掉，免得把自己當成怪
  3. 形態學閉合，把斷斷續續的描邊連成一整塊
  4. 連通元件分析，面積落在合理範圍的就是怪

做法參考 MapleStoryAutoLevelUp（356★，同為楓之谷 Artale）的
`template_free` 模式。

注意：JPEG 壓縮會破壞純黑，所以離線用截圖測試時要把 black_level 調高
（12~20）；即時擷取是無損的，用 0~8 最準。
"""
from typing import List, Tuple

import cv2
import numpy as np

from .mobs import Mob


class OutlineMobDetector:
    def __init__(self, black_level: int = 8, min_area: int = 800,
                 max_area: int = 40000, close_kernel: int = 20,
                 player_box: Tuple[int, int] = (100, 140),
                 min_size: Tuple[int, int] = (18, 18)):
        self.black_level = black_level
        self.min_area = min_area
        self.max_area = max_area
        self.close_kernel = max(close_kernel, 1)
        self.player_box = player_box
        self.min_size = min_size

    def detect(self, playfield_bgr: np.ndarray) -> List[Mob]:
        if playfield_bgr.size == 0:
            return []
        h, w = playfield_bgr.shape[:2]

        if self.black_level <= 0:
            mask = np.all(playfield_bgr == 0, axis=2)
        else:
            mask = playfield_bgr.max(axis=2) <= self.black_level
        mask = mask.astype(np.uint8) * 255

        # 鏡頭永遠跟著角色，所以玩家自己固定在畫面中央——挖掉免得自我偵測
        pw, ph = self.player_box
        cx, cy = w // 2, h // 2
        mask[max(cy - ph // 2, 0):cy + ph // 2,
             max(cx - pw // 2, 0):cx + pw // 2] = 0

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                           (self.close_kernel, self.close_kernel))
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        n, _, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
        mobs: List[Mob] = []
        for i in range(1, n):   # 0 是背景
            x, y, bw, bh, area = stats[i]
            if not (self.min_area <= area <= self.max_area):
                continue
            if bw < self.min_size[0] or bh < self.min_size[1]:
                continue
            mobs.append(Mob(cx=int(x + bw // 2), cy=int(y + bh // 2),
                            w=int(bw), h=int(bh), score=1.0, name="mob"))
        return mobs
