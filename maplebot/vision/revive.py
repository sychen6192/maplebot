"""死亡復活對話框偵測（零設定）。

角色死掉時，楓谷會在畫面中央彈出「要在目前地圖復活嗎？」的對話框，
底部一顆橘色「確定」鈕。不點掉，bot 就永遠停在死亡畫面——掛整晚也白掛。

**為什麼靠顏色+形狀而不是模板**：跟 outline 找怪同一套哲學，零設定、換
客戶端也不用重截。那顆確定鈕是遊戲 UI 畫的固定橘色（不是場景美術會出現
的顏色），又扁又寬、實心、位在畫面中央偏下——這組條件在正常遊戲畫面裡
不會湊齊。抓到它 = 死亡對話框在。

回傳按鈕中心的 **playfield 座標**，executor 換算成螢幕座標點下去。
找不到回 None（沒死，正常打怪）。
"""
from typing import Optional, Tuple

import cv2
import numpy as np

# 按鈕橘色（BGR 範圍）。實測按鈕填色 mean 約 (94,149,222)。
_ORANGE_LO = np.array([20, 90, 175], dtype=np.uint8)    # BGR 下限
_ORANGE_HI = np.array([110, 180, 248], dtype=np.uint8)  # BGR 上限

# 按鈕幾何（以 790px 寬 playfield 為基準，隨畫面寬度縮放）
_BTN_W = (18, 42)          # 寬
_BTN_H = (5, 13)           # 高（很扁）
_MIN_FILL = 0.33           # 白色「確定」文字會把橘色挖空，實心率不高
_MIN_ASPECT = 2.2          # 寬明顯大於高

# 只在畫面中央這個範圍找對話框。對話框永遠正中央、按鈕在中心略偏下，
# 收緊垂直範圍剛好把頂部的任務指引/公告 UI（也有橘色）擋在外面。
_CENTER_MARGIN_X = 0.22
_CENTER_MARGIN_Y = 0.30


def find_confirm_button(playfield_bgr: np.ndarray,
                        scale: float = 1.0) -> Optional[Tuple[int, int]]:
    """回傳「確定」鈕中心的 playfield 座標；沒有對話框回 None。

    死亡對話框的簽名是**一對並排的扁橘鈕**（確定＋取消）——比單一橘塊特異
    得多。場景裡的橘色美術（乾草堆、香菇）湊不出「兩顆同高、同大小、水平
    相鄰的扁實心矩形」，所以要求成對出現才算數，幾乎不可能誤報。
    找到就回**左邊**那顆（確定）的中心。
    """
    if playfield_bgr.size == 0:
        return None
    h, w = playfield_bgr.shape[:2]
    x0, x1 = int(w * _CENTER_MARGIN_X), int(w * (1 - _CENTER_MARGIN_X))
    y0, y1 = int(h * _CENTER_MARGIN_Y), int(h * (1 - _CENTER_MARGIN_Y))
    roi = playfield_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return None

    mask = cv2.inRange(roi, _ORANGE_LO, _ORANGE_HI)
    n, _, stats, cent = cv2.connectedComponentsWithStats(mask, connectivity=8)

    wmin, wmax = max(int(_BTN_W[0] * scale), 6), max(int(_BTN_W[1] * scale), 12)
    hmin, hmax = max(int(_BTN_H[0] * scale), 3), max(int(_BTN_H[1] * scale), 6)

    btns = []      # 通過單顆條件的扁橘塊 (cx, cy, w, h)
    for i in range(1, n):
        _, _, bw, bh, area = stats[i]
        if not (wmin <= bw <= wmax and hmin <= bh <= hmax):
            continue
        if area < _MIN_FILL * bw * bh or bw < _MIN_ASPECT * bh:
            continue
        btns.append((float(cent[i][0]), float(cent[i][1]), bw, bh))

    # 找一對：同高、大小相近、水平相鄰（間距約 1~2.6 個按鈕寬）。
    # 對話框永遠置中，所以多組候選時挑「兩顆中心最貼近畫面水平中心」那組
    # ——把頂部公告 UI 之類偏一邊的假對排除掉。
    roi_cx = roi.shape[1] / 2
    best = None
    best_off = None
    for a in range(len(btns)):
        for b in range(len(btns)):
            if a == b:
                continue
            ax, ay, aw, ah = btns[a]
            bx, by, bw2, bh2 = btns[b]
            if ax >= bx:                       # a 必須在左（確定）
                continue
            if abs(ay - by) > ah:              # 不同高 -> 不是同一列的兩顆鈕
                continue
            if abs(aw - bw2) > 0.5 * aw:       # 大小差太多
                continue
            gap = bx - ax
            if not (aw <= gap <= 2.6 * aw):    # 水平相鄰
                continue
            off = abs((ax + bx) / 2 - roi_cx)  # 這對的中心離畫面中心多遠
            if best_off is None or off < best_off:
                best_off = off
                best = (int(ax) + x0, int(ay) + y0)
    return best
