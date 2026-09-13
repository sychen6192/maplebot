"""小地圖自動定位（參考 auto-maple 的 corner-template 校正法）。

使用者截一次小地圖「左上角」與「右下角」的框角模板後，
每次啟動用模板匹配自動找出小地圖 ROI——小地圖被拖動、
展開收合、換解析度都不用重新手動校正。
"""
import os
from typing import Optional, Tuple

import cv2
import numpy as np

from ..config import Region
from . import match

TL_NAME = "minimap_tl.png"
BR_NAME = "minimap_br.png"
PLAYER_NAME = "minimap_player.png"

MATCH_THRESHOLD = 0.6


def load_ui_template(ui_dir: str, name: str) -> Optional[np.ndarray]:
    """載入使用者截的 UI 模板。**讀彩色**——比對要用到顏色（見 vision/match.py）。

    以前這裡讀灰階，於是小地圖上黃點/紅點/藍點被壓成差不多一樣亮，模板比對
    跟背景的差距只有 +0.055；改成彩色之後是 +0.36。
    """
    path = os.path.join(ui_dir, name)
    if not os.path.exists(path):
        return None
    return cv2.imread(path, cv2.IMREAD_COLOR)


def _best_match(img: np.ndarray, tpl: np.ndarray) -> Tuple[float, Tuple[int, int]]:
    hit = match.match(img, tpl)
    return hit if hit is not None else (-1.0, (0, 0))


def find_minimap(frame_bgr: np.ndarray, tl_tpl: np.ndarray, br_tpl: np.ndarray,
                 border: int = 6) -> Optional[Region]:
    """用左上/右下角落模板找出小地圖內部區域；找不到（分數過低）回 None。"""
    if frame_bgr.shape[0] < max(tl_tpl.shape[0], br_tpl.shape[0]) or \
            frame_bgr.shape[1] < max(tl_tpl.shape[1], br_tpl.shape[1]):
        return None

    tl_score, tl = _best_match(frame_bgr, tl_tpl)
    br_score, br = _best_match(frame_bgr, br_tpl)
    if tl_score < MATCH_THRESHOLD or br_score < MATCH_THRESHOLD:
        return None

    x1 = tl[0] + border
    y1 = tl[1] + border
    x2 = br[0] + br_tpl.shape[1] - border
    y2 = br[1] + br_tpl.shape[0] - border
    if x2 - x1 < 20 or y2 - y1 < 15:   # 兩個角配對出來的區域太小 = 誤匹配
        return None
    return (x1, y1, x2 - x1, y2 - y1)
