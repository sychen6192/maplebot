"""小地圖辨識：玩家（黃點）與其他玩家（紅點）。

座標都是小地圖 ROI 內的 (x, y)。
"""
from typing import List, Optional, Tuple

import cv2
import numpy as np

from ..config import VisionCfg


def _color_mask(img_bgr: np.ndarray, rgb: Tuple[int, int, int], tol: int) -> np.ndarray:
    r, g, b = rgb
    target = np.array([b, g, r], dtype=np.int16)  # BGR
    diff = np.abs(img_bgr.astype(np.int16) - target)
    return np.all(diff <= tol, axis=2)


def find_player(minimap_bgr: np.ndarray, cfg: VisionCfg) -> Optional[Tuple[int, int]]:
    mask = _color_mask(minimap_bgr, cfg.minimap_player_rgb, cfg.color_tolerance)
    pts = np.argwhere(mask)
    if len(pts) < cfg.min_dot_pixels:
        return None
    cy, cx = pts.mean(axis=0)
    return (int(round(cx)), int(round(cy)))


def find_others(minimap_bgr: np.ndarray, cfg: VisionCfg) -> List[Tuple[int, int]]:
    mask = _color_mask(minimap_bgr, cfg.minimap_other_rgb, cfg.color_tolerance)
    if mask.sum() < cfg.min_dot_pixels:
        return []
    n, labels, stats, centroids = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8
    )
    out: List[Tuple[int, int]] = []
    for i in range(1, n):  # 0 是背景
        if stats[i, cv2.CC_STAT_AREA] >= cfg.min_dot_pixels:
            x, y = centroids[i]
            out.append((int(round(x)), int(round(y))))
    return out
