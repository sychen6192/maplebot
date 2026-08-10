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
