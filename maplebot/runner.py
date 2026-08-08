"""主迴圈：擷取 → 感知 → 決策 → 執行，固定頻率運轉。

職責只剩調度與安全：實際辨識在 Perceiver、按鍵在 Executor。
"""
import time
from typing import Optional

import numpy as np

from .alerts import Alerts
from .brain import fsm
from .brain.advisor import Advisor
from .config import AppCfg, Profile
from .control.input_win import Keyboard
from .executor import Executor, Stats
from .perception import Perceiver
from .progress import ExpTracker
from .safety import LostPlayerWatchdog, Safety, is_black_screen, save_anomaly
from .vision.locate import BR_NAME, TL_NAME, find_minimap, load_ui_template
from .vision.mobs import MobDetector

IDLE_WARN_SECONDS = 20.0   # 連續閒置這麼久就把 Wait 的理由升級成警告


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
        self.alerts = Alerts(cfg.safety.sound_alerts)
        self.rt = fsm.Runtime()
        self.stats = Stats()
        self.exp = ExpTracker(stall_seconds=cfg.safety.exp_stall_minutes * 60)
        self.perceiver = Perceiver(cfg, detector)
        self.executor = Executor(keyboard, self.rt, self.stats, logger, dry_run)

        pf = cfg.region("playfield")
        self.playfield_center = (pf[2] // 2, pf[3] // 2)

        self.advisor = Advisor(cfg.advisor, self._on_advisor_abnormal, logger)
        self._last_frame: Optional[np.ndarray] = None
        self._last_summary = time.monotonic()
        self._prev_others = 0
        self._input_warned = False
        self._idle_since: Optional[float] = None
        self._idle_warned = False

    # ---- 小地圖自動定位（auto-maple corner-template 法）----

    def _resolve_minimap(self) -> bool:
        if not self.cfg.minimap_auto:
            return True
        ui_dir = self.cfg.vision.ui_templates_dir
        tl = load_ui_template(ui_dir, TL_NAME)
        br = load_ui_template(ui_dir, BR_NAME)
        if tl is None or br is None:
            self.log.error(
                "regions.minimap 設為 auto，但缺少角落模板 %s/%s、%s。"
                "請用 tools/grab_template.py --dir %s --name minimap_tl 截取"
                "（再截 minimap_br），或改回手動座標。",
                ui_dir, TL_NAME, BR_NAME, ui_dir)
            return False
        frame = self.capture.grab()
        region = find_minimap(frame, tl, br, border=self.cfg.vision.minimap_border)
        if region is None:
            self.log.error("找不到小地圖角落（分數過低）。確認小地圖有展開、"
                           "模板是目前解析度截的")
            return False
        self.cfg.regions["minimap"] = region
        self.log.info("小地圖自動定位完成: [%d, %d, %d, %d]", *region)
        return True

    # ---- 安全事件 ----

    def _on_advisor_abnormal(self, note: str) -> None:
        self.safety.paused = True
        self.alerts.ping("warn")
        save_anomaly(self._last_frame, f"VLM 督導: {note}", self.log)
        self.log.warning("VLM 督導判定異常，已切換為暫停。確認畫面後按 %s 繼續",
                         self.cfg.safety.pause_key)

    def _on_player_lost(self) -> None:
        save_anomaly(self._last_frame, "太久找不到玩家小地圖黃點", self.log)
        self.alerts.ping("warn")
        # 視窗可能被移動過，趁暫停時重新定位一次
        if hasattr(self.capture, "refresh"):
            try:
                self.capture.refresh()
                self.log.info("已重新定位遊戲視窗")
            except Exception as e:
                self.log.warning("重新定位視窗失敗: %s", e)
        if self.cfg.minimap_auto:
            self._resolve_minimap()
        self.log.warning("連續 %.0fs 找不到玩家位置，自動暫停（按 %s 繼續）",
                         self.cfg.safety.lost_player_timeout, self.cfg.safety.pause_key)
        self.safety.paused = True

    def _check_idle(self, action, now: float) -> None:
        """一直 Wait 代表某條規則擋著，不是程式當掉——但使用者從 log 看不出來，
        因為 Wait 的理由是 DEBUG 層級。連續閒置太久就把理由升級成警告。"""
        if not isinstance(action, fsm.Wait):
            self._idle_since = None
            self._idle_warned = False
            return
        if self._idle_since is None:
            self._idle_since = now
            return
        if self._idle_warned or now - self._idle_since < IDLE_WARN_SECONDS:
            return
        self._idle_warned = True
        self.log.warning("已連續 %.0f 秒沒有任何動作，原因：%s",
                         now - self._idle_since, action.reason)
        if "其他玩家" in action.reason:
            self.log.warning(
                "畫面上其實沒有別人的話就是小地圖紅點誤判："
                "用 tools/debug_view.py --snapshot 看紅圈畫在哪，"
                "縮小 minimap ROI、調低 vision.color_tolerance，"
                "或先設 safety.pause_when_players: false")

    def _check_input_delivered(self) -> None:
        """SendInput 被擋掉時，log 會照常顯示「已送出按鍵」但遊戲毫無反應。
        只吵一次就好，但一定要吵——這是最常見也最難自己看出來的失敗。"""
        if self._input_warned or self.kb.failures == 0:
            return
        self._input_warned = True
        err = self.kb.last_error()
        self.log.error(
            "按鍵送不進去！SendInput 失敗 %d 次（錯誤碼 %d）。"
            "%s按鍵動作會照常出現在 log，但遊戲收不到。",
            self.kb.failures, err,
            "遊戲是用系統管理員執行的，這個終端機也必須用系統管理員開啟。"
            if err == 5 else "")
        self.alerts.ping("warn")

    def _on_exp_stalled(self, frame: np.ndarray, now: float) -> None:
        mins = self.cfg.safety.exp_stall_minutes
        save_anomaly(frame, f"連續 {mins:.0f} 分鐘沒有經驗進帳", self.log)
        self.alerts.ping("warn")
        self.log.warning(
            "連續 %.0f 分鐘沒賺到經驗，自動暫停（按 %s 繼續）。"
            "常見原因：技能鍵設錯、怪物偵測抓不到、角色卡在打不到怪的地方",
            mins, self.cfg.safety.pause_key)
        self.safety.paused = True
        self.exp.last_gain_at = now   # 繼續後重新計時，不要一恢復就再次觸發

    def _panic(self, frame: np.ndarray, reason: str, return_home: bool = True) -> None:
        save_anomaly(frame, reason, self.log)
        self.alerts.ping("panic")
        self.log.error("PANIC: %s", reason)
        self.executor.stop_movement()
        self.kb.release_all()
        if self.profile.panic_return_key and return_home and not self.dry_run:
            self.log.warning("按下回城卷（%s 鍵）後停止", self.profile.panic_return_key)
            self.kb.tap(self.profile.panic_return_key, 0.1)
            time.sleep(2.0)
        self.log.error("停止所有動作")
        self.safety.stop = True

    # ---- 主迴圈 ----

    def run(self) -> None:
        if not self._resolve_minimap():
            return
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
                    self.executor.stop_movement()
                    self.kb.release_all()
                    time.sleep(0.2)
                    continue

                now = time.monotonic()
                frame = self.capture.grab()

                if self.cfg.safety.black_screen_pause and is_black_screen(frame):
                    save_anomaly(frame, "畫面全黑（斷線/換頻道/讀圖）", self.log)
                    self.alerts.ping("warn")
                    self.log.warning("偵測到黑屏，自動暫停（按 %s 繼續）",
                                     self.cfg.safety.pause_key)
                    self.safety.paused = True
                    continue

                state = self.perceiver.perceive(frame, now)
                self._last_frame = frame
                if self.advisor.cfg.enabled:
                    self.advisor.latest_frame = frame
                self.stats.ticks += 1

                if len(state.others) > self._prev_others:
                    self.alerts.ping("ding")
                self._prev_others = len(state.others)

                self.exp.update(state.exp, now)
                if self.exp.stalled(now):
                    self._on_exp_stalled(frame, now)
                    continue

                if self.watchdog.update(state.player is not None, now):
                    self._on_player_lost()
                    continue

                action = fsm.decide(state, self.cfg, self.profile, self.rt,
                                    now, self.playfield_center)
                if self.dry_run:
                    # Wait 的理由一定要印出來——否則「每 tick 都 Wait」看起來
                    # 像卡住，實際上是某條規則一直擋著（例如誤判有其他玩家）
                    why = f"（{action.reason}）" if isinstance(action, fsm.Wait) else ""
                    self.log.info(
                        "tick %d | HP %s MP %s | 玩家 %s | 怪 %d | 他人 %d | -> %s%s",
                        self.stats.ticks,
                        f"{state.hp:.0%}" if state.hp is not None else "?",
                        f"{state.mp:.0%}" if state.mp is not None else "?",
                        state.player, len(state.mobs), len(state.others),
                        type(action).__name__, why)
                self._check_idle(action, now)

                if isinstance(action, fsm.Panic):
                    self._panic(frame, action.reason, action.return_home)
                    break
                self.executor.execute(action, now)
                self._check_input_delivered()

                if time.monotonic() - self._last_summary >= 60:
                    self.log.info("狀態：%s", self.stats.summary())
                    self.log.info("進度：%s", self.exp.summary(time.monotonic()))
                    self._last_summary = time.monotonic()

                if self.max_ticks and self.stats.ticks >= self.max_ticks:
                    self.log.info("已達 max_ticks=%d，結束", self.max_ticks)
                    break

                elapsed = time.monotonic() - loop_start
                if elapsed < tick_interval:
                    time.sleep(tick_interval - elapsed)
        finally:
            self.advisor.stop()
            self.executor.stop_movement()
            self.kb.release_all()
            self.log.info("已結束。%s", self.stats.summary())
            self.log.info("進度：%s", self.exp.summary(time.monotonic()))
