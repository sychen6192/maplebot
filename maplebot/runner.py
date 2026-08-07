"""主迴圈：擷取 → 感知 → 決策 → 執行，固定頻率運轉。"""
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .brain import fsm
from .brain.advisor import Advisor
from .brain.state import GameState
from .config import AppCfg, Profile
from .control.input_win import Keyboard
from .safety import LostPlayerWatchdog, Safety, save_anomaly
from .vision import minimap, status
from .vision.mobs import MobDetector


@dataclass
class Stats:
    started: float = field(default_factory=time.monotonic)
    ticks: int = 0
    attacks: int = 0
    buffs: int = 0
    potions_hp: int = 0
    potions_mp: int = 0

    def summary(self) -> str:
        mins = (time.monotonic() - self.started) / 60
        return (f"運行 {mins:.1f} 分鐘 | tick {self.ticks} | 攻擊 {self.attacks} 次 | "
                f"buff {self.buffs} 次 | HP 藥 {self.potions_hp} | MP 藥 {self.potions_mp}")


class Runner:
    def __init__(self, cfg: AppCfg, profile: Profile, capture, keyboard: Keyboard,
                 detector: MobDetector, logger, dry_run: bool = False,
                 max_ticks: int = 0):
        self.cfg = cfg
        self.profile = profile
        self.capture = capture
        self.kb = keyboard
        self.detector = detector
        self.log = logger
        self.dry_run = dry_run
        self.max_ticks = max_ticks

        self.safety = Safety(cfg.safety.stop_key, cfg.safety.pause_key, logger)
        self.watchdog = LostPlayerWatchdog(cfg.safety.lost_player_timeout)
        self.rt = fsm.Runtime()
        self.stats = Stats()

        pf = cfg.region("playfield")
        self.playfield_center = (pf[2] // 2, pf[3] // 2)

        self.advisor = Advisor(cfg.advisor, self._on_advisor_abnormal, logger)
        self._last_frame: Optional[np.ndarray] = None
        self._last_summary = time.monotonic()

    # ---- 感知 ----

    def perceive(self, now: float) -> GameState:
        st = GameState(ts=now)
        mm = self.capture.grab(self.cfg.region("minimap"))
        st.player = minimap.find_player(mm, self.cfg.vision)
        st.others = minimap.find_others(mm, self.cfg.vision)

        bars = self.cfg.vision.bar_colors
        for kind in ("hp", "mp", "exp"):
            region_name = f"{kind}_bar"
            if region_name not in self.cfg.regions:
                continue
            ratio = status.bar_ratio(self.capture.grab(self.cfg.regions[region_name]),
                                     bars.get(kind, "red"))
            setattr(st, kind, ratio)

        playfield = self.capture.grab(self.cfg.region("playfield"))
        st.mobs = self.detector.detect(playfield)
        self._last_frame = playfield
        if self.advisor.cfg.enabled:
            self.advisor.latest_frame = playfield
        return st

    # ---- 執行 ----

    def execute(self, action: fsm.Action, now: float) -> None:
        if isinstance(action, fsm.Wait):
            self.log.debug("等待: %s", action.reason)
            time.sleep(action.seconds if not self.dry_run else 0)
            return

        if isinstance(action, fsm.Panic):
            save_anomaly(self._last_frame, action.reason, self.log)
            self.log.error("PANIC: %s，停止所有動作", action.reason)
            self.kb.release_all()
            self.safety.stop = True
            return

        if isinstance(action, fsm.DrinkPotion):
            self.log.info("喝 %s 藥水（%s 鍵）", action.kind.upper(), action.key)
            self.kb.tap(action.key)
            self.rt.note_potion(action.kind, now)
            if action.kind == "hp":
                self.stats.potions_hp += 1
            else:
                self.stats.potions_mp += 1
            return

        if isinstance(action, fsm.CastBuff):
            self.log.info("施放 buff（%s 鍵）", action.key)
            self.kb.tap(action.key)
            self._sleep(action.cast_seconds)
            self.rt.note_buff(action.index, now)
            self.stats.buffs += 1
            return

        if isinstance(action, fsm.Attack):
            arrow = "right" if action.direction > 0 else "left"
            self.log.info("攻擊：面向%s，%s 鍵 x%d",
                          "右" if action.direction > 0 else "左", action.key, action.repeat)
            self.kb.tap(arrow, 0.05)
            for _ in range(max(action.repeat, 1)):
                self.kb.tap(action.key, action.cast_seconds)
                self._sleep(0.1)
            self.rt.last_attack = now
            self.stats.attacks += 1
            return

        if isinstance(action, fsm.Move):
            arrow = "right" if action.direction > 0 else "left"
            self.log.debug("巡邏：往%s走 %.2fs（目標小地圖 x=%d）",
                           "右" if action.direction > 0 else "左", action.seconds, action.target_x)
            self.kb.tap(arrow, action.seconds if not self.dry_run else 0.01)
            return

    def _sleep(self, seconds: float) -> None:
        if not self.dry_run:
            time.sleep(seconds)

    def _on_advisor_abnormal(self, note: str) -> None:
        self.safety.paused = True
        save_anomaly(self._last_frame, f"VLM 督導: {note}", self.log)
        self.log.warning("VLM 督導判定異常，已切換為暫停。確認畫面後按 %s 繼續",
                         self.cfg.safety.pause_key)

    # ---- 主迴圈 ----

    def run(self) -> None:
        tick_interval = 1.0 / max(self.cfg.fps, 1.0)
        self.log.info("開始執行 profile「%s」%s", self.profile.name,
                      "（dry-run：不會送出任何按鍵）" if self.dry_run else "")
        self.log.info("熱鍵：%s 暫停/繼續，%s 停止",
                      self.cfg.safety.pause_key, self.cfg.safety.stop_key)
        self.advisor.start()
        try:
            while not self.safety.stop:
                loop_start = time.monotonic()
                self.safety.poll()
                if self.safety.paused:
                    self.kb.release_all()
                    time.sleep(0.2)
                    continue

                now = time.monotonic()
                state = self.perceive(now)
                self.stats.ticks += 1

                if self.watchdog.update(state.player is not None, now):
                    save_anomaly(self._last_frame, "太久找不到玩家小地圖黃點", self.log)
                    self.log.warning("連續 %.0fs 找不到玩家位置，自動暫停（按 %s 繼續）",
                                     self.cfg.safety.lost_player_timeout,
                                     self.cfg.safety.pause_key)
                    self.safety.paused = True
                    continue

                action = fsm.decide(state, self.cfg, self.profile, self.rt,
                                    now, self.playfield_center)
                if self.dry_run:
                    self.log.info("tick %d | HP %s MP %s | 玩家 %s | 怪 %d | -> %s",
                                  self.stats.ticks,
                                  f"{state.hp:.0%}" if state.hp is not None else "?",
                                  f"{state.mp:.0%}" if state.mp is not None else "?",
                                  state.player, len(state.mobs),
                                  type(action).__name__)
                self.execute(action, now)

                if time.monotonic() - self._last_summary >= 60:
                    self.log.info("狀態：%s", self.stats.summary())
                    self._last_summary = time.monotonic()

                if self.max_ticks and self.stats.ticks >= self.max_ticks:
                    self.log.info("已達 max_ticks=%d，結束", self.max_ticks)
                    break

                elapsed = time.monotonic() - loop_start
                if elapsed < tick_interval:
                    time.sleep(tick_interval - elapsed)
        finally:
            self.advisor.stop()
            self.kb.release_all()
            self.log.info("已結束。%s", self.stats.summary())
