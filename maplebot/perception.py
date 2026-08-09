"""感知層：把一張完整的遊戲畫面變成 GameState。

每 tick 只擷取一次完整畫面，各區域用 numpy 切片取得，
所以整個感知過程可以用靜態截圖離線測試。
區域超出畫面（視窗被縮小/校正錯誤）時對應欄位維持 None，
由決策層與 watchdog 處理。
"""
from dataclasses import replace
from typing import Optional

import numpy as np

from .brain.state import GameState
from .config import AppCfg
from .vision import minimap, status
from .vision.locate import PLAYER_NAME, load_ui_template
from .vision.follower import FollowerFilter
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
        self._followers = FollowerFilter(
            min_shift_px=cfg.vision.follower_min_shift_px,
            hits_needed=cfg.vision.follower_hits,
            tol_px=cfg.vision.follower_tol_px,
            max_followers=cfg.vision.follower_max,
        ) if cfg.vision.filter_followers else None
        self._prev_player: Optional[tuple] = None
        self.last_followers: list = []      # 給 debug_view 畫出來用
        # 被撞到時血條會閃，那一幀會讀成 0%——擋在這裡，別讓下游灌藥又停機
        self._bars = {
            name: status.BarFilter(cfg.vision.bar_max_drop,
                                   cfg.vision.bar_confirm_frames)
            for name in ("hp", "mp", "exp")
        }

    @property
    def bar_glitches(self) -> int:
        """擋掉幾次血條誤讀（閃爍/特效蓋住）。"""
        return sum(f.suppressed for f in self._bars.values())

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

        for name, default_color in (("hp", "red"), ("mp", "blue"), ("exp", "yellow")):
            roi = self._slice(frame, f"{name}_bar")
            if roi is None:
                continue
            raw = status.bar_ratio(roi, vc.bar_colors.get(name, default_color))
            setattr(st, name, self._bars[name].update(raw))

        pf = self._slice(frame, "playfield")
        if pf is not None:
            interval = vc.mob_interval
            due = (interval <= 0 or self._mobs_ts is None
                   or now - self._mobs_ts >= interval)
            if due:
                roi, ox, oy = self._search_roi(pf)
                mobs = self.detector.detect(roi)
                if ox or oy:      # 換算回 playfield 座標
                    mobs = [replace(m, cx=m.cx + ox, cy=m.cy + oy) for m in mobs]
                if self._followers is not None:
                    # 傳整個 playfield（不是搜尋框）給它量鏡頭位移：背景紋理越多越準
                    mobs, self.last_followers = self._followers.filter(
                        mobs, pf, self._player_moved(st.player))
                self._mobs_cache = mobs
                self._mobs_ts = now
            st.mobs = self._mobs_cache
        return st

    def _player_moved(self, player: Optional[tuple]) -> bool:
        """角色自上次計分後是否已在小地圖上移動夠遠。

        小地圖一格等於畫面上好幾十 px，所以「移動 1~2 格」就足以讓靜止的怪
        在畫面上明顯滑動。逐幀比對量不出來（8 fps 一個 tick 走不到一格），
        所以基準是上次回報 True 的位置。
        """
        if player is None:
            return False
        if self._prev_player is None:
            self._prev_player = player
            return False
        moved = (abs(player[0] - self._prev_player[0])
                 + abs(player[1] - self._prev_player[1]))
        if moved < self.cfg.vision.player_move_px:
            return False
        self._prev_player = player
        return True

    def _search_roi(self, playfield: np.ndarray):
        """只取角色周圍的攻擊範圍框；回傳 (影像, x偏移, y偏移)。"""
        box = self.cfg.vision.mob_search_box
        if not box:
            return playfield, 0, 0
        h, w = playfield.shape[:2]
        bw, bh = min(box[0], w), min(box[1], h)
        x0 = max((w - bw) // 2, 0)
        y0 = max((h - bh) // 2, 0)
        return playfield[y0:y0 + bh, x0:x0 + bw], x0, y0
