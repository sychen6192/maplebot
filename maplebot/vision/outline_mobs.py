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


# 所有尺寸類參數都是對這個參考寬度校正的；實際畫面較大時等比例放大。
# 楓谷經典版 800x600 視窗扣掉 UI 後的 playfield 大約就是這個寬度。
REFERENCE_WIDTH = 790


class OutlineMobDetector:
    """靠黑色描邊找怪。

    面積與核心大小都跟畫面解析度相關：同一隻怪在 2554px 寬的視窗裡，
    描邊團塊會比 790px 視窗大好幾倍。auto_scale 會依實際畫面寬度等比例
    調整門檻，所以同一組設定在不同視窗大小下都能用。
    """

    def __init__(self, black_level: int = 8, min_area: int = 300,
                 max_area: int = 20000, close_kernel: int = 20,
                 player_box: Tuple[int, int] = (100, 140),
                 min_size: Tuple[int, int] = (18, 18),
                 auto_scale: bool = True):
        self.black_level = black_level
        self.min_area = min_area
        self.max_area = max_area
        self.close_kernel = max(close_kernel, 1)
        self.player_box = player_box
        self.min_size = min_size
        self.auto_scale = auto_scale

    def _scaled(self, frame_width: int):
        """回傳依畫面寬度縮放後的 (min_area, max_area, kernel, player_box, min_size)。"""
        if not self.auto_scale or frame_width <= 0:
            return (self.min_area, self.max_area, self.close_kernel,
                    self.player_box, self.min_size)
        s = frame_width / REFERENCE_WIDTH
        return (
            int(self.min_area * s),
            int(self.max_area * s),
            max(int(round(self.close_kernel * s)), 3),
            (int(self.player_box[0] * s), int(self.player_box[1] * s)),
            (int(self.min_size[0] * s), int(self.min_size[1] * s)),
        )

    def detect(self, playfield_bgr: np.ndarray) -> List[Mob]:
        if playfield_bgr.size == 0:
            return []
        h, w = playfield_bgr.shape[:2]
        min_area, max_area, close_kernel, player_box, min_size = self._scaled(w)

        if self.black_level <= 0:
            mask = np.all(playfield_bgr == 0, axis=2)
        else:
            mask = playfield_bgr.max(axis=2) <= self.black_level
        mask = mask.astype(np.uint8) * 255

        # 鏡頭永遠跟著角色，所以玩家自己固定在畫面中央——挖掉免得自我偵測
        pw, ph = player_box
        cx, cy = w // 2, h // 2
        mask[max(cy - ph // 2, 0):cy + ph // 2,
             max(cx - pw // 2, 0):cx + pw // 2] = 0

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                           (close_kernel, close_kernel))
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        n, _, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
        mobs: List[Mob] = []
        for i in range(1, n):   # 0 是背景
            x, y, bw, bh, area = stats[i]
            if not (min_area <= area <= max_area):
                continue
            if bw < min_size[0] or bh < min_size[1]:
                continue
            mobs.append(Mob(cx=int(x + bw // 2), cy=int(y + bh // 2),
                            w=int(bw), h=int(bh), score=1.0, name="mob"))
        return mobs
