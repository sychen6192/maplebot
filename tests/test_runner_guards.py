"""主迴圈新增的兩條停機路徑：最長運行時間、遊戲行程消失。

這兩條的失敗模式不對稱：
  - 該停沒停 = bot 對著桌面按技能鍵、或掛到隔天中午
  - 不該停卻停 = 掛機白掛一晚
所以兩個方向都要釘住，特別是「離線用截圖跑」時絕不能誤判成遊戲當了。
"""
import logging
import time

import cv2
import numpy as np
import pytest
import yaml

from maplebot.capture import ImageCapture
from maplebot.config import load_config, load_profile
from maplebot.control.input_win import Keyboard, NullBackend
from maplebot.runner import Runner
from maplebot.vision.mobs import make_detector


@pytest.fixture
def shot(tmp_path):
    img = np.zeros((300, 300, 3), dtype=np.uint8)
    img[10:50, 10:130] = (150, 190, 205)      # 小地圖底
    img[28:31, 60:63] = (0, 255, 255)         # 玩家黃點
    img[60:68, 10:60] = (0, 0, 230)           # HP 條
    img[80:280, 0:300] = cv2.GaussianBlur(
        np.random.default_rng(3).integers(0, 255, (200, 300, 3), dtype=np.uint8),
        (5, 5), 0)
    p = tmp_path / "shot.png"
    cv2.imwrite(str(p), img)
    return str(p)


def _runner(tmp_path, shot, **cfg_extra):
    data = {
        "window": {"title": "X"},
        "loop": {"fps": 60},
        "regions": {"minimap": [10, 10, 120, 40], "hp_bar": [10, 60, 50, 8],
                    "playfield": [0, 80, 300, 200]},
        "report": {"enabled": False},         # 測試不要在 repo 裡留報告
    }
    data.update(cfg_extra)
    cfg_path = tmp_path / "default.yaml"
    cfg_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    prof_path = tmp_path / "map.yaml"
    prof_path.write_text(yaml.safe_dump(
        {"name": "t", "patrol": {"waypoints": [30, 90]}, "attack": {"key": "x"}},
        allow_unicode=True), encoding="utf-8")

    cfg = load_config(str(cfg_path), local_path="")
    profile = load_profile(str(prof_path))
    logger = logging.getLogger("maplebot.test.guards")
    return Runner(cfg, profile, ImageCapture(shot), Keyboard(NullBackend()),
                  make_detector(cfg.vision, profile.templates_dir), logger,
                  dry_run=True)


# ---- 最長運行時間 ----

def test_no_limit_by_default(tmp_path, shot):
    r = _runner(tmp_path, shot)
    assert r._check_runtime_limit(r._started_mono + 86400) is False


def test_limit_fires_only_after_the_deadline(tmp_path, shot):
    r = _runner(tmp_path, shot, safety={"max_runtime_minutes": 10})
    assert r._check_runtime_limit(r._started_mono + 599) is False
    assert r._check_runtime_limit(r._started_mono + 601) is True
    assert "最長運行時間" in r._stop_reason


def test_limit_is_recorded_as_an_alert(tmp_path, shot):
    r = _runner(tmp_path, shot, safety={"max_runtime_minutes": 1})
    r._check_runtime_limit(r._started_mono + 61)
    assert r.alerts.counts() == {"warn": 1}


def test_runtime_limit_actually_ends_the_loop(tmp_path, shot):
    """整迴圈跑一次：限制設成 0 分鐘，第一個 tick 就該收工。"""
    r = _runner(tmp_path, shot, safety={"max_runtime_minutes": 0.0001})
    time.sleep(0.02)
    r.run()
    assert r.status.running is False
    assert "最長運行時間" in r._stop_reason


# ---- 遊戲行程消失 ----

class DeadProcess:
    pid = 1234

    def oneshot(self):
        raise RuntimeError("NoSuchProcess")


def test_offline_source_never_reports_the_game_as_dead(tmp_path, shot):
    """用截圖跑的時候根本沒有遊戲行程可綁。這裡誤判的話，離線開發直接不能用。"""
    r = _runner(tmp_path, shot)
    assert r._check_system(r._started_mono) is False
    assert r.sysmon.game_lost is False


def test_game_exit_stops_the_bot(tmp_path, shot):
    r = _runner(tmp_path, shot)
    if not r.sysmon.available:
        pytest.skip("需要 psutil")
    r.sysmon._proc = DeadProcess()
    assert r._check_system(r._started_mono) is True
    assert "遊戲行程結束" in r._stop_reason


def test_game_exit_can_be_switched_off(tmp_path, shot):
    """有人就是想在遊戲重開的空檔讓 bot 掛著等。"""
    r = _runner(tmp_path, shot, monitor={"stop_when_game_exits": False})
    if not r.sysmon.available:
        pytest.skip("需要 psutil")
    r.sysmon._proc = DeadProcess()
    assert r._check_system(r._started_mono) is False
    assert r.sysmon.game_lost is True        # 仍然記錄，只是不停機


def test_monitor_disabled_skips_the_whole_layer(tmp_path, shot):
    r = _runner(tmp_path, shot, monitor={"enabled": False})
    assert r.sysmon.available is False
    assert r._check_system(r._started_mono) is False


# ---- 效能量測有真的接上 ----

def test_run_records_stage_timings(tmp_path, shot):
    r = _runner(tmp_path, shot)
    r.max_ticks = 3
    r.run()
    assert {"capture", "perceive", "decide"} <= set(r.metrics.stages)
    assert r.metrics.ticks >= 1


def test_report_is_skipped_when_disabled(tmp_path, shot):
    """report.enabled: false 時不該在硬碟上留下任何東西。"""
    r = _runner(tmp_path, shot)
    r.cfg.report.dir = str(tmp_path / "reports")
    r.max_ticks = 2
    r.run()
    assert not (tmp_path / "reports").exists()


def test_report_is_written_when_enabled(tmp_path, shot):
    r = _runner(tmp_path, shot)
    r.cfg.report.enabled = True
    r.cfg.report.chart = False
    r.cfg.report.dir = str(tmp_path / "reports")
    r.max_ticks = 2
    r.run()
    written = list((tmp_path / "reports").glob("session_*"))
    assert {p.suffix for p in written} == {".json", ".md"}


def test_a_broken_report_does_not_break_the_run(tmp_path, shot):
    """報告是收工後才跑的程式碼。它炸掉不能連帶影響「這場已經跑完了」，
    也不能擋住 finally 裡放開按鍵那些清理工作。"""
    r = _runner(tmp_path, shot)
    r.cfg.report.enabled = True
    r.cfg.report.dir = "\0invalid"          # 寫不出來的路徑
    r.max_ticks = 2
    r.run()                                  # 不該丟例外
    assert r.status.running is False


# ---- 黑屏：換圖淡出（短暫）vs 斷線（持續）----

class _FadeCapture:
    """前幾次 grab 正常（讓開場自檢過），之後全黑——模擬傳送門淡出/斷線。"""
    method = "image"

    def __init__(self, shot, black_from=3, black_until=10 ** 9):
        self._img = cv2.imread(shot)
        self._grabs = 0
        self.black_from = black_from
        self.black_until = black_until

    @property
    def size(self):
        h, w = self._img.shape[:2]
        return (w, h)

    def grab(self, region=None):
        self._grabs += 1
        black = self.black_from <= self._grabs <= self.black_until
        img = np.zeros_like(self._img) if black else self._img
        if region is None:
            return img.copy()
        x, y, w, h = region
        return img[y:y + h, x:x + w].copy()


def test_a_brief_blackout_does_not_pause(tmp_path, shot):
    """黑不滿門檻秒數（換圖淡出）絕不能把整晚掛機停在傳送門口。"""
    r = _runner(tmp_path, shot, safety={"black_screen_seconds": 60.0})
    r.capture = _FadeCapture(shot)          # 自檢後開始全黑
    r.max_seconds = 0.8
    r.run()
    assert r.safety.paused is False


def test_a_persistent_blackout_pauses(tmp_path, shot):
    """黑超過門檻（斷線/當機）還是要暫停——緩衝不能把保護整個關掉。"""
    r = _runner(tmp_path, shot, safety={"black_screen_seconds": 0.1})
    r.capture = _FadeCapture(shot)
    r.max_seconds = 1.5
    r.run()
    assert r.safety.paused is True


def test_blackout_recovery_resumes_ticking(tmp_path, shot):
    """黑幾幀後畫面回來（換圖完成）：不暫停、tick 繼續前進。"""
    r = _runner(tmp_path, shot, safety={"black_screen_seconds": 60.0})
    r.capture = _FadeCapture(shot, black_from=3, black_until=4)
    r.max_seconds = 0.8
    r.run()
    assert r.safety.paused is False
    assert r.stats.ticks > 2


# ---- 死亡自動復活 ----

class _StubCapture:
    """會回報 origin 的擷取樁，記錄 focus 呼叫次數。"""
    method = "image"
    origin = (100, 200)
    size = (300, 300)

    def __init__(self):
        self.focused = 0

    def focus(self):
        self.focused += 1
        return True

    def grab(self, region=None):
        return np.zeros((200, 300, 3), dtype=np.uint8)


def _dead_state():
    from maplebot.brain.state import GameState
    st = GameState(ts=0.0, hp=0.0, minimap_xy=(50, 50))
    st.revive_button = (150, 110)      # playfield 座標
    return st


def _live_runner(tmp_path, shot, **cfg_extra):
    """dry_run=False 的 runner，換上會記錄點擊的鍵盤與會回報 origin 的擷取。"""
    r = _runner(tmp_path, shot, **cfg_extra)
    r.dry_run = False
    r.executor.dry_run = False
    r.backend = NullBackend()
    r.kb = Keyboard(r.backend)
    r.executor.kb = r.kb
    r.capture = _StubCapture()
    return r


def test_revive_clicks_the_button_in_screen_coords(tmp_path, shot):
    r = _live_runner(tmp_path, shot)
    frame = np.zeros((200, 300, 3), dtype=np.uint8)
    handled = r._handle_death(_dead_state(), frame, now=1.0)
    assert handled is True
    # origin(100,200) + playfield(0,80) + button(150,110) = (250, 390)
    assert r.backend.clicks == [(250, 390)]
    assert r.capture.focused >= 1


def test_no_revive_button_is_a_noop(tmp_path, shot):
    from maplebot.brain.state import GameState
    r = _live_runner(tmp_path, shot)
    st = GameState(ts=0.0, hp=0.0, minimap_xy=(50, 50))    # 沒有 revive_button
    assert r._handle_death(st, np.zeros((200, 300, 3), np.uint8), now=1.0) is False
    assert r.backend.clicks == []


def test_auto_revive_can_be_disabled(tmp_path, shot):
    r = _live_runner(tmp_path, shot, safety={"auto_revive": False})
    assert r._handle_death(_dead_state(), np.zeros((200, 300, 3), np.uint8), 1.0) is False
    assert r.backend.clicks == []


def test_too_many_deaths_triggers_panic_stop(tmp_path, shot):
    r = _live_runner(tmp_path, shot, safety={"max_deaths": 3, "death_window_minutes": 10})
    frame = np.zeros((200, 300, 3), dtype=np.uint8)
    for i in range(2):
        r._handle_death(_dead_state(), frame, now=float(i))
        assert r.safety.stop is False       # 前兩次只復活，不停機
    r._handle_death(_dead_state(), frame, now=2.0)
    assert r.safety.stop is True            # 第三次熔斷停機
    assert "max_deaths" in r._stop_reason


def test_failed_revive_click_still_counts_toward_the_breaker(tmp_path, shot):
    """點擊送不出去（提權不足/桌面鎖定）重試不會自己好：每次**嘗試**都要
    計入熔斷。原本失敗路徑直接 return，對話框還在、下一幀又重來——警報
    以幀率刷一整晚，max_deaths 永遠數不到。"""
    r = _live_runner(tmp_path, shot, safety={"max_deaths": 3, "death_window_minutes": 10})
    r.backend.click = lambda x, y: False        # 模擬 SetCursorPos 失敗
    frame = np.zeros((200, 300, 3), dtype=np.uint8)
    for i in range(2):
        assert r._handle_death(_dead_state(), frame, now=float(i)) is True
        assert r.safety.stop is False
    r._handle_death(_dead_state(), frame, now=2.0)
    assert r.safety.stop is True
    assert "max_deaths" in r._stop_reason


def test_old_deaths_fall_out_of_the_window(tmp_path, shot):
    """死亡間隔夠久（超過視窗）就不該累積成熔斷——偶發死亡不算連環送死。"""
    r = _live_runner(tmp_path, shot, safety={"max_deaths": 2, "death_window_minutes": 5})
    frame = np.zeros((200, 300, 3), dtype=np.uint8)
    r._handle_death(_dead_state(), frame, now=0.0)
    r._handle_death(_dead_state(), frame, now=10 * 60.0)   # 10 分鐘後，早已出窗
    assert r.safety.stop is False


# ---- bot 時鐘：暫停/黑屏那段不能算進決策層的計時器 ----

def test_bot_clock_excludes_paused_time(tmp_path, shot):
    """暫停十分鐘再繼續，決策層看到的時間只該前進一點點。

    不扣掉的話，恢復後的第一個 tick 就會同時滿足「低血撐了 600 秒」與
    「10 分鐘沒賺經驗」——一口藥都還沒按、一隻怪都還沒打就停機或再暫停。
    """
    r = _runner(tmp_path, shot)

    assert r._bot_clock(1000.0) == 1000.0      # 沒暫停過 = 就是牆上時鐘
    r._go_offline(1000.0)
    r._go_offline(1200.0)                      # 暫停期間重複呼叫不能重設起點
    r._back_online(1600.0)
    assert r._offline_total == pytest.approx(600.0)
    assert r._bot_clock(1600.0) == pytest.approx(1000.0)

    # 恢復後正常跑：bot 時鐘跟著牆上時鐘等速前進
    assert r._bot_clock(1604.0) == pytest.approx(1004.0)

    # 第二段暫停要累加，不是覆蓋
    r._go_offline(1604.0)
    r._back_online(1610.0)
    assert r._bot_clock(1610.0) == pytest.approx(1004.0)


def test_back_online_without_going_offline_is_a_noop(tmp_path, shot):
    """每個 tick 都會呼叫 _back_online，沒暫停過時不能把時間軸弄歪。"""
    r = _runner(tmp_path, shot)
    r._back_online(1000.0)
    r._back_online(2000.0)
    assert r._offline_total == 0.0
    assert r._bot_clock(2000.0) == 2000.0


# ---- 背景擷取執行緒（loop.threads.capture）----
# 這是效能選項，但它動到「畫面從哪來」，所以失敗模式跟上面兩條一樣不對稱：
# 退不回同步擷取 = 整個不跑；拿過期畫面去決策 = 照著舊世界打。

def test_capture_stays_on_the_main_loop_by_default(tmp_path, shot):
    r = _runner(tmp_path, shot)
    r._start_frame_source()
    assert r.frames is None
    assert r._next_frame() is not None       # 照樣拿得到畫面


def test_the_thread_is_used_when_asked(tmp_path, shot):
    r = _runner(tmp_path, shot, loop={"fps": 60, "threads": {"capture": True}})
    r._start_frame_source()
    try:
        assert r.frames is not None
        assert r._next_frame() is not None
    finally:
        if r.frames is not None:
            r.frames.stop()


def test_it_falls_back_when_the_thread_cannot_get_a_frame(tmp_path, shot):
    """執行緒起不來就退回同步擷取——不能因為這個效能選項讓整個 bot 不跑。"""
    r = _runner(tmp_path, shot, loop={"fps": 60, "threads": {"capture": True}})

    class _Dead:
        def grab(self, region=None):
            raise RuntimeError("擷取壞了")

    r.capture = _Dead()
    r._start_frame_source()
    assert r.frames is None                  # 沒有停在半開的狀態


def test_a_stale_frame_is_not_handed_to_the_decision_layer(tmp_path, shot):
    """畫面太舊時 _next_frame 要回 None，讓主迴圈跳過這個 tick。"""
    r = _runner(tmp_path, shot, loop={"fps": 60, "threads": {"capture": True},
                                      "frame_max_age": 0.2})
    r._start_frame_source()
    try:
        assert r.frames is not None
        r.frames.max_age = -1.0              # 任何幀都算太舊
        assert r._next_frame() is None
    finally:
        r.frames.stop()


def test_frame_max_age_must_be_positive(tmp_path, shot):
    """0 或負數會讓每一幀都被判定太舊，bot 完全不動——設定時就擋下來。"""
    from maplebot.config import ConfigError
    with pytest.raises(ConfigError, match="frame_max_age"):
        _runner(tmp_path, shot, loop={"fps": 60, "frame_max_age": 0})


# ---- 背景怪物偵測（loop.threads.mobs）----

def test_mobs_are_detected_inline_by_default(tmp_path, shot):
    r = _runner(tmp_path, shot)
    r._start_mob_worker()
    assert r.mob_worker is None
    assert r.perceiver.mob_source is None


def test_the_mob_worker_is_used_when_asked(tmp_path, shot):
    r = _runner(tmp_path, shot, loop={"fps": 60, "threads": {"mobs": True}})
    r._start_mob_worker()
    try:
        assert r.mob_worker is not None
        assert r.perceiver.mob_source is not None
    finally:
        r.mob_worker.stop()


def test_a_stale_snapshot_is_dropped(tmp_path, shot):
    """舊的框比沒有框更難察覺：怪早就走開了，bot 還對著空地揮。"""
    from maplebot.perception import MobSnapshot

    r = _runner(tmp_path, shot, loop={"fps": 60, "threads": {"mobs": True},
                                      "mob_max_age": 0.5})
    r._start_mob_worker()
    try:
        r.mob_worker.result.put(MobSnapshot(mobs=["x"], ts=time.monotonic()), 0.0)
        assert r._latest_mobs()[0] is not None

        old = MobSnapshot(mobs=["x"], ts=time.monotonic() - 5.0)
        r.mob_worker.result.put(old, 0.0)
        assert r._latest_mobs()[0] is None
    finally:
        r.mob_worker.stop()


def test_the_job_carries_this_frames_position(tmp_path, shot):
    """送進 worker 的位置必須是**這一幀**量到的，不是上一輪留下的。"""
    from maplebot.perception import MobJob

    r = _runner(tmp_path, shot, loop={"fps": 60, "threads": {"mobs": True}})
    r._start_mob_worker()
    sent = []
    r.mob_worker.submit = lambda job: sent.append(job)
    try:
        frame = r.capture.grab()
        state = r.perceiver.perceive(frame, 1.0)
        r.mob_worker.submit(MobJob(frame, state.screen_xy, state.minimap_xy, 1.0))
        assert sent and sent[0].screen_xy == state.screen_xy
        assert sent[0].minimap_xy == state.minimap_xy
        assert sent[0].frame is frame
    finally:
        r.mob_worker.stop()


def test_mob_max_age_must_be_positive(tmp_path, shot):
    """0 或負數會讓每一批框都被判太舊，怪永遠是 0 隻——設定時就擋下來。"""
    from maplebot.config import ConfigError
    with pytest.raises(ConfigError, match="mob_max_age"):
        _runner(tmp_path, shot, loop={"fps": 60, "mob_max_age": 0})


def test_the_worker_is_stopped_on_shutdown(tmp_path, shot):
    r = _runner(tmp_path, shot, loop={"fps": 60, "threads": {"mobs": True}},
                safety={"max_runtime_minutes": 0})
    r.max_ticks = 2
    r.run()
    assert r.mob_worker is not None
    assert r.mob_worker._thread is None      # 收工時有被 join 掉


# ---- 給 GUI 看的狀態：為什麼暫停／停止、開場自檢走到哪 ----
# GUI 只讀 Status，不伸手進 runner。這些欄位錯了，回來看的人看到的就是
# 「已暫停」卻不知道是自己按的還是 watchdog 判定異常——後者要先看畫面。

def _scripted_poll(r, script):
    """把熱鍵輪詢換成照表操作：第 n 次 poll 時呼叫 script[n]。

    主迴圈每一圈（包括暫停中的空轉圈）都會 poll 一次，所以這是不靠
    執行緒與 sleep 的決定性方式，模擬「使用者在某一圈按了暫停／繼續」。
    """
    calls = iter(range(1, 10 ** 6))

    def poll():
        fn = script.get(next(calls))
        if fn is not None:
            fn()
    r.safety.poll = poll


class _FocusCapture(_FadeCapture):
    """畫面一直正常（自檢會過），並數 focus() 被叫了幾次。"""

    def __init__(self, shot):
        super().__init__(shot, black_from=10 ** 9)
        self.focused = 0

    def focus(self):
        self.focused += 1
        return True


def test_a_persistent_blackout_says_why_it_paused(tmp_path, shot):
    r = _runner(tmp_path, shot, safety={"black_screen_seconds": 0.1})
    r.capture = _FadeCapture(shot)
    r.max_seconds = 1.5
    r.run()
    assert r.safety.paused is True                 # 旗標本身仍是純 bool
    assert "黑屏" in r.status.pause_reason
    assert r.status.paused is True
    assert r.status.paused_by_user is False        # watchdog 暫停，不是人按的


@pytest.mark.parametrize("dry_run, refocused", [(False, 1), (True, 0)])
def test_resume_brings_the_game_back_to_the_foreground(tmp_path, shot, dry_run, refocused):
    """從 GUI 按繼續的那一刻前景是控制台；不帶回遊戲，方向鍵會打進 Tk。
    dry-run 不送鍵，搶焦點只會打擾正在看 GUI 的人。"""
    r = _runner(tmp_path, shot) if dry_run else _live_runner(tmp_path, shot)
    cap = r.capture = _FocusCapture(shot)
    seen = {}

    def pause():
        seen["before"] = cap.focused               # 開跑時 _focus_game 那次不算
        r.safety.pause("測試暫停", by_user=True)

    _scripted_poll(r, {3: pause, 5: r.safety.resume})
    r.max_ticks = 4
    r.run()
    assert r.stats.ticks == 4                      # 真的有恢復並繼續跑
    assert cap.focused - seen["before"] == refocused


def test_status_tells_the_gui_how_the_run_went(tmp_path, shot):
    r = _runner(tmp_path, shot)
    assert r.status.preflight == ""
    r.max_ticks = 2
    r.run()
    st = r.status
    assert st.preflight == "ok"
    assert st.started_at > 0
    assert st.ended_at >= st.started_at
    assert "max_ticks" in st.stop_reason
    assert st.dry_run is True
    assert st.target_fps == 60
    assert st.report_path == ""                    # report.enabled: false


def test_a_failed_preflight_is_published(tmp_path, shot):
    """自檢沒過時 run() 直接 return，不進 finally——狀態也要落在 fail，
    不然 GUI 永遠顯示「啟動中」。"""
    r = _runner(tmp_path, shot, regions={"minimap": [10, 10, 120, 40],
                                         "hp_bar": [500, 500, 50, 8],
                                         "playfield": [0, 80, 300, 200]})
    r.run()
    assert r.status.preflight == "fail"
    assert r.status.ended_at >= r.status.started_at > 0
    assert r.status.running is False


def test_a_minimap_resolve_failure_is_a_failed_preflight(tmp_path, shot):
    r = _runner(tmp_path, shot, regions={"minimap": "auto", "hp_bar": [10, 60, 50, 8],
                                         "playfield": [0, 80, 300, 200]},
                vision={"ui_templates_dir": str(tmp_path / "none")})
    r.run()
    assert r.status.preflight == "fail"


def test_a_preflight_exception_is_still_a_failure(tmp_path, shot):
    r = _runner(tmp_path, shot)

    def boom():
        raise RuntimeError("擷取壞了")
    r._preflight = boom
    with pytest.raises(RuntimeError):
        r.run()
    assert r.status.preflight == "fail"


def test_a_gui_stop_keeps_its_own_reason(tmp_path, shot):
    """以前 finally 一律寫「使用者按下 f12 停止」，GUI 停止時是錯的，還會進報告。"""
    r = _runner(tmp_path, shot)
    _scripted_poll(r, {3: lambda: r.safety.request_stop("你在控制台按了停止")})
    r.run()
    assert r._stop_reason == "你在控制台按了停止"
    assert r.status.stop_reason == "你在控制台按了停止"


def test_a_bare_stop_flag_falls_back_to_the_hotkey_wording(tmp_path, shot):
    r = _runner(tmp_path, shot)

    def stop():
        r.safety.stop = True
    _scripted_poll(r, {2: stop})
    r.run()
    assert r.status.stop_reason == "使用者按下 f12 停止"


def test_report_path_is_absolute(tmp_path, shot, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = _runner(tmp_path, shot)
    r.cfg.report.enabled = True
    r.cfg.report.chart = False
    r.cfg.report.dir = "reports"                   # 相對路徑：GUI 不能依賴工作目錄
    r.max_ticks = 2
    r.run()
    import os
    assert os.path.isabs(r.status.report_path)
    assert r.status.report_path.endswith(".md")
    assert os.path.exists(r.status.report_path)


def test_exp_rate_uses_the_bot_clock(tmp_path, shot):
    """經驗效率要扣掉暫停那段：餵給 ExpTracker 的是 bot 時鐘，算速率也要同一條軸。"""
    from maplebot.brain import fsm
    from maplebot.brain.state import GameState

    r = _runner(tmp_path, shot)
    st = GameState(ts=0.0, hp=1.0)
    r._publish(st, fsm.Wait("x"), 100.0)
    assert r.status.exp_gained is None             # 還沒讀到 EXP，不是 +0%
    assert r.status.exp_per_hour is None

    r.exp.update(0.50, 100.0)
    r.exp.update(0.60, 100.0 + 3600)
    r._publish(st, fsm.Wait("x"), 100.0 + 3600)
    assert r.status.exp_gained == pytest.approx(0.10)
    assert r.status.exp_per_hour == pytest.approx(0.10)


def test_a_stale_pause_reason_is_not_published(tmp_path, shot):
    """舊的 toggle 直接寫 safety.paused = False，原因還留在 Safety 裡；
    Status 不能出現「沒暫停卻有暫停原因」。"""
    r = _runner(tmp_path, shot)
    r.safety.pause("你按了 F9", by_user=True)
    r._publish_pause()
    assert (r.status.paused, r.status.pause_reason, r.status.paused_by_user) == \
        (True, "你按了 F9", True)
    r.safety.paused = False
    r._publish_pause()
    assert (r.status.paused, r.status.pause_reason, r.status.paused_by_user) == \
        (False, "", False)


def test_the_dry_run_tick_line_is_marked(tmp_path, shot, caplog):
    """GUI 靠 tick=True 把每 tick 一行收起來；沒標的話只能比對字串開頭。"""
    caplog.set_level(logging.INFO, logger="maplebot.test.guards")
    r = _runner(tmp_path, shot)
    r.max_ticks = 2
    r.run()
    ticks = [rec for rec in caplog.records if rec.getMessage().startswith("tick ")]
    assert len(ticks) == 2
    assert all(getattr(rec, "tick", False) is True for rec in ticks)
    others = [rec for rec in caplog.records if not rec.getMessage().startswith("tick ")]
    assert others and not any(getattr(rec, "tick", False) for rec in others)


def test_exp_rate_waits_for_enough_data(tmp_path, shot):
    """EXP 條一格約 0.4%：開跑 20 秒跳一格就是「每小時 +78%」。累積夠久才顯示。"""
    from maplebot.brain import fsm
    from maplebot.brain.state import GameState
    from maplebot.runner import RATE_WARMUP

    r = _runner(tmp_path, shot)
    st = GameState(ts=0.0, hp=1.0)
    r.exp.update(0.500, 100.0)
    r.exp.update(0.504, 120.0)
    r._publish(st, fsm.Wait("x"), 120.0)
    assert r.status.exp_gained == pytest.approx(0.004)
    assert r.status.exp_per_hour is None
    r._publish(st, fsm.Wait("x"), 100.0 + RATE_WARMUP)
    assert r.status.exp_per_hour is not None
