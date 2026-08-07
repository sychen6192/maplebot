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
from .vision.locate import PLAYER_NAME, load_ui_template
from .vision.mobs import MobDetector


class Perceiver:
    def __init__(self, cfg: AppCfg, detector: MobDetector):
        self.cfg = cfg
        self.detector = detector
        # 有玩家點模板就優先用模板匹配（見 vision/minimap.py）
        self.player_template = load_ui_template(cfg.vision.ui_templates_dir, PLAYER_NAME)
        # 怪物偵測若很貴（遠端推理、大畫面），可以降頻並沿用上次結果；
        # HP/位置這些便宜又攸關安全的辨識仍然每個 tick 都做。
        self._mobs_cache: list = []
        self._mobs_ts: Optional[float] = None

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
            st.minimap_size = (mm.shape[1], mm.shape[0])
            st.player = minimap.find_player(mm, vc, template=self.player_template)
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
            interval = vc.mob_interval
            due = (interval <= 0 or self._mobs_ts is None
                   or now - self._mobs_ts >= interval)
            if due:
                self._mobs_cache = self.detector.detect(pf)
                self._mobs_ts = now
            st.mobs = self._mobs_cache
        return st
