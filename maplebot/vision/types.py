"""偵測器的型別：怪物框（Mob）與偵測器介面（MobDetector）。

這個檔刻意不 import cv2 和 numpy。brain/state.py 要拿 Mob 當 GameState 的欄位，
決策層（ADR 0002 的純函式）不該為了一個 dataclass 就把 OpenCV 載進來——
以前 Mob 定義在 mobs.py，而 mobs.py 頂層就 import cv2，結果
`import maplebot.brain.fsm` 會連 cv2、numpy 一起載入。tests/test_layering.py 守著這條線。

四種偵測器的實作在 outline_mobs / template_mobs / yolo_mobs / remote_mobs，
由 mobs.py:make_detector 依設定挑一個。
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Protocol

if TYPE_CHECKING:   # 只給型別標註用；執行期不載 numpy
    import numpy as np


@dataclass
class Mob:
    cx: int
    cy: int
    w: int
    h: int
    score: float
    name: str


class MobDetector(Protocol):
    def detect(self, playfield_bgr: "np.ndarray") -> List[Mob]: ...
