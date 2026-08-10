"""自動找出 HP / MP / EXP 條的 ROI —— 換解析度不用重新手動框。

**為什麼需要**：regions 是照某一個視窗大小量的，遊戲解析度一改全部錯位，
血條讀成 0% 就會灌兩瓶藥再判定瀕死停機。原本唯一的解法是重跑 calibrate.py
用滑鼠拉五個框，換一次解析度就要重來一次。

**怎麼找**：只找「最大的紅/藍/黃色塊」會被右下角那幾顆彩色按鈕跟粉紅色
聊天列騙走（實測就是這樣失敗的）。真正認得出來的是**三條 bar 的組合關係**：
高度相近、y 幾乎同一條線、由左到右紅→藍→黃。按鈕跟聊天列湊不出這個組合。

找到有顏色的那一段之後還要往左右擴到外框：只框有顏色的部分，血量剩 60%
就會把那 60% 當成整條，永遠讀成 100%。
"""
import itertools
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from ..config import Region
from .status import color_mask

# 外框判定：三通道最大值低於此值視為框線（bar 的邊框是深色的）
BORDER_LEVEL = 70
BAR_H = (5, 26)          # bar 的高度範圍
MIN_RUN_W = 8            # 有顏色的那段至少要這麼寬（擋雜訊）
MIN_ASPECT = 3.0         # 又扁又長才可能是 bar，按鈕接近正方形
ROW_TOL = 8              # 三條 bar 的 y 中心差多少內算「同一排」
SEARCH_BAND = 0.12       # 只找畫面最下面這個比例的範圍（狀態列固定在底部）

ORDER = (("hp_bar", "red"), ("mp_bar", "blue"), ("exp_bar", "yellow"))


def _candidates(roi: np.ndarray, color: str) -> List[Tuple[int, int, int, int]]:
    mask = color_mask(roi, color).astype(np.uint8) * 255
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, _ = stats[i]
        if not (BAR_H[0] <= h <= BAR_H[1]) or w < MIN_RUN_W:
            continue
        # 這裡**不能**要求又扁又長：血剩 10% 時有顏色的那段又短又胖，
        # 一卡在這裡就整條 bar 都找不到了。形狀等擴到外框之後再驗。
        out.append((int(x), int(y), int(w), int(h)))
    return out


def _expand_to_border(roi: np.ndarray, box) -> Tuple[int, int, int, int]:
    """沿著中心列往左右走到外框為止，把「空的那段」也含進來。"""
    x, y, w, h = box
    row = roi[y + h // 2]
    left = x
    while left > 0 and row[left - 1].max() >= BORDER_LEVEL:
        left -= 1
    right = x + w
    while right < roi.shape[1] and row[right].max() >= BORDER_LEVEL:
        right += 1
    return (left, y, right - left, h)


def find_status_bars(frame_bgr: np.ndarray) -> Optional[Dict[str, Region]]:
    """回傳 {"hp_bar": (x,y,w,h), ...}；認不出來回 None。"""
    if frame_bgr.size == 0:
        return None
    fh, fw = frame_bgr.shape[:2]
    top = int(fh * (1.0 - SEARCH_BAND))
    roi = frame_bgr[top:fh]
    # 先擴到外框再驗形狀：整條 bar 一定是又扁又長，但**有顏色的那一段**在
    # 血量低的時候又短又胖，先驗形狀會把它濾掉。按鈕擴不出長條（周圍就是
    # 深色底，一步就停），所以擋得掉。
    cands = {}
    for _, color in ORDER:
        boxes = {_expand_to_border(roi, b) for b in _candidates(roi, color)}
        cands[color] = [b for b in boxes if b[2] >= b[3] * MIN_ASPECT]
    if not all(cands.values()):
        return None

    best = None
    for combo in itertools.product(*(cands[color] for _, color in ORDER)):
        ys = [b[1] + b[3] / 2 for b in combo]
        spread = max(ys) - min(ys)
        if spread > ROW_TOL:
            continue
        if not (combo[0][0] < combo[1][0] < combo[2][0]):   # 紅 -> 藍 -> 黃
            continue
        # 同一排、而且三條擠在一起的那一組才是狀態列
        span = combo[2][0] + combo[2][2] - combo[0][0]
        score = spread * 10 + span / 100.0
        if best is None or score < best[0]:
            best = (score, combo)
    if best is None:
        return None
    return {name: (x, top + y, w, h)
            for (name, _), (x, y, w, h) in zip(ORDER, best[1])}
