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
from .vision import (minimap, mob_hpbar, nametag, player_bar, playfield,
                     revive, status)
from .vision.locate import PLAYER_NAME, load_ui_template
from .vision.follower import FollowerFilter
from .vision.mobs import MobDetector
from .vision.outline_mobs import REFERENCE_WIDTH


class Perceiver:
    def __init__(self, cfg: AppCfg, detector: MobDetector):
        self.cfg = cfg
        self.detector = detector
        # 有玩家點模板就優先用模板匹配（見 vision/minimap.py）
        self.player_template = load_ui_template(cfg.vision.ui_templates_dir, PLAYER_NAME)
        # 角色名牌定位（見 vision/nametag.py）。沒有模板檔就是 None，
        # 自動退回組隊紅條——所以沒截模板的人不會壞掉。
        self.nametag = nametag.load_locator(
            cfg.vision.ui_templates_dir, cfg.vision.nametag_offset,
            cfg.vision.nametag_threshold) if cfg.vision.locate_nametag else None
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
            scale = pf.shape[1] / REFERENCE_WIDTH
            # 死亡偵測：HP 讀到 ≈0 才去找復活對話框（省成本、也避免活著時
            # 誤點）。HP=0 ＋ 對話框都在 = 雙重確認才會真的去點復活。
            if st.hp is not None and st.hp <= 0.02:
                st.revive_button = revive.find_confirm_button(pf, scale)
            if self.nametag is not None:
                st.player_screen = self.nametag.locate(pf, scale)
            if st.player_screen is None and vc.locate_player_bar:
                st.player_screen = player_bar.find_player_bar(
                    pf, scale=scale, mask_out=self._overlays())
            interval = vc.mob_interval
            due = (interval <= 0 or self._mobs_ts is None
                   or now - self._mobs_ts >= interval)
            if due:
                roi, ox, oy = self._search_roi(pf, st.player_screen)
                roi = self._blank_overlays(roi, ox, oy)
                # 描邊偵測要照**實際**的角色位置挖掉自己，不是畫面正中央
                if hasattr(self.detector, "player_xy"):
                    self.detector.player_xy = (
                        (st.player_screen[0] - ox, st.player_screen[1] - oy)
                        if st.player_screen is not None else None)
                # 門檻縮放要以**整個 playfield** 為基準：怪的 sprite 大小
                # 跟遊戲解析度走，跟搜尋框多寬無關
                if hasattr(self.detector, "frame_width"):
                    self.detector.frame_width = pf.shape[1]
                mobs = self.detector.detect(roi)
                if vc.detect_hp_bars:
                    # 怪物頭上的血條是遊戲畫的 UI，顏色固定、不用調門檻——
                    # 專門補描邊偵測漏掉的那幾隻（見 vision/mob_hpbar.py）
                    mobs = mob_hpbar.merge(mobs, mob_hpbar.find_hp_bars(
                        roi, tolerance=vc.hp_bar_tolerance,
                        scale=pf.shape[1] / REFERENCE_WIDTH))
                if ox or oy:      # 換算回 playfield 座標
                    mobs = [replace(m, cx=m.cx + ox, cy=m.cy + oy) for m in mobs]
                if self._followers is not None:
                    # 傳整個 playfield（不是搜尋框）給它量鏡頭位移：背景紋理越多越準
                    mobs, self.last_followers = self._followers.filter(
                        mobs, pf, self._player_moved(st.player))
                if st.player_screen is not None:
                    mobs = self._drop_self(mobs, st.player_screen, pf.shape[1])
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

    def _drop_self(self, mobs, player_xy, frame_width):
        """把落在角色身上的偵測結果丟掉（規則見 vision/playfield.py）。"""
        return playfield.drop_at(mobs, player_xy,
                                 self.cfg.vision.outline_player_box,
                                 frame_width / REFERENCE_WIDTH)

    def _overlays(self):
        return playfield.overlay_rects(self.cfg.regions)

    def _blank_overlays(self, roi: np.ndarray, ox: int, oy: int) -> np.ndarray:
        """把疊在主畫面上的 UI 塗成中灰再拿去找怪。"""
        pf = self.cfg.regions.get("playfield", (0, 0, 0, 0))
        # 搜尋框左上角在 client 區的座標：playfield 原點 + 搜尋框在 playfield 內的偏移
        return playfield.blank_rects(roi, self.cfg.vision.mob_exclude,
                                     (pf[0] + ox, pf[1] + oy))

    def _search_roi(self, playfield: np.ndarray, center=None):
        """只取角色周圍的攻擊範圍框；回傳 (影像, x偏移, y偏移)。

        框要跟著**角色**走，不是釘在畫面中央：鏡頭有跟隨延遲、在地圖邊緣
        還會卡住，角色偏離中心 100~200px 是常態——框釘在中央時，角色腳邊
        另一側的怪整排都在框外，看得到的人以為 bot 瞎了。
        量不到角色位置才退回畫面中央。
        """
        box = self.cfg.vision.mob_search_box
        if not box:
            return playfield, 0, 0
        h, w = playfield.shape[:2]
        bw, bh = min(box[0], w), min(box[1], h)
        cx, cy = center or (w // 2, h // 2)
        x0 = min(max(cx - bw // 2, 0), w - bw)
        y0 = min(max(cy - bh // 2, 0), h - bh)
        return playfield[y0:y0 + bh, x0:x0 + bw], x0, y0
