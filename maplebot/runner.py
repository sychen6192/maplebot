"""主迴圈：擷取 → 感知 → 決策 → 執行，固定頻率運轉。

職責只剩調度與安全：實際辨識在 Perceiver、按鍵在 Executor。
"""
import time
from typing import Optional

import numpy as np

from .brain import fsm
from .brain.advisor import Advisor
from .config import AppCfg, Profile
from .control.input_win import Keyboard
from .executor import Executor, Stats
from .perception import Perceiver
from .safety import LostPlayerWatchdog, Safety, save_anomaly
from .vision.mobs import MobDetector


class Runner:
    def __init__(self, cfg: AppCfg, profile: Profile, capture, keyboard: Keyboard,
                 detector: MobDetector, logger, dry_run: bool = False,
                 max_ticks: int = 0):
        self.cfg = cfg
        self.profile = profile
        self.capture = capture
        self.kb = keyboard
        self.log = logger
        self.dry_run = dry_run
        self.max_ticks = max_ticks

        self.safety = Safety(cfg.safety.stop_key, cfg.safety.pause_key, logger)
        self.watchdog = LostPlayerWatchdog(cfg.safety.lost_player_timeout)
        self.rt = fsm.Runtime()
        self.stats = Stats()
        self.perceiver = Perceiver(cfg, detector)
        self.executor = Executor(keyboard, self.rt, self.stats, logger, dry_run)

        pf = cfg.region("playfield")
        self.playfield_center = (pf[2] // 2, pf[3] // 2)

        self.advisor = Advisor(cfg.advisor, self._on_advisor_abnormal, logger)
        self._last_frame: Optional[np.ndarray] = None
        self._last_summary = time.monotonic()

    def _on_advisor_abnormal(self, note: str) -> None:
        self.safety.paused = True
        save_anomaly(self._last_frame, f"VLM 督導: {note}", self.log)
        self.log.warning("VLM 督導判定異常，已切換為暫停。確認畫面後按 %s 繼續",
                         self.cfg.safety.pause_key)

    def _on_player_lost(self) -> None:
        save_anomaly(self._last_frame, "太久找不到玩家小地圖黃點", self.log)
        # 視窗可能被移動過，趁暫停時重新定位一次
        if hasattr(self.capture, "refresh"):
            try:
                self.capture.refresh()
                self.log.info("已重新定位遊戲視窗")
            except Exception as e:
                self.log.warning("重新定位視窗失敗: %s", e)
        self.log.warning("連續 %.0fs 找不到玩家位置，自動暫停（按 %s 繼續）",
                         self.cfg.safety.lost_player_timeout, self.cfg.safety.pause_key)
        self.safety.paused = True

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
                frame = self.capture.grab()
                state = self.perceiver.perceive(frame, now)
                self._last_frame = frame
                if self.advisor.cfg.enabled:
                    self.advisor.latest_frame = frame
                self.stats.ticks += 1

                if self.watchdog.update(state.player is not None, now):
                    self._on_player_lost()
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

                if isinstance(action, fsm.Panic):
                    save_anomaly(frame, action.reason, self.log)
                    self.log.error("PANIC: %s，停止所有動作", action.reason)
                    self.kb.release_all()
                    self.safety.stop = True
                    break
                self.executor.execute(action, now)

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
