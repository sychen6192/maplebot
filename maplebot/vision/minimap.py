"""小地圖辨識：玩家（黃點）與其他玩家（紅點）。

玩家偵測兩段式（參考 auto-maple）：
1. 有 minimap_player.png 模板就先用模板匹配（最穩，不怕地形同色）
2. 否則用顏色遮罩 + 連通元件，只挑「面積像一個點」的色塊——
   面積上限擋掉黃色地形（自由市場這類小地圖地板就是黃的）

座標都是小地圖 ROI 內的 (x, y)。
"""
from typing import List, Optional, Tuple

import cv2
import numpy as np

from ..config import VisionCfg

PLAYER_TEMPLATE_THRESHOLD = 0.75


def _color_mask(img_bgr: np.ndarray, rgb: Tuple[int, int, int], tol: int) -> np.ndarray:
    r, g, b = rgb
    target = np.array([b, g, r], dtype=np.int16)  # BGR
    diff = np.abs(img_bgr.astype(np.int16) - target)
    return np.all(diff <= tol, axis=2)


def _dot_blobs(mask: np.ndarray, min_px: int, max_px: int) -> List[Tuple[int, int, int]]:
    """回傳 (x, y, area)，只保留面積介於點大小範圍內的色塊。"""
    n, _, stats, centroids = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8
    )
    out = []
    for i in range(1, n):  # 0 是背景
        area = int(stats[i, cv2.CC_STAT_AREA])
        if min_px <= area <= max_px:
            x, y = centroids[i]
            out.append((int(round(x)), int(round(y)), area))
    return out


def find_player(minimap_bgr: np.ndarray, cfg: VisionCfg,
                template: Optional[np.ndarray] = None) -> Optional[Tuple[int, int]]:
    if template is not None and \
            minimap_bgr.shape[0] >= template.shape[0] and \
            minimap_bgr.shape[1] >= template.shape[1]:
        gray = cv2.cvtColor(minimap_bgr, cv2.COLOR_BGR2GRAY)
        res = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if score >= PLAYER_TEMPLATE_THRESHOLD:
            return (loc[0] + template.shape[1] // 2, loc[1] + template.shape[0] // 2)

    mask = _color_mask(minimap_bgr, cfg.minimap_player_rgb, cfg.color_tolerance)
    blobs = _dot_blobs(mask, cfg.min_dot_pixels, cfg.max_dot_pixels)
    if not blobs:
        return None
    x, y, _ = max(blobs, key=lambda b: b[2])
    return (x, y)


def find_others(minimap_bgr: np.ndarray, cfg: VisionCfg) -> List[Tuple[int, int]]:
    mask = _color_mask(minimap_bgr, cfg.minimap_other_rgb, cfg.color_tolerance)
    if mask.sum() < cfg.min_dot_pixels:
        return []
    return [(x, y) for x, y, _ in
            _dot_blobs(mask, cfg.min_dot_pixels, cfg.max_dot_pixels)]
