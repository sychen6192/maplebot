"""GUI 控制器：開始/停止/暫停、錄製、log 收集。

用靜態圖片當畫面來源，所以不用 Windows、不用遊戲就能跑完整條路徑。
"""
import logging
import time

import cv2
import numpy as np
import pytest
import yaml

from maplebot.capture import ImageCapture
from maplebot.gui.controller import Controller
from maplebot.recorder import KeyWatcher, Recorder
from maplebot.route import Sample


def _yaml(path, data):
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return str(path)


@pytest.fixture
def shot(tmp_path):
    """一張合成畫面：小地圖有黃點、HP 滿、playfield 有紋理。"""
    img = np.zeros((300, 300, 3), dtype=np.uint8)
    img[10:50, 10:130] = (150, 190, 205)
    img[28:31, 60:63] = (0, 255, 255)
    img[60:68, 10:60] = (0, 0, 230)
    img[80:280, 0:300] = cv2.GaussianBlur(
        np.random.default_rng(2).integers(0, 255, (200, 300, 3), dtype=np.uint8), (5, 5), 0)
    p = tmp_path / "shot.png"
    cv2.imwrite(str(p), img)
    return str(p)


@pytest.fixture
def ctl(tmp_path, shot):
    base = _yaml(tmp_path / "default.yaml", {
        "window": {"title": "X"},
        "loop": {"fps": 60},
        "regions": {"minimap": [10, 10, 120, 40], "hp_bar": [10, 60, 50, 8],
                    "playfield": [0, 80, 300, 200]},
        # 測的是控制器，不是報告。開著的話每跑一次測試就在 repo 的
        # logs/reports/ 留一份垃圾
        "report": {"enabled": False},
    })
    _yaml(tmp_path / "profiles.yaml", {})
    prof = _yaml(tmp_path / "mymap.yaml", {
        "name": "test", "patrol": {"waypoints": [30, 90]}, "attack": {"key": "x"}})
    logger = logging.getLogger("maplebot.test")
    logger.setLevel(logging.INFO)
    return Controller(base, prof, logger=logger,
                      capture_factory=lambda cfg: ImageCapture(shot))


def _wait(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_load_reports_what_it_read(ctl):
    assert ctl.load()
    assert ctl.cfg.window_title == "X"
    assert any("已載入設定" in line for line in ctl.lines)


def test_bad_config_is_reported_not_raised(tmp_path):
    bad = tmp_path / "default.yaml"
    bad.write_text("regions: [this is not a mapping]", encoding="utf-8")
    c = Controller(str(bad), str(bad))
    assert c.load() is False
    assert c.error


def test_start_and_stop(ctl):
    assert ctl.load()
    assert ctl.start(dry_run=True)
    assert _wait(lambda: ctl.status().ticks > 2), "bot 沒有開始跑"
    assert ctl.running
    ctl.stop()
    assert _wait(lambda: not ctl.running), "停不下來"


def test_status_is_published_for_the_ui(ctl):
    assert ctl.load()
    ctl.start(dry_run=True)
    assert _wait(lambda: ctl.status().ticks > 2)
    st = ctl.status()
    ctl.stop()
    assert st.hp == pytest.approx(1.0, abs=0.05)
    assert st.player is not None
    assert st.action != "-"


def test_pause_toggles(ctl):
    assert ctl.load()
    ctl.start(dry_run=True)
    assert _wait(lambda: ctl.status().ticks > 2)
    assert ctl.toggle_pause() is True
    assert ctl.toggle_pause() is False
    ctl.stop()


def test_cannot_start_twice(ctl):
    assert ctl.load()
    assert ctl.start(dry_run=True)
    assert ctl.start(dry_run=True) is False
    ctl.stop()


def test_save_then_reload_keeps_the_values(ctl, tmp_path):
    assert ctl.load()
    values = ctl.values()
    values.update(attack_key="v", attack_range="250", waypoints="12, 44")
    assert ctl.save(values, [("8", "90")])
    assert ctl.values()["attack_key"] == "v"
    assert ctl.values()["attack_range"] == 250
    assert ctl.values()["waypoints"] == "12, 44"
    assert ctl.buff_rows()[0] == ("8", "90.0")


def test_recording_produces_waypoints(ctl):
    assert ctl.load()
    assert ctl.start_record()
    assert _wait(lambda: ctl.recorder and len(ctl.recorder.samples) > 3)
    text = ctl.stop_record()
    assert not ctl.recording
    # 靜態圖片的角色不會動，所以只會錄到一個點——重點是流程不炸、有輸出
    assert text
    assert "patrol:" in ctl.last_route


def test_logs_drain_once(ctl):
    ctl.load()
    first = ctl.drain_logs()
    assert first
    assert ctl.drain_logs() == []


def test_profiles_lists_the_folder(ctl):
    names = ctl.profiles()
    assert any(n.endswith("mymap.yaml") for n in names)


# ---- Recorder 本身 ----

class _Keys:
    def __init__(self, seq):
        self.seq = list(seq)

    def pressed(self):
        return self.seq.pop(0) if self.seq else ()


def test_recorder_collects_samples_and_keys():
    walk = iter([(30, 20), (40, 20), (50, 20)])
    rec = Recorder(lambda t: next(walk), _Keys([("right",), ("right",), ("x",)]))
    rec.start(now=0.0)
    for i in range(3):
        rec.step(now=float(i))
    assert [s.x for s in rec.samples] == [30, 40, 50]
    assert rec.samples[-1].keys == ("x",)
    assert rec.seconds == 2.0
    assert rec.tracked == 3


def test_recorder_counts_frames_without_a_player():
    rec = Recorder(lambda t: (None, None), _Keys([]))
    rec.start(now=0.0)
    rec.step(now=0.0)
    assert rec.tracked == 0


def test_key_watcher_is_silent_off_windows():
    assert KeyWatcher().pressed() == ()


def test_sample_defaults():
    assert Sample(t=0.0, x=None).keys == ()


def _has_cv_gui() -> bool:
    info = cv2.getBuildInformation()
    return any(k in info for k in ("GTK+", "Cocoa", "WIN32 UI", "QT"))


@pytest.mark.skipif(not _has_cv_gui(),
                    reason="opencv-headless 沒有框選視窗（CI 就是這種）")
def test_calibrate_writes_into_local_yaml(tmp_path, shot):
    """校正結果直接寫檔（不用複製貼上），而且不能洗掉其他設定。"""
    import subprocess
    import sys
    base = _yaml(tmp_path / "default.yaml", {"window": {"title": "X"},
                                             "regions": {"minimap": [0, 0, 5, 5]}})
    local = tmp_path / "local.yaml"
    _yaml(local, {"safety": {"critical_hp_ratio": 0.4}})
    out = subprocess.run(
        [sys.executable, "tools/calibrate.py", "--config", base,
         "--source", shot, "--write", str(local)],
        input="\n" * 10, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    saved = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert saved["safety"]["critical_hp_ratio"] == 0.4      # 沒被洗掉
    assert saved["window"]["calibrated_for"] == [300, 300]  # 記下校正時的視窗大小


def test_run_tool_reports_failures(ctl):
    ctl.load()
    assert ctl.run_tool(["-c", "import sys; sys.exit(3)"], "測試")
    assert _wait(lambda: any("失敗" in ln for ln in ctl.lines), timeout=20)


def test_tools_refuse_to_run_while_the_bot_is_running(ctl):
    ctl.load()
    ctl.start(dry_run=True)
    assert _wait(lambda: ctl.status().ticks > 1)
    assert ctl.calibrate() is False
    assert ctl.check_vision() is False
    ctl.stop()
