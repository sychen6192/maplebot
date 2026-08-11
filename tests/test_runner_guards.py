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
    st = GameState(ts=0.0, hp=0.0, player=(50, 50))
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
    st = GameState(ts=0.0, hp=0.0, player=(50, 50))    # 沒有 revive_button
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
