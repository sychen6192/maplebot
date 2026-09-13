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


def _dot_blobs(mask: np.ndarray, min_px: int, max_px: int,
               merge_gap: int = 3) -> List[Tuple[int, int, int]]:
    """回傳 (x, y, area)，只保留面積介於點大小範圍內的色塊。

    **先把碎塊接回去再量面積**。面積上限本來就是為了擋掉同色地形（自由市場
    的小地圖地板就是黃的），但它只在地形是一整塊時有效——實際畫面不是。
    拿 tests/fixtures/mapleaga_800x600.jpg 量：那片黃地板在嚴格比色下碎成
    89 個小塊，每一塊 1~21px 全部落在點大小範圍內，面積上限一次都沒生效；
    而真正的玩家點只有 5px，於是 find_player 的「取面積最大的」保證挑到地板
    （實測回報 (11,45)，人在 (95,11)）。

    膨脹 merge_gap 再做連通元件，那整片地板就連回一塊、面積爆表被擋掉；
    玩家點四周沒有同色像素，膨脹後仍然自成一塊。實測候選從 96 個降到 3 個，
    而且最大的那個就是玩家點。

    面積與重心一律用**原始**遮罩算——膨脹只用來判斷誰跟誰算同一塊，
    拿膨脹後的形狀算重心會把點的位置往鄰近雜訊的方向拉偏。

    merge_gap <= 1 等於關掉這個合併（回到舊行為）。
    """
    m = mask.astype(np.uint8)
    grown = cv2.dilate(m, np.ones((merge_gap, merge_gap), np.uint8)) \
        if merge_gap > 1 else m
    n, labels, _, _ = cv2.connectedComponentsWithStats(grown, connectivity=8)

    ys, xs = np.nonzero(m)
    if xs.size == 0:
        return []
    # labels[m > 0] 與 np.nonzero(m) 都是列優先，所以兩邊逐項對應
    lab = labels[m > 0]
    area = np.bincount(lab, minlength=n)
    sum_x = np.bincount(lab, weights=xs, minlength=n)
    sum_y = np.bincount(lab, weights=ys, minlength=n)

    out = []
    for i in range(1, n):  # 0 是背景
        a = int(area[i])
        if min_px <= a <= max_px:
            out.append((int(round(sum_x[i] / a)), int(round(sum_y[i] / a)), a))
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
    blobs = _dot_blobs(mask, cfg.min_dot_pixels, cfg.max_dot_pixels,
                       cfg.minimap_merge_gap)
    if not blobs:
        return None
    x, y, _ = max(blobs, key=lambda b: b[2])
    return (x, y)


def find_others(minimap_bgr: np.ndarray, cfg: VisionCfg) -> List[Tuple[int, int]]:
    mask = _color_mask(minimap_bgr, cfg.minimap_other_rgb, cfg.color_tolerance)
    if mask.sum() < cfg.min_dot_pixels:
        return []
    return [(x, y) for x, y, _ in
            _dot_blobs(mask, cfg.min_dot_pixels, cfg.max_dot_pixels,
                       cfg.minimap_merge_gap)]
