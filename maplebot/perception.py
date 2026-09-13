"""感知層：把一張完整的遊戲畫面變成 GameState。

每 tick 只擷取一次完整畫面，各區域用 numpy 切片取得，
所以整個感知過程可以用靜態截圖離線測試。
區域超出畫面（視窗被縮小/校正錯誤）時對應欄位維持 None，
由決策層與 watchdog 處理。
"""
from dataclasses import dataclass, field, replace
from typing import Callable, List, Optional, Tuple

import numpy as np

from .brain.state import GameState
from .config import AppCfg
from .vision import (minimap, mob_hpbar, nametag, player_bar, playfield,
                     revive, status)
from .vision.locate import PLAYER_NAME, load_ui_template
from .vision.follower import FollowerFilter
from .vision.mobs import MobDetector
from .vision.outline_mobs import REFERENCE_WIDTH
from .vision.minimap_yolo import make_minimap_detector
from .vision.track import PointTracker


@dataclass
class MobJob:
    """送給偵測執行緒的一份工作：**同一幀**的畫面與那一幀量到的位置。

    位置一定要跟畫面同一幀。怪的搜尋框以角色為中心、描邊偵測要照角色實際位置
    把自己挖掉——兩者用到不同幀的位置時，挖掉「自己」會挖到空地，而角色本人
    被當成一隻怪打整晚。
    """
    frame: object
    screen_xy: Optional[Tuple[int, int]]
    minimap_xy: Optional[Tuple[int, int]]
    now: float


@dataclass
class MobSnapshot:
    """一次偵測的結果。ts 是**來源那一幀**的時間，不是算完的時間。"""
    mobs: List = field(default_factory=list)
    followers: List = field(default_factory=list)
    ts: float = 0.0


class Perceiver:
    def __init__(self, cfg: AppCfg, detector: MobDetector):
        self.cfg = cfg
        self.detector = detector
        # 小地圖玩家點：顏色遮罩（零設定）或訓練好的模型。兩者介面相同，
        # 回傳的都是候選清單，交給同一個軌跡追蹤挑（見 vision/minimap_yolo.py）
        self.minimap_detector = make_minimap_detector(cfg.vision)
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
        # 設了就改由外部（背景執行緒）供應怪的框，perceive 不再自己偵測。
        # None = 主迴圈自己做（預設）。見 maplebot/pipeline.py
        self.mob_source: Optional[Callable[[], Tuple[Optional[MobSnapshot], float]]] = None
        # 位置的軌跡追蹤（見 vision/track.py）。單幀偵測分不出「真的點」與
        # 「剛好長得像點的地形/UI」，但角色是連續移動的、那些東西不是。
        # 關掉的話兩個 tracker 都設成 None，行為完全退回單幀取最強候選。
        self._mm_track = PointTracker(cfg.vision.minimap_max_jump_px,
                                      cfg.vision.minimap_max_coast) \
            if cfg.vision.track_player else None
        self._screen_track: Optional[PointTracker] = None   # scale 要等第一幀才知道
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
            cands = self.minimap_detector.candidates(mm)
            if self._mm_track is not None:
                st.minimap_xy = self._mm_track.update(cands)
                st.minimap_conf = self._mm_track.confidence
            elif cands:
                x, y, _ = max(cands, key=lambda c: c[2])
                st.minimap_xy, st.minimap_conf = (x, y), 1.0
            st.other_players = minimap.find_others(mm, vc)

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
            st.screen_xy = self._locate_character(pf, scale, vc)
            st.screen_conf = (self._screen_track.confidence
                              if self._screen_track is not None
                              else (1.0 if st.screen_xy is not None else 0.0))
            if self.mob_source is None:
                interval = vc.mob_interval
                due = (interval <= 0 or self._mobs_ts is None
                       or now - self._mobs_ts >= interval)
                if due:
                    self._mobs_cache = self._detect_mobs(
                        pf, st.screen_xy, st.minimap_xy)
                    self._mobs_ts = now
                st.mobs = self._mobs_cache
            else:
                # 框由背景執行緒供應。它算的是**幾幀前**的畫面，所以要把幀齡
                # 一起帶給下游——「兩隻怪」跟「半秒前有兩隻怪」不是同一件事
                snap, _ = self.mob_source()
                if snap is not None:
                    st.mobs = snap.mobs
                    st.mobs_age = max(now - snap.ts, 0.0)
        return st

    def _locate_character(self, pf: np.ndarray, scale: float,
                          vc) -> Optional[tuple]:
        """角色在 playfield 上的位置：名牌 -> 組隊紅條 -> 軌跡。

        名牌自帶「上次命中點附近先搜」的區域搜尋，但那只是省時間；它跟紅條
        都仍然可能在某一幀指到別的東西（名牌被怪擋住、場景裡有紅色物件）。
        軌跡這一層管的是**跨幀**的合理性：一個 tick 跳半個畫面的位置不採信。
        """
        cands: list = []
        if self.nametag is not None:
            hit = self.nametag.locate(pf, scale)
            if hit is not None:
                # 名牌優先於紅條：分數給滿，沒有軌跡時它一定勝出
                cands.append((hit[0], hit[1], float('inf')))
        if not cands and vc.locate_player_bar:
            cands = player_bar.find_player_bar_candidates(
                pf, scale=scale, mask_out=self._overlays())

        if not vc.track_player:
            if not cands:
                return None
            x, y, _ = max(cands, key=lambda c: c[2])
            return (x, y)

        if self._screen_track is None:
            self._screen_track = PointTracker(
                vc.screen_max_jump_px * scale, vc.screen_max_coast)
        xy = self._screen_track.update(cands)
        return xy

    def _detect_mobs(self, pf, screen_xy, minimap_xy) -> list:
        """在一張 playfield 上找怪，回傳 playfield 座標的框。

        `screen_xy` / `minimap_xy` 必須是**同一張 pf** 量到的（見 MobJob）。
        這個方法會動到 detector 與 follower filter 的內部狀態，所以同一時間
        只能有一個執行緒呼叫它——threads.mobs 開著時，那個執行緒是 worker，
        主迴圈完全不碰。
        """
        vc = self.cfg.vision
        roi, ox, oy = self._search_roi(pf, screen_xy)
        roi = self._blank_overlays(roi, ox, oy)
        # 描邊偵測要照**實際**的角色位置挖掉自己，不是畫面正中央
        if hasattr(self.detector, "player_xy"):
            self.detector.player_xy = (
                (screen_xy[0] - ox, screen_xy[1] - oy)
                if screen_xy is not None else None)
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
                mobs, pf, self._player_moved(minimap_xy))
        if screen_xy is not None:
            mobs = self._drop_self(mobs, screen_xy, pf.shape[1])
        return mobs

    def run_mob_job(self, job: MobJob) -> Optional[MobSnapshot]:
        """背景執行緒的工作內容：把一份 MobJob 算成一批框。

        刻意不自己去抓畫面、也不自己量角色位置——那兩件事已經由主迴圈在
        同一幀上做完並包進 job 裡了。自己再量一次的話，框與角色位置會來自
        不同幀（而且還要跟主迴圈搶定位器的內部狀態）。
        """
        pf = self._slice(job.frame, "playfield")
        if pf is None:
            return None
        mobs = self._detect_mobs(pf, job.screen_xy, job.minimap_xy)
        return MobSnapshot(mobs=mobs, followers=list(self.last_followers),
                           ts=job.now)

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
