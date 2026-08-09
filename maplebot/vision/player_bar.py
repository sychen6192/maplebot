"""用組隊紅條找出角色在畫面上的精確位置。

借自 MapleStoryAutoLevelUp 的 `party_red_bar`（他們試過 nametag 模板匹配後
改用這個，並把 nametag 標記為 deprecated）。淘寶賣的商業腳本也要求「隊伍放 P」，
同一招。

**為什麼需要**：程式原本假設「角色永遠在畫面正中央」。這個假設在楓谷是錯的：

  * 鏡頭有跟隨延遲與死區，走動時角色會跑在鏡頭前面
  * 走到地圖邊緣鏡頭會卡住，角色繼續往邊緣移動
  * 站在地圖上下邊界時垂直方向同理

實測 1920 視窗上角色可以偏離中心 200px 以上。後果有兩個，而且都很難自己看出來：
挖掉「自己」的框挖錯地方（角色被當成怪打），以及攻擊範圍框沒對準角色
（一邊打得到卻不打、另一邊構不到卻猛揮）。

**做法**：自己跟自己組隊後，角色頭上會出現一條組隊血條。那是遊戲畫的 UI，
純紅色、固定高度的細長條，用 HSV 遮罩加上幾何條件就能穩定抓到。抓不到就
退回畫面中央（也就是原本的行為），所以沒組隊也不會壞掉。
"""
from typing import Optional, Tuple

import cv2
import numpy as np

# 組隊血條的紅（OpenCV HSV）。H=0 的純紅，飽和度與亮度都要夠高。
# 對應 MapleStoryAutoLevelUp 的 lower_red [0,60,60] / upper_red [0,100,100]
#（他們用 0~360 / 0~100 的標準 HSV，這裡換算成 OpenCV 的 0~179 / 0~255）
LOWER_RED = (0, 153, 153)
UPPER_RED = (0, 255, 255)

# 血條的幾何條件與角色偏移，都以 790px 寬的 playfield 為基準。
# 參考專案的值是對 1296px 寬的視窗調的，這裡換算過（x0.61）
BAR_H = (3, 5)            # 高度：很扁，這是最強的過濾條件
BAR_W = (2, 32)           # 寬度（血條會隨 HP 縮短，所以下限很鬆）
MIN_AREA = 6
MIN_FILL = 0.7            # 面積 / 外框面積：實心細條
PLAYER_OFFSET = (12, 40)  # 從血條左上角到角色中心的位移

# 找到的位置離畫面中心超過這個比例就不採信——楓谷的鏡頭不會把角色甩那麼遠，
# 這種結果幾乎確定是把場景裡的紅色物件當成血條了
MAX_OFFSET_RATIO = 0.35


def find_player_bar(playfield_bgr: np.ndarray, scale: float = 1.0,
                    mask_out=None) -> Optional[Tuple[int, int]]:
    """回傳角色在 playfield 座標的中心位置；找不到回 None。

    mask_out 是要先塗黑的矩形清單 [(x, y, w, h)]，用來蓋掉小地圖——
    小地圖上的其他玩家紅點也是紅的。
    """
    if playfield_bgr.size == 0:
        return None
    img = playfield_bgr
    if mask_out:
        img = img.copy()
        for x, y, w, h in mask_out:
            img[max(y, 0):y + h, max(x, 0):x + w] = 0

    mask = cv2.inRange(cv2.cvtColor(img, cv2.COLOR_BGR2HSV),
                       np.array(LOWER_RED, dtype=np.uint8),
                       np.array(UPPER_RED, dtype=np.uint8))
    # 用連通元件而不是輪廓：cv2.contourArea 量的是多邊形面積，
    # 細條會被低估（12x3 的實心條只算出 22 而不是 36），實心率判斷會失準
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    hmin, hmax = max(int(BAR_H[0] * scale), 2), max(int(round(BAR_H[1] * scale)), 3)
    wmin, wmax = max(int(BAR_W[0] * scale), 2), max(int(BAR_W[1] * scale), 4)
    min_area = max(int(MIN_AREA * scale * scale), 4)

    best = None
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if not (hmin <= h <= hmax and wmin <= w <= wmax):
            continue
        if area < min_area or area < MIN_FILL * w * h:
            continue
        if best is None or w * h > best[2] * best[3]:
            best = (x, y, w, h)
    if best is None:
        return None

    fh, fw = playfield_bgr.shape[:2]
    px = int(best[0] + PLAYER_OFFSET[0] * scale)
    py = int(best[1] + PLAYER_OFFSET[1] * scale)
    if abs(px - fw // 2) > fw * MAX_OFFSET_RATIO or \
            abs(py - fh // 2) > fh * MAX_OFFSET_RATIO:
        return None            # 離中心太遠，多半是場景裡的紅色物件
    return (px, py)
