"""用角色名牌找出角色在畫面上的精確位置。

跟 vision/player_bar.py 解同一個問題（「角色永遠在畫面正中央」是錯的），
但不需要進遊戲做任何設定：名牌本來就一直掛在角色腳下。

**為什麼要另外做一套**：組隊紅條要先自己跟自己組隊才會出現，而不是每個
客戶端都找得到組隊的按鍵。名牌是角色一出生就有的，抓它零準備。

**為什麼可以只認自己**：名牌上是**你的角色名**，別人的名字不一樣，
模板比不中——所以這招天生只會找到自己，不會抓到路人。

**半透明底怎麼辦**：名牌底是半透明黑塊，背後的地形會透出來，直接比整塊
會隨背景飄（實測 CCOEFF 從 1.00 掉到 0.86）。所以只比**文字筆畫**：
建模板時把亮的像素挑出來當遮罩，用遮罩版的匹配，實測同一張模板在草地、
乾草堆、天空背景下都還有 0.98 以上。

**為什麼用 SQDIFF 而不是 CCORR**：OpenCV 只有 SQDIFF / SQDIFF_NORMED /
CCORR_NORMED 吃遮罩。CCORR_NORMED 比的是原始亮度的相關性，一整片同色的
區域跟任何模板都會算出接近 1.0 的完美分數——單元測試裡一張純色畫面就被
它「找到」了角色。SQDIFF 比的是差值平方，純色區域差很多，不會假陽性。
"""
import os
from typing import Optional, Tuple

import cv2
import numpy as np

# 使用者自己截的「角色特徵」。截什麼都行——名牌、帽子、整個角色 sprite；
# 只要那塊圖是**你的角色才有**的就成立。舊檔名仍然可用。
FEATURE_NAMES = ("player_feature.png", "player_nametag.png")

# 名牌中心 -> 角色中心的位移，以 790px 寬的 playfield 為基準（往上是負的）。
# 名牌畫在角色腳下，所以要往上找回身體中段。
DEFAULT_OFFSET = (0, -24)

MATCH_THRESHOLD = 0.85

# 只在畫面中央這個比例的範圍內找。楓谷的鏡頭不會把角色甩到邊邊，
# 限制搜尋範圍同時省時間、也擋掉離譜的誤匹配。
SEARCH_MARGIN = 0.15


class NametagLocator:
    """載入一次模板，之後每幀用遮罩模板匹配找角色。"""

    def __init__(self, template_bgr: np.ndarray,
                 offset: Tuple[int, int] = DEFAULT_OFFSET,
                 threshold: float = MATCH_THRESHOLD):
        gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY) \
            if template_bgr.ndim == 3 else template_bgr
        self.template = gray
        self.mask = _text_mask(gray)
        self.offset = offset
        self.threshold = threshold
        self.last_score = 0.0

    def locate(self, playfield_bgr: np.ndarray,
               scale: float = 1.0) -> Optional[Tuple[int, int]]:
        """回傳角色中心的 playfield 座標；沒把握就回 None（呼叫端退回畫面中央）。"""
        th, tw = self.template.shape[:2]
        h, w = playfield_bgr.shape[:2]
        if h < th or w < tw:
            return None

        x0 = int(w * SEARCH_MARGIN)
        y0 = int(h * SEARCH_MARGIN)
        x1 = max(w - x0, x0 + tw)
        y1 = max(h - y0, y0 + th)
        roi = cv2.cvtColor(playfield_bgr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        if roi.shape[0] < th or roi.shape[1] < tw:
            return None

        res = cv2.matchTemplate(roi, self.template, cv2.TM_SQDIFF_NORMED,
                                mask=self.mask)
        # 遮罩匹配在退化的區塊會算出 inf/nan，不清掉會被 minMaxLoc 選中
        res = np.nan_to_num(res, nan=1.0, posinf=1.0, neginf=1.0)
        diff, _, loc, _ = cv2.minMaxLoc(res)      # SQDIFF：越小越像
        score = 1.0 - float(diff)
        self.last_score = score
        if score < self.threshold:
            return None
        cx = x0 + loc[0] + tw // 2 + int(round(self.offset[0] * scale))
        cy = y0 + loc[1] + th // 2 + int(round(self.offset[1] * scale))
        return (cx, cy)


def _text_mask(gray: np.ndarray) -> np.ndarray:
    """決定模板裡哪些像素可信。

    截名牌跟截角色本體要用不同策略，而使用者不該還要回來設定這個：

    * **名牌**底色是半透明黑塊，背後的地形會透出來 —— 整塊比會隨背景飄
      （實測 1.00 -> 0.86）。只比文字筆畫才穩得住。
    * **角色 sprite**（或帽子、武器）是不透明的，整塊都是資訊。這時挑「亮的
      部分」反而把角色深色的那半丟掉，比得更差。

    兩種都取「亮的部分」就好，不用分辨是哪一種：名牌只剩文字筆畫（正是要的），
    不透明的圖也還剩幾百個像素可比，一樣夠準。
    （試過自動分辨「暗部是不是平的」——結果剛好相反：名牌的暗部因為背景透出來
    反而變化很大，那個判斷會把最需要遮罩的情況判成不用遮罩。）
    """
    level = max(int(gray.mean()) + 25, 110)
    mask = (gray >= level).astype(np.uint8) * 255
    # 筆畫邊緣有抗鋸齒，膨脹一格把半亮的邊也算進來，樣本數才夠穩
    mask = cv2.dilate(mask, np.ones((2, 2), np.uint8))
    if int((mask > 0).sum()) < 30:      # 幾乎沒挑到東西 -> 模板多半框錯了
        return np.full(gray.shape, 255, dtype=np.uint8)
    return mask


def load_locator(ui_dir: str, offset: Tuple[int, int] = DEFAULT_OFFSET,
                 threshold: float = MATCH_THRESHOLD) -> Optional[NametagLocator]:
    """載入使用者截的角色特徵模板；沒有就回 None（呼叫端退回組隊紅條）。

    先問檔案在不在再讀：沒截模板是**正常情況**（預設就沒有），但 cv2.imread
    讀不到檔會往 stderr 印 "can't open/read file: check file path/integrity"，
    看起來像出事了。
    """
    for name in FEATURE_NAMES:
        path = os.path.join(ui_dir, name)
        if not os.path.exists(path):
            continue
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is not None:
            return NametagLocator(img, offset, threshold)
    return None
