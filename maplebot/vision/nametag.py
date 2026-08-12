"""用你自己截的「角色特徵」找出角色在畫面上的位置。

跟 vision/player_bar.py 解同一個問題（「角色永遠在畫面正中央」是錯的），
但不需要進遊戲做任何設定：截什麼都行——腳下的名牌、帽子、整個角色 sprite，
只要那塊圖是**你的角色才有**的。名牌尤其好用，因為上面是你的角色名，
別人的名字比不中，天生只會找到自己。

**為什麼要它**：鏡頭有跟隨延遲、走到地圖邊緣還會卡住，實測角色可以偏離
畫面中心 200px 以上。後果是挖掉「自己」挖錯地方（角色被當成一隻怪打整晚），
以及攻擊範圍框沒對準角色。

**解析度會變**：楓谷的 UI 跟著視窗大小縮放，所以 1920 截的模板放到 1366 的
畫面上會大 1.4 倍，完全比不中（實測最佳分數只有 0.506，而且還不在名牌上）。
模板旁邊會存一個 .json 記下截圖當下的 playfield 寬度，載入時照比例縮好；
沒有那個檔（舊模板）就自己掃一輪各種縮放找出對的比例，找到就記起來。

**為什麼用 CCOEFF 不用遮罩**：名牌底是半透明的，一開始以為要只比文字筆畫，
還量到「遮罩版分數 0.98、無遮罩只有 0.86」。那個比較是錯的——只看了**最佳
匹配點的分數**，沒看它跟背景的差距。實際量下來遮罩版的 SQDIFF 整張圖到處
都是 0.91（差距只有 +0.02，等於認不出來），無遮罩的 CCOEFF 分數雖然低，
但跟背景差 +0.37~+0.50，那才是真的分得出來。
"""
import json
import os
from typing import Optional, Tuple

import cv2
import numpy as np

# 使用者自己截的角色特徵。舊檔名仍然可用。
FEATURE_NAMES = ("player_feature.png", "player_nametag.png")

# 名牌中心 -> 角色中心的位移，以 790px 寬的 playfield 為基準（往上是負的）。
# 名牌畫在角色腳下，所以要往上找回身體中段。
DEFAULT_OFFSET = (0, -24)

MATCH_THRESHOLD = 0.70

# 整個 playfield 都要找。曾經為了省時間只找中央 70%，結果**剛好把這個功能
# 存在的理由挖掉了**：走到地圖邊緣時鏡頭會停住不再跟隨，角色就是會跑到畫面
# 邊邊去——實測角色在 1366 寬的畫面上跑到 x=205（離左緣 15%），正好卡在
# 邊界上找不到，於是角色又被當成一隻怪。小模板掃全畫面只要幾 ms，省不了什麼。
SEARCH_MARGIN = 0.0

# 不知道模板是多大截的時候，自己掃這些縮放比例找出對的那個
_SCALE_SWEEP = tuple(round(0.45 + 0.05 * i, 2) for i in range(23))    # 0.45 ~ 1.55
_RESCAN_AFTER = 15        # 連續這麼多幀都找不到就重掃（解析度中途改了）

# 找到過一次之後，下一幀只搜上次位置附近這個半徑（以 790px 寬為基準）。
# 角色一個 tick 走不到 30px（2560 畫面、8fps），60*scale 的窗綽綽有餘；
# 搜尋面積從整個中央區掉到 ~1/8，matchTemplate 的成本跟著掉一個量級
# ——實測 2560x1440 下 perceive 平均 81ms，這是最大的單一成本。
# 局部沒中（名牌被怪擋住、鏡頭大跳）就退回全域搜尋，行為不變、只是變快。
LOCAL_RADIUS = 60


class NametagLocator:
    def __init__(self, template_bgr: np.ndarray,
                 offset: Tuple[int, int] = DEFAULT_OFFSET,
                 threshold: float = MATCH_THRESHOLD,
                 template_width: Optional[int] = None):
        gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY) \
            if template_bgr.ndim == 3 else template_bgr
        self.template = gray
        self.offset = offset
        self.threshold = threshold
        self.template_width = template_width    # 截圖當下的 playfield 寬度
        self.last_score = 0.0
        self.scale_used: Optional[float] = None
        self._tpl: Optional[np.ndarray] = None
        self._misses = 0
        # 上次命中的模板左上角（playfield 座標）——下一幀先搜這附近
        self._last_hit: Optional[Tuple[int, int]] = None

    # ---- 模板縮放 ----

    def _resized(self, factor: float) -> Optional[np.ndarray]:
        if abs(factor - 1.0) < 0.02:
            return self.template
        interp = cv2.INTER_AREA if factor < 1 else cv2.INTER_CUBIC
        out = cv2.resize(self.template, None, fx=factor, fy=factor,
                         interpolation=interp)
        return out if out.size else None

    def _pick_scale(self, roi: np.ndarray, playfield_width: int) -> None:
        """決定模板要縮多少。知道截圖尺寸就直接算，不知道就掃一輪。

        比例要拿**整個 playfield 的寬度**去算，不能用搜尋範圍的寬度。
        """
        if self.template_width:
            factor = playfield_width / float(self.template_width)
            self._tpl = self._resized(factor)
            self.scale_used = factor
            return
        best = None
        for factor in _SCALE_SWEEP:
            tpl = self._resized(factor)
            if tpl is None or tpl.shape[0] > roi.shape[0] or tpl.shape[1] > roi.shape[1]:
                continue
            score = float(cv2.matchTemplate(roi, tpl, cv2.TM_CCOEFF_NORMED).max())
            if best is None or score > best[0]:
                best = (score, factor, tpl)
        if best is not None:
            self.scale_used, self._tpl = best[1], best[2]

    # ---- 定位 ----

    def _match_in(self, playfield_bgr: np.ndarray, x0: int, y0: int,
                  x1: int, y1: int) -> Optional[Tuple[int, int]]:
        """在指定窗內匹配；回傳模板左上角的 playfield 座標。

        用 CCOEFF 而不是遮罩 SQDIFF 的理由見模組開頭：遮罩版整張圖到處都是
        0.91，分不出來。這裡只負責「在這個窗裡找」，窗要多大由 locate 決定。
        """
        tpl = self._tpl
        if tpl is None:
            return None
        roi = cv2.cvtColor(playfield_bgr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        if roi.shape[0] < tpl.shape[0] or roi.shape[1] < tpl.shape[1]:
            return None
        res = cv2.matchTemplate(roi, tpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        self.last_score = float(score)
        if score < self.threshold:
            return None
        return (x0 + loc[0], y0 + loc[1])

    def locate(self, playfield_bgr: np.ndarray,
               scale: float = 1.0) -> Optional[Tuple[int, int]]:
        """回傳角色中心的 playfield 座標；沒把握就回 None（呼叫端退回畫面中央）。"""
        h, w = playfield_bgr.shape[:2]
        if h == 0 or w == 0:
            return None
        x0, y0 = int(w * SEARCH_MARGIN), int(h * SEARCH_MARGIN)
        roi = cv2.cvtColor(playfield_bgr[y0:max(h - y0, y0 + 1),
                                         x0:max(w - x0, x0 + 1)],
                           cv2.COLOR_BGR2GRAY)

        if self._tpl is None:
            self._pick_scale(roi, w)
        tpl = self._tpl
        if tpl is None or roi.shape[0] < tpl.shape[0] or roi.shape[1] < tpl.shape[1]:
            return None

        th, tw = tpl.shape[:2]
        hit = None
        if self._last_hit is not None:
            r = max(int(LOCAL_RADIUS * scale), 2 * max(tw, th))
            lx, ly = self._last_hit
            hit = self._match_in(playfield_bgr,
                                 max(lx - r, 0), max(ly - r, 0),
                                 min(lx + tw + r, w), min(ly + th + r, h))
        if hit is None:
            # 局部沒中（第一幀、名牌被擋、鏡頭大跳）-> 退回整個搜尋範圍
            hit = self._match_in(playfield_bgr, x0, y0,
                                 max(w - x0, x0 + tw), max(h - y0, y0 + th))
        self._last_hit = hit
        if hit is None:
            # 局部與全域都沒中才算一次 miss：局部沒中是常態（名牌被擋一幀），
            # 拿它去累積重掃計數會把還堪用的縮放比例丟掉
            self._misses += 1
            if self._misses >= _RESCAN_AFTER and not self.template_width:
                self._tpl = None        # 解析度可能中途改了，下一幀重掃
                self._misses = 0
            return None
        self._misses = 0
        return (hit[0] + tw // 2 + int(round(self.offset[0] * scale)),
                hit[1] + th // 2 + int(round(self.offset[1] * scale)))


def _template_width(path: str) -> Optional[int]:
    """讀模板旁邊那個 .json 記的「截圖當下的 playfield 寬度」。"""
    side = os.path.splitext(path)[0] + ".json"
    try:
        with open(side, encoding="utf-8") as f:
            return int(json.load(f)["playfield_width"])
    except Exception:
        return None


def save_width(path: str, playfield_width: int) -> None:
    """截模板時把當下的 playfield 寬度記在旁邊，換解析度才縮得回來。"""
    side = os.path.splitext(path)[0] + ".json"
    with open(side, "w", encoding="utf-8") as f:
        json.dump({"playfield_width": int(playfield_width)}, f)


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
            return NametagLocator(img, offset, threshold, _template_width(path))
    return None
