"""HP / MP / EXP 條比例辨識。

做法：對整條 bar 的 ROI 做顏色遮罩，逐欄統計「該欄有多少列是目標色」，
再取最右邊有填色的欄位位置換算比例。文字覆蓋在條上只會造成細縫，
用「欄內比例門檻」即可忽略。
"""
from typing import Dict, Optional, Tuple

import numpy as np

# RGB 各通道的 (min, max)；涵蓋楓谷經典 UI 的紅/藍/黃綠條
COLOR_PRESETS: Dict[str, Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int]]] = {
    "red":    ((140, 255), (0, 120), (0, 120)),
    "blue":   ((0, 130),   (0, 170), (140, 255)),
    "yellow": ((150, 255), (130, 255), (0, 130)),
    "green":  ((0, 130),   (140, 255), (0, 140)),
}

# 欄內至少要有這個比例的像素是目標色，該欄才算「有填色」
_COLUMN_FILL_MIN = 0.30


def bar_ratio(bar_bgr: np.ndarray, color: str) -> Optional[float]:
    """回傳 0.0~1.0；ROI 無效（零面積）才回 None。

    注意：找不到任何填色一律回 0.0 而不是 None——寧可把「ROI 框錯」
    當成低血量觸發停機，也不能把真的快死掉誤判成視覺失效而繼續打。
    """
    if color not in COLOR_PRESETS:
        raise ValueError(f"未知的 bar 顏色 {color!r}，可用: {list(COLOR_PRESETS)}")
    if bar_bgr.size == 0:
        return None
    (rmin, rmax), (gmin, gmax), (bmin, bmax) = COLOR_PRESETS[color]
    b = bar_bgr[:, :, 0].astype(np.int16)
    g = bar_bgr[:, :, 1].astype(np.int16)
    r = bar_bgr[:, :, 2].astype(np.int16)
    mask = (
        (r >= rmin) & (r <= rmax)
        & (g >= gmin) & (g <= gmax)
        & (b >= bmin) & (b <= bmax)
    )
    col_fill = mask.mean(axis=0)
    filled = np.nonzero(col_fill >= _COLUMN_FILL_MIN)[0]
    if len(filled) == 0:
        return 0.0
    return float((filled[-1] + 1) / bar_bgr.shape[1])
