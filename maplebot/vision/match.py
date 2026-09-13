"""模板比對：對**遊戲自己畫的 UI** 要比彩色，而且限定在 ROI 內。

出發點是商業版拆出來的 `template_detector.dll`：它匯出
`td_detect_in_roi` / `td_set_use_color` / `td_set_prefer_gpu`，配的 shader
（`template_match_bgr_cs.hlsl`）算的是 BGR 三通道平方差和（SSD）。三件事——
彩色、限定 ROI、SSD——這個專案原本三件都選了另一邊。

**量過之後只採用其中兩件。** 拿 `tests/fixtures/mapleaga_800x600.jpg` 的小地圖
玩家點當模板，量「命中分數與背景最高分的差距」（差距才是能不能分辨的指標，
絕對分數不是——這個結論 nametag.py 已經踩過一次）：

    擾動(雜訊,增益)   灰階CCOEFF(現況)   彩色CCOEFF   彩色SQDIFF
    (0, 1.00)            +0.0553         +0.3592      +0.2238
    (8, 1.00)            +0.0587         +0.3695      +0.2153
    (8, 1.08)            +0.0633         +0.3649      +0.2129
    (12, 0.90)           +0.0634         +0.3392      +0.2211

  * **彩色是大勝**：+0.055 -> +0.36，差距拉開約六倍。灰階會把 (0,255,255)
    的黃跟 (255,255,0) 的青壓成同一個值，而小地圖上玩家是黃點、其他玩家是
    紅點、傳送門是藍點——灰階等於把最強的判別特徵丟掉
  * **SSD 沒有比較好**：彩色 SQDIFF 一路輸給彩色 CCOEFF，連在亮度增益
    0.9/1.08 的情況下也是（那本來該是 SSD 最吃虧、CCOEFF 最占便宜的地方，
    結果方向一致）。所以不跟進這一半

商業版選 SSD 多半不是因為它比較準，而是因為它好寫成 compute shader：
那支 HLSL 就是一個三層迴圈累加平方差，不用算均值與變異數。那是 GPU 實作的
考量，不是辨識精度的考量，沒有理由照抄到 CPU 上。

**這個模組不適用於什麼**：sprite 疊在會變的背景上——那正是 `nametag.py`
（半透明底 + 背後地形會變）。那裡量過遮罩版 SQDIFF 整張圖到處都是 0.91
（跟背景差 +0.02，等於認不出來）。那個結論仍然成立，nametag 不改。
"""
from typing import Optional, Tuple

import cv2
import numpy as np

Rect = Tuple[int, int, int, int]        # x, y, w, h

# 比對方法。預設 ccoeff 是量出來的（見模組開頭），sqdiff 留著是因為在
# 「模板與畫面像素完全一致」的合成/無損情境下它更嚴格，換一種看看很便宜。
METHODS = {
    "ccoeff": (cv2.TM_CCOEFF_NORMED, False),   # (cv2 常數, 分數是否越小越像)
    "sqdiff": (cv2.TM_SQDIFF_NORMED, True),
}


def _as_gray(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


def _is_flat(tpl: np.ndarray) -> bool:
    """模板每個通道都是同一個值嗎？

    要**逐通道**看。整塊 (0,255,255) 的純黃在「把三個通道混在一起」的標準差
    下很大（0 跟 255 差很多），但 CCOEFF 是各通道先各自減掉自己的均值再相關，
    所以真正會除到零的是「每個通道各自都沒有變化」。混在一起算會漏判。
    """
    flat = tpl.reshape(-1, tpl.shape[2]) if tpl.ndim == 3 else tpl.reshape(-1, 1)
    return bool(flat.std(axis=0).max() < 1e-6)


def _prepare(img: np.ndarray, tpl: np.ndarray, use_color: bool):
    """讓影像與模板通道數一致；任一邊本來就是灰階就兩邊都走灰階。"""
    if not use_color or img.ndim == 2 or tpl.ndim == 2:
        return _as_gray(img), _as_gray(tpl)
    return img, tpl


def match(img_bgr: np.ndarray, tpl: np.ndarray, roi: Optional[Rect] = None,
          use_color: bool = True, method: str = "ccoeff"
          ) -> Optional[Tuple[float, Tuple[int, int]]]:
    """在 img 裡找 tpl，回傳 `(score, (x, y))`；比不了（尺寸不合）回 None。

    score 一律是「越大越像」的 0.0~1.0——sqdiff 的方向已經翻過來了。
    比對分數在這個 repo 裡全部同一個方向，兩種混著遲早有人把門檻比反。

    (x, y) 是模板左上角在**原圖**的座標（給了 roi 也已經換算回去）。
    """
    if method not in METHODS:
        raise ValueError(f"未知的比對方法 {method!r}，可用: {list(METHODS)}")
    ox, oy = 0, 0
    if roi is not None:
        x, y, w, h = roi
        ih, iw = img_bgr.shape[:2]
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + w, iw), min(y + h, ih)
        if x1 <= x0 or y1 <= y0:
            return None
        img_bgr, ox, oy = img_bgr[y0:y1, x0:x1], x0, y0

    a, b = _prepare(img_bgr, tpl, use_color)
    if a.size == 0 or b.size == 0 or \
            a.shape[0] < b.shape[0] or a.shape[1] < b.shape[1]:
        return None

    cv_method, smaller_is_better = METHODS[method]
    if cv_method == cv2.TM_CCOEFF_NORMED and _is_flat(b):
        # 純色模板的 CCOEFF 沒有定義：它要除以模板的標準差，而那是 0。
        # OpenCV 不會報錯，會回一整片看起來很高的分數，然後命中點落在 (0,0)
        # ——看起來像「比中了」，其實是除以零。純色沒有結構可以相關，改用
        # 絕對差（sqdiff）才有意義，那正好是它擅長的情況。
        cv_method, smaller_is_better = METHODS["sqdiff"]
    res = cv2.matchTemplate(a, b, cv_method)
    lo, hi, lo_loc, hi_loc = cv2.minMaxLoc(res)
    score, loc = (1.0 - lo, lo_loc) if smaller_is_better else (hi, hi_loc)
    return (float(score), (loc[0] + ox, loc[1] + oy))


def match_center(img_bgr: np.ndarray, tpl: np.ndarray, threshold: float,
                 roi: Optional[Rect] = None, use_color: bool = True,
                 method: str = "ccoeff") -> Optional[Tuple[int, int]]:
    """比中了就回模板**中心**的座標，沒中回 None。大部分呼叫端要的是這個。"""
    hit = match(img_bgr, tpl, roi=roi, use_color=use_color, method=method)
    if hit is None or hit[0] < threshold:
        return None
    _, (x, y) = hit
    return (x + tpl.shape[1] // 2, y + tpl.shape[0] // 2)
