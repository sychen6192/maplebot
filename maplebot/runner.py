"""主迴圈：擷取 → 感知 → 決策 → 執行，固定頻率運轉。

職責只剩調度與安全：實際辨識在 Perceiver、按鍵在 Executor。
"""
import time
from dataclasses import dataclass
from typing import Optional, Tuple

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


@dataclass
class Status:
    """每個 tick 更新的即時狀態，給 UI 讀（純量欄位，跨執行緒讀取夠安全）。"""
    running: bool = False
    paused: bool = False
    ticks: int = 0
    hp: Optional[float] = None
    mp: Optional[float] = None
    exp: Optional[float] = None
    player: Optional[Tuple[int, int]] = None
    mobs: int = 0
    others: int = 0
    followers: int = 0
    action: str = "-"
    reason: str = ""


class Runner:
    def __init__(self, cfg: AppCfg, profile: Profile, capture, keyboard: Keyboard,
                 detector: MobDetector, logger, dry_run: bool = False,
                 max_ticks: int = 0, max_seconds: float = 0.0):
        self.cfg = cfg
        self.profile = profile
        self.capture = capture
        self.kb = keyboard
        self.log = logger
        self.dry_run = dry_run
        self.max_ticks = max_ticks
        self.max_seconds = max_seconds

        self.safety = Safety(cfg.safety.stop_key, cfg.safety.pause_key, logger)
        self.watchdog = LostPlayerWatchdog(cfg.safety.lost_player_timeout)
        self.alerts = Alerts(cfg.safety.sound_alerts)
        self.rt = fsm.Runtime()
        self.stats = Stats()
        self.status = Status()
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
        self._follower_warned = False
        self._attack_breaks = 0
        self._bar_warned = False

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

    # ---- 開場自檢 ----

    def _match_calibrated_size(self, size):
        """視窗大小跟校正時不一樣時，直接把它調回去。

        ROI 是照某個視窗大小量的，尺寸一變全部錯位。與其開場報錯要使用者
        自己去拉視窗（拉不準就一直錯），不如程式自己調——視窗多大本來就不是
        使用者在意的事。調不動（靜態圖來源、被遊戲鎖住）就照舊報錯。
        """
        resize = getattr(self.capture, "resize_client", None)
        if resize is None:
            return size
        cw, ch = self.cfg.calibrated_for
        self.log.info("遊戲視窗是 %dx%d，校正時是 %dx%d——自動調回去",
                      size[0], size[1], cw, ch)
        new_size = resize(cw, ch)
        if new_size == (cw, ch):
            self.log.info("視窗已調整為 %dx%d", cw, ch)
        return new_size or size

    def _preflight(self) -> bool:
        """跑之前先確認辨識是對的。

        ROI 錯掉時最惡劣的症狀不是「不會動」，而是**看起來在動但全是錯的**：
        血條框錯就讀成 HP 0%，於是灌兩瓶藥再判定瀕死停機（使用者只會看到
        「莫名其妙就停了」）。這些都能在第一幀就看出來，不必等它自爆。
        """
        frame = self.capture.grab()
        size = (frame.shape[1], frame.shape[0])
        if self.cfg.calibrated_for and tuple(self.cfg.calibrated_for) != size:
            size = self._match_calibrated_size(size)
        if self.cfg.calibrated_for and tuple(self.cfg.calibrated_for) != size:
            cw, ch = self.cfg.calibrated_for
            self.log.error(
                "遊戲視窗現在是 %dx%d，但 regions 是照 %dx%d 校正的——"
                "所有 ROI 都會錯位（血條讀成 0%% 之類），而且自動調整也失敗了。"
                "請手動把視窗調回去，或重跑 python tools/calibrate.py "
                "再把新的 regions 貼進 config/local.yaml",
                size[0], size[1], cw, ch)
            return False
        frame = self.capture.grab()

        state = self.perceiver.perceive(frame, time.monotonic())
        if state.hp is None:
            self.log.error("讀不到 HP：regions.hp_bar 超出畫面範圍。請重跑 tools/calibrate.py")
            return False
        if state.hp <= 0.0:
            save_anomaly(frame, "開場自檢：HP 讀值 0%", self.log)
            self.log.error(
                "開場就讀到 HP 0%%——角色真的沒血你也不會現在開 bot，"
                "所以幾乎確定是 regions.hp_bar 框錯了（框到數字、外框或旁邊的 UI）。"
                "先跑 python tools/debug_view.py --snapshot check.png 看框在哪，"
                "再重跑 tools/calibrate.py 只框紅色血條本體。"
                "已存一張畫面到 logs/anomalies/ 方便對照")
            return False
        if state.player is None:
            self.log.warning("開場找不到小地圖玩家黃點——確認 regions.minimap 只框地圖畫布本體，"
                             "或調整 vision.color_tolerance。先照跑，但巡邏可能不會動")
        self.log.info("開場自檢通過：HP %.0f%%｜%s｜怪 %d",
                      state.hp * 100,
                      f"玩家 {state.player}" if state.player else "找不到玩家點",
                      len(state.mobs))
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

    def _notice_followers(self) -> None:
        """跟隨物過濾器抓到東西時說一聲——不然使用者只會看到「怪數量少了一隻」，
        猜不到是寵物被排除了。"""
        if self._follower_warned or not self.perceiver.last_followers:
            return
        self._follower_warned = True
        self.log.info(
            "偵測到 %d 個跟著角色移動的目標（幾乎都是寵物），已排除、不會攻擊。"
            "誤排除到真的怪的話設 vision.filter_followers: false",
            len(self.perceiver.last_followers))

    def _notice_bar_glitch(self) -> None:
        """血條誤讀被擋下來時說一聲——這是「以前會莫名停機」的那個原因。"""
        if self._bar_warned or self.perceiver.bar_glitches == 0:
            return
        self._bar_warned = True
        self.log.info(
            "血條讀值閃了一下（被撞到時的特效），已忽略——這種誤讀以前會被"
            "當成瀕死而停機。一直發生的話把 vision.bar_confirm_frames 調成 3")

    def _notice_attack_break(self) -> None:
        """一直打同一個打不死的東西（寵物、隔著地形的怪）會被強制打斷。"""
        if self.rt.attack_breaks <= self._attack_breaks:
            return
        self._attack_breaks = self.rt.attack_breaks
        self.log.warning(
            "連續攻擊 %.0f 秒但角色位置完全沒變，先去巡邏 %.0f 秒。"
            "常見原因：把寵物當成怪在打、或隔著地形打不到。"
            "第 %d 次觸發——一直發生的話用 tools/debug_view.py --snapshot "
            "看黃框框在什麼東西上",
            self.cfg.safety.attack_stall_seconds,
            self.cfg.safety.attack_break_seconds, self.rt.attack_breaks)

    def _publish(self, state, action) -> None:
        st = self.status
        st.ticks = self.stats.ticks
        st.hp, st.mp, st.exp = state.hp, state.mp, state.exp
        st.player, st.mobs, st.others = state.player, len(state.mobs), len(state.others)
        st.followers = len(self.perceiver.last_followers)
        st.action = type(action).__name__
        st.reason = getattr(action, "reason", "")
        st.paused = self.safety.paused

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
        if not self._preflight():
            self.alerts.ping("warn")
            return
        tick_interval = 1.0 / max(self.cfg.fps, 1.0)
        self.log.info("開始執行 profile「%s」%s", self.profile.name,
                      "（dry-run：不會送出任何按鍵）" if self.dry_run else "")
        self.log.info("熱鍵：%s 暫停/繼續，%s 停止",
                      self.cfg.safety.pause_key, self.cfg.safety.stop_key)
        self.advisor.start()
        self.status.running = True
        deadline = time.monotonic() + self.max_seconds if self.max_seconds else None
        try:
            while not self.safety.stop:
                if deadline is not None and time.monotonic() >= deadline:
                    self.log.info("已達 max_seconds=%.0f，結束", self.max_seconds)
                    break
                loop_start = time.monotonic()
                self.safety.poll()
                # 暫停時整個 tick 都會跳過，狀態也要在這裡更新——不然 UI 上的
                # 燈號會一直停在「執行中」，看起來像按了沒反應
                self.status.paused = self.safety.paused
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
                self._notice_followers()
                self._notice_attack_break()
                self._notice_bar_glitch()
                self._publish(state, action)

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
            self.status.running = False
            self.advisor.stop()
            self.executor.stop_movement()
            self.kb.release_all()
            self.log.info("已結束。%s", self.stats.summary())
            self.log.info("進度：%s", self.exp.summary(time.monotonic()))
