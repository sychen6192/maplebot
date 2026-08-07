"""感知層：把一張完整的遊戲畫面變成 GameState。

每 tick 只擷取一次完整畫面，各區域用 numpy 切片取得，
所以整個感知過程可以用靜態截圖離線測試。
區域超出畫面（視窗被縮小/校正錯誤）時對應欄位維持 None，
由決策層與 watchdog 處理。
"""
from typing import Optional

import numpy as np

from .brain.state import GameState
from .config import AppCfg
from .vision import minimap, status
from .vision.mobs import MobDetector


class Perceiver:
    def __init__(self, cfg: AppCfg, detector: MobDetector):
        self.cfg = cfg
        self.detector = detector

    def _slice(self, frame: np.ndarray, name: str) -> Optional[np.ndarray]:
        region = self.cfg.regions.get(name)
        if region is None:
            return None
        x, y, w, h = region
        fh, fw = frame.shape[:2]
        if x < 0 or y < 0 or x + w > fw or y + h > fh:
            return None
        return frame[y:y + h, x:x + w]

    def perceive(self, frame: np.ndarray, now: float) -> GameState:
        st = GameState(ts=now)
        vc = self.cfg.vision

        mm = self._slice(frame, "minimap")
        if mm is not None:
            st.player = minimap.find_player(mm, vc)
            st.others = minimap.find_others(mm, vc)

        hp = self._slice(frame, "hp_bar")
        if hp is not None:
            st.hp = status.bar_ratio(hp, vc.bar_colors.get("hp", "red"))
        mp = self._slice(frame, "mp_bar")
        if mp is not None:
            st.mp = status.bar_ratio(mp, vc.bar_colors.get("mp", "blue"))
        exp = self._slice(frame, "exp_bar")
        if exp is not None:
            st.exp = status.bar_ratio(exp, vc.bar_colors.get("exp", "yellow"))

        pf = self._slice(frame, "playfield")
        if pf is not None:
            st.mobs = self.detector.detect(pf)
        return st
