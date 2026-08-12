"""playfield 影像的共用前處理：疊在上面的 UI，以及角色自己那一塊。

即時執行（perception.py）與離線自動標註（teachers.py）必須用**同一套**規則。
老師標出來的框跟 bot 實際看到的不一樣，學生就是在學另一個問題——所以這兩段
邏輯放在這裡共用，而不是各抄一份。
"""
from typing import List, Optional, Sequence, Tuple

import numpy as np

from ..vision.mobs import Mob

Rect = Tuple[int, int, int, int]

OVERLAY_GRAY = 128


def overlay_rects(regions) -> List[Rect]:
    """playfield 上疊著的 UI 區塊（換算成 playfield 座標）。

    小地圖通常疊在主畫面左上角，上面的其他玩家紅點會被誤認成組隊紅條。
    """
    pf = regions.get("playfield")
    mm = regions.get("minimap")
    if pf is None or mm is None:
        return []
    return [(mm[0] - pf[0], mm[1] - pf[1], mm[2], mm[3])]


def blank_rects(img: np.ndarray, rects: Sequence[Rect],
                origin: Tuple[int, int] = (0, 0)) -> np.ndarray:
    """把疊在畫面上的 UI 塗成中灰再拿去找怪。

    rects 是 client 區座標；origin 是 img 左上角在 client 區的座標。
    沒有任何矩形落在畫面內時回傳原影像（不複製）。

    小地圖面板的標題文字、聊天視窗的字都有黑色描邊，描邊偵測分不出那是
    文字還是怪——實測小地圖標題那一條會固定變成一隻「怪」。塗中灰而不是
    塗黑：黑的話反而變成一整塊符合條件的黑塊。
    """
    if not rects:
        return img
    ox, oy = origin
    h, w = img.shape[:2]
    out: Optional[np.ndarray] = None
    for x, y, rw, rh in rects:
        x0, y0 = x - ox, y - oy
        x1, y1 = max(x0, 0), max(y0, 0)
        x2, y2 = min(x0 + rw, w), min(y0 + rh, h)
        if x2 <= x1 or y2 <= y1:
            continue
        if out is None:
            out = img.copy()
        out[y1:y2, x1:x2] = OVERLAY_GRAY
    return img if out is None else out


def drop_at(mobs: Sequence[Mob], xy: Optional[Tuple[int, int]],
            box: Tuple[int, int], scale: float = 1.0) -> List[Mob]:
    """把落在 xy 附近（box 大小的框內）的偵測結果丟掉。

    描邊偵測是照**畫面中央**挖掉自己的，但鏡頭有跟隨延遲、走到地圖邊緣還會
    卡住，角色其實常常不在正中央——那時角色自己就會被當成一隻怪打（離線標
    註時則是每一張訓練圖都多一個「角色是怪」的標註，學生學得最牢的就那個）。

    xy 是 None（量不到角色位置）時原樣回傳，由呼叫端決定要不要信。
    """
    if xy is None:
        return list(mobs)
    bw, bh = int(box[0] * scale), int(box[1] * scale)
    px, py = xy
    return [m for m in mobs
            if abs(m.cx - px) > bw // 2 or abs(m.cy - py) > bh // 2]
