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


def color_mask(bgr: np.ndarray, color: str) -> np.ndarray:
    """哪些像素屬於這個 bar 的顏色（布林遮罩）。

    讀比例與自動校正共用同一份判色——分成兩套遲早會漂掉，變成「校正框到的
    東西跟讀值認得的顏色不一樣」這種很難查的問題。
    """
    if color not in COLOR_PRESETS:
        raise ValueError(f"未知的 bar 顏色 {color!r}，可用: {list(COLOR_PRESETS)}")
    (rmin, rmax), (gmin, gmax), (bmin, bmax) = COLOR_PRESETS[color]
    b = bgr[:, :, 0].astype(np.int16)
    g = bgr[:, :, 1].astype(np.int16)
    r = bgr[:, :, 2].astype(np.int16)
    return (
        (r >= rmin) & (r <= rmax)
        & (g >= gmin) & (g <= gmax)
        & (b >= bmin) & (b <= bmax)
    )


def bar_ratio(bar_bgr: np.ndarray, color: str) -> Optional[float]:
    """回傳 0.0~1.0；ROI 無效（零面積）才回 None。

    注意：找不到任何填色一律回 0.0 而不是 None——寧可把「ROI 框錯」
    當成低血量觸發停機，也不能把真的快死掉誤判成視覺失效而繼續打。
    """
    if color not in COLOR_PRESETS:
        raise ValueError(f"未知的 bar 顏色 {color!r}，可用: {list(COLOR_PRESETS)}")
    if bar_bgr.size == 0:
        return None
    col_fill = color_mask(bar_bgr, color).mean(axis=0)
    filled = np.nonzero(col_fill >= _COLUMN_FILL_MIN)[0]
    if len(filled) == 0:
        return 0.0
    return float((filled[-1] + 1) / bar_bgr.shape[1])


class BarFilter:
    """血條讀值的去雜訊：暴跌要下一幀再確認一次才算數。

    被怪撞到時血條會閃一下（整條被特效蓋掉／清空），那一幀顏色遮罩什麼都
    抓不到，bar_ratio 就回 0.0——跟「真的沒血」長得一模一樣。下游看到 0%
    的後果是灌藥＋判定瀕死停機，掛整晚就這樣結束在第一次被撞到。

    真實傷害會**留在那裡**，閃爍不會：所以一幀之內掉超過 max_drop 時先沿用
    上一個讀值，連續 confirm 幀都還是低才承認。代價是真的被爆擊時晚一兩幀
    才反應（8 fps 約 0.25 秒），換掉整晚白掛很划算。

    上升與小幅下降一律立刻採用——延遲回報「血變多了」沒有任何好處。
    """

    def __init__(self, max_drop: float = 0.35, confirm: int = 2):
        self.max_drop = max_drop
        self.confirm = max(confirm, 1)
        self.last: Optional[float] = None
        self.pending = 0
        self.suppressed = 0        # 擋掉幾次疑似誤讀（給 runner 回報用）

    def reset(self) -> None:
        self.last = None
        self.pending = 0

    def update(self, raw: Optional[float]) -> Optional[float]:
        if raw is None:
            return self.last          # ROI 讀不到就沿用，不要往下游丟 None
        if self.last is None or raw >= self.last - self.max_drop:
            self.last, self.pending = raw, 0
            return raw
        self.pending += 1
        if self.pending >= self.confirm:
            self.last, self.pending = raw, 0
            return raw
        self.suppressed += 1
        return self.last              # 先沿用上一個值，等下一幀確認
