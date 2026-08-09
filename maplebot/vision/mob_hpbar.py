"""靠怪物頭上的綠色血條找怪。

借自 MapleStoryAutoLevelUp 的 `with_enemy_hp_bar`（356★，同為楓之谷 Artale）。

描邊偵測是「猜」——黑塊多大算一隻怪都是門檻調出來的，怪太大、太小、跟地形
連在一起就會漏。怪物血條剛好相反：那是**遊戲自己畫的 UI**，顏色是固定的
純綠 (BGR 71,204,64)，畫面上不會有第二個東西長這樣。只要抓到那個顏色，
那裡就一定有一隻怪，不用調任何門檻。

限制很明確：血條只在怪**被打過之後**才出現。所以這不能取代描邊偵測（第一下
還是得靠它找到目標），但可以補上「打到一半跟丟」——描邊漏掉的那幾幀，血條
還在，bot 就不會轉頭走掉。兩邊的框用 NMS 合併。
"""
from typing import List, Tuple

import cv2
import numpy as np

from .mobs import Mob

# 怪物血條的綠色（BGR）。跟 MapleStoryAutoLevelUp 用的同一組值
HP_BAR_BGR: Tuple[int, int, int] = (71, 204, 64)

# 血條相對於怪的位置：血條在頭上，怪的身體在它下方
BODY_OFFSET_Y = 10        # 從血條往下多少 px 開始算身體（以 790px 寬為基準）
BODY_W = 70               # 猜測的身體寬高
BODY_H = 60


def find_hp_bars(playfield_bgr: np.ndarray, tolerance: int = 25,
                 min_pixels: int = 6, scale: float = 1.0) -> List[Mob]:
    """回傳每個怪物血條底下推測出來的怪。

    tolerance 是各通道容許誤差：擷取方式不同（PrintWindow / 螢幕擷取 / 縮放）
    顏色會有一兩階誤差，完全精確比對太脆。
    """
    if playfield_bgr.size == 0:
        return []
    lo = np.array([max(c - tolerance, 0) for c in HP_BAR_BGR], dtype=np.uint8)
    hi = np.array([min(c + tolerance, 255) for c in HP_BAR_BGR], dtype=np.uint8)
    mask = cv2.inRange(playfield_bgr, lo, hi)

    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    h, w = playfield_bgr.shape[:2]
    body_w = max(int(BODY_W * scale), 8)
    body_h = max(int(BODY_H * scale), 8)
    offset = max(int(BODY_OFFSET_Y * scale), 1)

    mobs: List[Mob] = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < min_pixels:
            continue
        if bh > bw:               # 血條是橫的；直的色塊是別的東西
            continue
        # 血條中心的正下方就是怪
        cx = int(x + bw // 2)
        cy = int(min(y + offset + body_h // 2, h - 1))
        mobs.append(Mob(cx=cx, cy=max(cy, 0), w=body_w, h=body_h,
                        score=1.0, name="hpbar"))
    return mobs


def merge(primary: List[Mob], extra: List[Mob], iou_thr: float = 0.3) -> List[Mob]:
    """把兩組偵測結果合併，重疊的只留一個。

    描邊偵測的框比較準（是真的量出來的），所以重疊時優先留它——extra 只是
    用來補漏的。
    """
    if not extra:
        return list(primary)
    kept = list(primary)
    for cand in extra:
        if not any(_iou(cand, m) > iou_thr for m in kept):
            kept.append(cand)
    return kept


def _iou(a: Mob, b: Mob) -> float:
    ax1, ay1 = a.cx - a.w // 2, a.cy - a.h // 2
    bx1, by1 = b.cx - b.w // 2, b.cy - b.h // 2
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2 = min(ax1 + a.w, bx1 + b.w)
    iy2 = min(ay1 + a.h, by1 + b.h)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0
