"""主迴圈：擷取 → 感知 → 決策 → 執行，固定頻率運轉。

職責只剩調度與安全：實際辨識在 Perceiver、按鍵在 Executor。
"""
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from . import report as report_mod
from .alerts import Alerts
from .brain import fsm
from .brain.advisor import Advisor
from .config import AppCfg, Profile
from .control.input_win import Keyboard
from .executor import Executor, Stats
from .metrics import LoopMetrics, Series
from .perception import Perceiver
from .progress import ExpTracker
from .safety import LostPlayerWatchdog, Safety, is_black_screen, save_anomaly
from .sysmon import SystemMonitor
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
    fps: float = 0.0
    game_mem_mb: Optional[float] = None


class Runner:
    def __init__(self, cfg: AppCfg, profile: Profile, capture, keyboard: Keyboard,
                 detector: MobDetector, logger, dry_run: bool = False,
                 max_ticks: int = 0, max_seconds: float = 0.0, preview=None):
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
        self.metrics = LoopMetrics(cfg.fps)
        self.series = Series(cfg.report.sample_interval if cfg.report.enabled else 0.0)
        self.sysmon = SystemMonitor(cfg.monitor, logger)
        self.preview = preview

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
        self._started_wall = time.time()
        self._started_mono = time.monotonic()
        self._stop_reason = ""
        self._perf_warned = False
        self._black_since: Optional[float] = None   # 畫面開始全黑的時間點
        self._deaths: list = []                     # 近期復活的時間戳（熔斷用）
        # 決策層的計時器量的是「bot 真的在跑」的時間，不是牆上時鐘。暫停與
        # 黑屏期間主迴圈整個跳過，沒有感知也沒有動作，那段時間不能算進
        # 「低血撐了幾秒」「多久沒賺經驗」這類條件裡（見 _bot_clock）
        self._offline_since: Optional[float] = None
        self._offline_total: float = 0.0

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
        if state.minimap_xy is None:
            self.log.warning("開場找不到小地圖玩家黃點——確認 regions.minimap 只框地圖畫布本體，"
                             "或調整 vision.color_tolerance。先照跑，但巡邏可能不會動")
        self.log.info("開場自檢通過：HP %.0f%%｜%s｜怪 %d",
                      state.hp * 100,
                      f"玩家 {state.minimap_xy}" if state.minimap_xy else "找不到玩家點",
                      len(state.mobs))
        return True

    def _focus_game(self) -> None:
        """開跑前把遊戲視窗帶到前景。

        SendInput 打進的是**前景**視窗——bot 從終端機或 GUI 啟動時焦點在
        那邊，所有按鍵會打進終端機而不是遊戲。這種失敗完全無聲：SendInput
        回報成功、log 照常印「往左走」，角色卻站在原地被怪圍毆到死。
        （實測有一場：喝了 37 瓶藥沒一瓶生效、脫困 9 次。）
        """
        if self.dry_run:
            return
        hwnd = getattr(self.capture, "hwnd", None)
        if hwnd is not None:
            from .window import input_privilege_gap
            if input_privilege_gap(hwnd):
                self.alerts.ping("panic", "遊戲提權了、bot 沒有，按鍵會被默默丟掉")
                self.log.error(
                    "遊戲是以系統管理員執行的，這個 bot 卻不是——Windows 的 UIPI "
                    "會**默默丟掉**所有按鍵（SendInput 回報成功、遊戲收不到、"
                    "角色一步都不會動，還會站在原地被怪圍毆）。"
                    "請關掉 bot，用「以系統管理員身分執行」重新開終端機或 GUI")
        focus_fn = getattr(self.capture, "focus", None)
        if focus_fn is None:
            return
        try:
            ok = focus_fn()
        except Exception as e:
            self.log.warning("聚焦遊戲視窗失敗（%s）——請自己點一下遊戲視窗", e)
            return
        if ok:
            self.log.info("已把遊戲視窗帶到前景（按鍵只送給前景視窗）")
        else:
            self.alerts.ping("warn", "無法聚焦遊戲視窗")
            self.log.warning(
                "無法把遊戲視窗帶到前景！請在 bot 開跑後**自己點一下遊戲視窗**，"
                "否則所有按鍵都會打進別的程式（log 看起來正常、角色卻不會動）")

    # ---- 安全事件 ----

    def _handle_death(self, state, frame: np.ndarray, now: float) -> bool:
        """偵測到死亡復活對話框時點「確定」原地復活。回 True = 這個 tick 處理掉了。

        HP≈0 ＋ 對話框都在（雙重確認）才會動作。熔斷：death_window_minutes
        內復活超過 max_deaths 次就停機報警——復活點一直送死時，別無限復活
        把角色餵給怪。dry-run 只記錄不點。
        """
        if not self.cfg.safety.auto_revive or state.revive_button is None:
            return False

        # 換算 playfield 座標 -> 螢幕絕對座標（screen 擷取：origin＋region＋pf）
        origin = getattr(self.capture, "origin", None)
        pfx, pfy, _, _ = self.cfg.region("playfield")
        bx, by = state.revive_button
        self.alerts.ping("warn", "偵測到死亡，嘗試復活")
        self.log.warning("偵測到死亡復活對話框（HP 0%%）——點「確定」原地復活")
        save_anomaly(frame, "角色死亡（自動復活）", self.log)

        if not self.dry_run and origin is not None:
            self.executor.stop_movement()
            self.kb.release_all()
            sx, sy = origin[0] + pfx + bx, origin[1] + pfy + by
            if hasattr(self.capture, "focus"):
                self.capture.focus()
            if self.kb.click(sx, sy):
                time.sleep(2.5)  # 復活動畫 + 無敵閃爍，別馬上又判一次
            else:
                self.log.error("復活點擊送不出去（%s）——多半是提權不足，"
                               "SendInput/滑鼠都會被 UIPI 擋掉",
                               "錯誤碼 %d" % self.kb.last_error())
                # 失敗**也要**往下計入熔斷：提權/鎖定桌面這種環境問題重試
                # 不會自己好。原本這裡直接 return，結果對話框還在、下一幀
                # 又進來——警報和異常截圖以幀率狂刷一整晚，max_deaths
                # 卻永遠數不到（只在成功路徑計數）。小睡一下壓住重試頻率
                time.sleep(1.0)

        self._deaths.append(now)
        window = self.cfg.safety.death_window_minutes * 60
        if window > 0:
            self._deaths = [t for t in self._deaths if now - t <= window]
        if len(self._deaths) >= self.cfg.safety.max_deaths:
            self._panic(
                frame,
                f"{self.cfg.safety.death_window_minutes:g} 分鐘內死了 "
                f"{len(self._deaths)} 次（達 max_deaths）——復活點一直被送死，"
                "停機等人來看（換巡邏路線、加強補血、或這張圖不適合掛）",
                return_home=False)
        return True

    def _on_advisor_abnormal(self, note: str) -> None:
        self.safety.paused = True
        self.alerts.ping("warn", f"VLM 督導判定異常：{note}")
        save_anomaly(self._last_frame, f"VLM 督導: {note}", self.log)
        self.log.warning("VLM 督導判定異常，已切換為暫停。確認畫面後按 %s 繼續",
                         self.cfg.safety.pause_key)

    def _on_player_lost(self) -> None:
        save_anomaly(self._last_frame, "太久找不到玩家小地圖黃點", self.log)
        self.alerts.ping("warn", "太久找不到玩家小地圖黃點")
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

    # ---- bot 時鐘 ----

    def _go_offline(self, at: float) -> None:
        """主迴圈開始跳過 tick（暫停、黑屏）。"""
        if self._offline_since is None:
            self._offline_since = at

    def _back_online(self, at: float) -> None:
        """恢復跑 tick；把跳過的那段從決策層的時間軸上扣掉。

        不扣的話，暫停十分鐘再繼續，「HP 低於危險線已經 600 秒」會在恢復的
        第一個 tick 就成立——一口藥都還沒按就直接停機，正好跟 critical_hp_seconds
        想做的搶救相反。同一個問題也會讓 exp_stall 與找不到玩家的 watchdog
        一恢復就誤觸發。
        """
        if self._offline_since is not None:
            self._offline_total += at - self._offline_since
            self._offline_since = None

    def _bot_clock(self, wall: float) -> float:
        """牆上時鐘扣掉沒有在跑的時間。給 decide/executor/exp/watchdog 用。

        max_runtime_minutes 與效能取樣仍然看牆上時鐘——那些問的是「現實過了
        多久」，不是「bot 跑了多久」。
        """
        return wall - self._offline_total

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

    # ---- 系統監看與時間限制 ----

    def _attach_game_process(self) -> None:
        """把 sysmon 綁到正在擷取的那個視窗所屬的行程。

        優先用視窗 handle 反查 PID：開兩個客戶端時行程名字一模一樣，
        用名字找會綁到「另一個」——然後那個關掉時我們就誤判成遊戲當了。
        """
        if not self.sysmon.available:
            return
        pid = None
        hwnd = getattr(self.capture, "hwnd", None)
        if hwnd:
            pid = self.sysmon.attach_window(hwnd)
        if pid is None and self.cfg.monitor.process:
            pid = self.sysmon.attach_by_name(self.cfg.monitor.process)
        if pid is None:
            self.log.debug("沒有綁定遊戲行程（離線來源或權限不足），"
                           "遊戲當掉偵測不會生效")

    def _check_system(self, now: float) -> bool:
        """慢迴圈：資源門檻警告 + 遊戲行程消失偵測。回 True 代表要停機。"""
        snap = self.sysmon.poll(now)
        if snap is None:
            return False
        self.status.game_mem_mb = snap.game_mem_mb
        for kind, msg in self.sysmon.alerts(snap):
            self.alerts.ping("warn", msg)
            self.log.warning("%s", msg)
        if self.sysmon.game_lost and self.cfg.monitor.stop_when_game_exits:
            self.alerts.ping("panic", "遊戲行程已結束")
            self.log.error(
                "遊戲行程不見了（當掉、被關掉、或被踢下線）。立刻停止——"
                "再跑下去只是把技能鍵和方向鍵送進桌面或別的程式")
            self._stop_reason = "遊戲行程結束"
            return True
        return False

    def _check_runtime_limit(self, now: float) -> bool:
        limit = self.cfg.safety.max_runtime_minutes * 60
        if limit <= 0 or now - self._started_mono < limit:
            return False
        self.log.warning("已達 safety.max_runtime_minutes=%g 分鐘，自動收工",
                         self.cfg.safety.max_runtime_minutes)
        self.alerts.ping("warn", "達到最長運行時間，自動收工")
        self._stop_reason = f"達到最長運行時間（{self.cfg.safety.max_runtime_minutes:g} 分鐘）"
        return True

    def _notice_perf(self) -> None:
        """跟不上設定的 FPS 時講一次，並指出慢在哪一段。"""
        if self._perf_warned or self.stats.ticks < 80:
            return
        advice = self.metrics.advice()
        if not advice:
            return
        self._perf_warned = True
        self.log.warning("%s", advice)

    def _sample(self, state, now: float) -> None:
        last = self.sysmon.last
        self.series.maybe_add(
            now, self._started_mono,
            hp=state.hp, mp=state.mp, exp=state.exp, mobs=len(state.mobs),
            fps=self.metrics.fps,
            cpu=last.game_cpu if last else None,
            mem_mb=last.game_mem_mb if last else None)

    def _write_report(self) -> None:
        if not self.cfg.report.enabled:
            return
        try:
            data = report_mod.build(
                self.profile.name, self._started_wall, time.time(), time.monotonic(),
                self.stats, self.exp, self.metrics, self.alerts, self.sysmon,
                self.series, dry_run=self.dry_run, stop_reason=self._stop_reason)
            paths = report_mod.save(data, self.cfg.report.dir, self.cfg.report.chart)
            self.log.info("收工報告：%s", "、".join(paths))
            if self.cfg.report.chart and not any(p.endswith(".png") for p in paths):
                self.log.info("（沒有曲線圖：pip install matplotlib 之後就會一起產生）")
        except Exception as e:
            # 報告寫不出來絕不能蓋掉「這場跑完了」這件事，也不該把
            # 主迴圈 finally 區塊的其他清理工作（放開按鍵）擋在後面
            self.log.warning("寫收工報告失敗（不影響已完成的執行）: %s", e)

    def _publish(self, state, action) -> None:
        st = self.status
        st.ticks = self.stats.ticks
        st.hp, st.mp, st.exp = state.hp, state.mp, state.exp
        st.player, st.mobs, st.others = state.minimap_xy, len(state.mobs), len(state.other_players)
        st.followers = len(self.perceiver.last_followers)
        st.action = type(action).__name__
        st.reason = getattr(action, "reason", "")
        st.paused = self.safety.paused
        st.fps = self.metrics.fps

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
        self.alerts.ping("warn", f"SendInput 失敗 {self.kb.failures} 次，按鍵沒送進遊戲")

    def _on_exp_stalled(self, frame: np.ndarray, now: float) -> None:
        mins = self.cfg.safety.exp_stall_minutes
        save_anomaly(frame, f"連續 {mins:.0f} 分鐘沒有經驗進帳", self.log)
        self.alerts.ping("warn", f"連續 {mins:.0f} 分鐘沒有經驗進帳")
        self.log.warning(
            "連續 %.0f 分鐘沒賺到經驗，自動暫停（按 %s 繼續）。"
            "常見原因：技能鍵設錯、怪物偵測抓不到、角色卡在打不到怪的地方",
            mins, self.cfg.safety.pause_key)
        self.safety.paused = True
        self.exp.last_gain_at = now   # 繼續後重新計時，不要一恢復就再次觸發

    def _panic(self, frame: np.ndarray, reason: str, return_home: bool = True) -> None:
        save_anomaly(frame, reason, self.log)
        self.alerts.ping("panic", reason)
        self._stop_reason = f"PANIC：{reason}"
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
            self.alerts.ping("warn", "開場自檢沒過")
            return
        tick_interval = 1.0 / max(self.cfg.fps, 1.0)
        self.log.info("開始執行 profile「%s」%s", self.profile.name,
                      "（dry-run：不會送出任何按鍵）" if self.dry_run else "")
        self.log.info("熱鍵：%s 暫停/繼續，%s 停止",
                      self.cfg.safety.pause_key, self.cfg.safety.stop_key)
        if self.cfg.safety.max_runtime_minutes > 0:
            self.log.info("最長運行 %g 分鐘後自動收工",
                          self.cfg.safety.max_runtime_minutes)
        self._focus_game()
        self._attach_game_process()
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
                    self._go_offline(loop_start)
                    self.executor.stop_movement()
                    self.kb.release_all()
                    time.sleep(0.2)
                    continue

                wall = time.monotonic()
                if self._check_runtime_limit(wall):
                    break
                with self.metrics.stage("monitor"):
                    if self._check_system(wall):
                        break

                with self.metrics.stage("capture"):
                    frame = self.capture.grab()

                if self.cfg.safety.black_screen_pause and is_black_screen(frame):
                    # 換圖（走進傳送門）的淡出也是全黑，一兩秒就過去——要黑得
                    # 夠久才當成斷線暫停，不然巡邏路過傳送門整晚就停在那了
                    self._go_offline(wall)
                    if self._black_since is None:
                        self._black_since = wall
                    if wall - self._black_since >= self.cfg.safety.black_screen_seconds:
                        save_anomaly(frame, "畫面全黑（斷線/換頻道/讀圖）", self.log)
                        self.alerts.ping("warn", "畫面全黑（斷線/換頻道/讀圖）")
                        self.log.warning("畫面持續全黑 %.0f 秒，自動暫停（按 %s 繼續）",
                                         wall - self._black_since,
                                         self.cfg.safety.pause_key)
                        self.safety.paused = True
                        self._black_since = None
                    else:
                        self.executor.stop_movement()   # 換圖途中別按著方向鍵
                    time.sleep(0.2)
                    continue
                if self._black_since is not None:
                    self.log.info("畫面全黑 %.1f 秒後恢復（多半是走進傳送門換圖），繼續",
                                  wall - self._black_since)
                    self._black_since = None
                # 走到這裡才是真的有在跑：暫停/黑屏那段從決策層的時間軸扣掉
                self._back_online(wall)
                now = self._bot_clock(wall)

                with self.metrics.stage("perceive"):
                    state = self.perceiver.perceive(frame, now)
                self._last_frame = frame
                if self.advisor.cfg.enabled:
                    self.advisor.latest_frame = frame
                self.stats.ticks += 1

                if len(state.other_players) > self._prev_others:
                    self.alerts.ping("ding", f"小地圖出現其他玩家（{len(state.other_players)} 人）")
                self._prev_others = len(state.other_players)

                # 死亡復活要排在 exp_stall / decide 之前：死了 exp 當然不會漲，
                # 也不該讓 HP=0 觸發 Panic 停機——先試著復活繼續
                if self._handle_death(state, frame, now):
                    if self.safety.stop:      # 熔斷觸發了 Panic
                        break
                    continue

                self.exp.update(state.exp, now)
                if self.exp.stalled(now):
                    self._on_exp_stalled(frame, now)
                    continue

                if self.watchdog.update(state.minimap_xy is not None, now):
                    self._on_player_lost()
                    continue

                with self.metrics.stage("decide"):
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
                        state.minimap_xy, len(state.mobs), len(state.other_players),
                        type(action).__name__, why)
                self._check_idle(action, now)
                self._notice_followers()
                self._notice_attack_break()
                self._notice_bar_glitch()
                self._publish(state, action)
                self._sample(state, wall)   # 圖表的時間軸是現實時間，跟 _started_mono 同基準
                if self.preview is not None:
                    self.preview.show(frame, state, action, fps=self.metrics.fps,
                                      followers=self.perceiver.last_followers,
                                      extra=self.sysmon.summary())

                if isinstance(action, fsm.Panic):
                    self._panic(frame, action.reason, action.return_home)
                    break
                exec_start = time.monotonic()
                self.executor.execute(action, now)
                exec_seconds = time.monotonic() - exec_start
                self.metrics.record("execute", exec_seconds)
                self._check_input_delivered()

                if time.monotonic() - self._last_summary >= 60:
                    self.log.info("狀態：%s", self.stats.summary())
                    self.log.info("進度：%s", self.exp.summary(time.monotonic()))
                    self.log.info("效能：%s｜%s",
                                  self.metrics.summary(), self.sysmon.summary())
                    self._last_summary = time.monotonic()
                self._notice_perf()

                if self.max_ticks and self.stats.ticks >= self.max_ticks:
                    self.log.info("已達 max_ticks=%d，結束", self.max_ticks)
                    self._stop_reason = self._stop_reason or f"跑滿 max_ticks={self.max_ticks}"
                    break

                elapsed = time.monotonic() - loop_start
                # 扣掉 execute：那段是按住技能鍵/方向鍵的「刻意等待」，
                # 算進超支率的話每一份報告都會說「execute 最慢」而那是廢話
                self.metrics.tick(elapsed, elapsed - exec_seconds)
                if elapsed < tick_interval:
                    time.sleep(tick_interval - elapsed)
        finally:
            if not self._stop_reason and self.safety.stop:
                self._stop_reason = f"使用者按下 {self.cfg.safety.stop_key} 停止"
            self.status.running = False
            self.advisor.stop()
            self.executor.stop_movement()
            self.kb.release_all()
            if self.preview is not None:
                self.preview.close()
            self.log.info("已結束。%s", self.stats.summary())
            self.log.info("進度：%s", self.exp.summary(time.monotonic()))
            self.log.info("效能：%s", self.metrics.summary())
            if self.sysmon.available:
                self.log.info("系統：%s", self.sysmon.summary())
            self.log.info("%s", self.alerts.summary())
            self._write_report()
