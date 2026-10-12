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
from maplebot.recording.recorder import KeyWatcher, Recorder
from maplebot.recording.route import Sample


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


# ---- 只寫改過的欄位、讀不起來就鎖、範本唯讀 ----

import os
import shutil
import threading

from maplebot import window as window_mod
from maplebot.config import load_profile
from maplebot.diagnostics.selfcheck import VisionReport
from maplebot.gui import controller as controller_mod
from maplebot.gui import setup

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES = os.path.join(REPO, "config", "profiles")
# x=54 那條繩（grab run、tolerance 1）讀凍結的複本：config/profiles 裡那張是
# 使用者自己在掛的地圖，他重錄路線不該讓「存檔不能洗掉這條繩」的測試變紅
ROPE_MAP = os.path.join(REPO, "tests", "fixtures", "profiles", "弓箭手訓練場一-1366x768.yaml")


def _config(tmp_path, **regions):
    data = {
        "window": {"title": "X"},
        "loop": {"fps": 60},
        "regions": {"minimap": [10, 10, 120, 40], "hp_bar": [10, 60, 50, 8],
                    "playfield": [0, 80, 300, 200], **regions},
        "report": {"enabled": False},
    }
    return _yaml(tmp_path / "default.yaml", data)


def _copy_profile(tmp_path, name, as_name=None):
    """name 是 config/profiles 底下的範本檔名，或測試資料的完整路徑。"""
    folder = tmp_path / "profiles"
    folder.mkdir(exist_ok=True)
    src = name if os.path.isabs(name) else os.path.join(PROFILES, name)
    dst = folder / (as_name or os.path.basename(name))
    shutil.copyfile(src, dst)
    return str(dst)


def _ctl(tmp_path, shot, profile, **regions):
    logger = logging.getLogger("maplebot.test")
    logger.setLevel(logging.INFO)
    return Controller(_config(tmp_path, **regions), profile, logger=logger,
                      capture_factory=lambda cfg: ImageCapture(shot))


def test_saving_without_edits_touches_no_file(tmp_path, shot):
    prof = _copy_profile(tmp_path, ROPE_MAP)
    before = open(prof, "rb").read()
    ctl = _ctl(tmp_path, shot, prof)
    assert ctl.load()
    assert ctl.changes(ctl.values(), ctl.buff_rows()) == []
    assert ctl.save(ctl.values(), ctl.buff_rows())
    assert open(prof, "rb").read() == before
    assert not (tmp_path / "local.yaml").exists()


def test_start_does_not_strip_the_rope_waypoint(tmp_path, shot):
    """以前按一次開始就整份重寫：x=54 的繩從 grab run / tolerance 1 變回站定跳抓。"""
    prof = _copy_profile(tmp_path, ROPE_MAP)
    before = open(prof, "rb").read()
    ctl = _ctl(tmp_path, shot, prof)
    assert ctl.load()
    assert ctl.save(ctl.values(), ctl.buff_rows())
    assert ctl.start(dry_run=True)
    assert _wait(lambda: ctl.status().ticks > 2)
    ctl.stop()
    assert _wait(lambda: not ctl.running)
    rope = [w for w in load_profile(prof).patrol.waypoints if w.x == 54][0]
    assert rope.grab == "run" and rope.tolerance == 1
    assert open(prof, "rb").read() == before


def test_one_edit_writes_one_field_and_keeps_the_rest(tmp_path, shot):
    prof = _copy_profile(tmp_path, "example.yaml", "mine.yaml")
    ctl = _ctl(tmp_path, shot, prof)
    assert ctl.load()
    values = ctl.values()
    values["attack_range"] = "250"
    assert ctl.changes(values, ctl.buff_rows()) == ["attack_range"]
    assert ctl.save(values, ctl.buff_rows()), ctl.error
    text = open(prof, encoding="utf-8").read()
    assert "range_px: 250" in text
    assert "# 地圖/職業 profile 範例" in text          # 註解還在
    p = load_profile(prof)
    assert p.buffs[0].cast_seconds == 1.5               # 以前會被重設成 1.2
    assert any("已存檔：水平距離 320 → 250" in ln for ln in ctl.lines)
    assert not (tmp_path / "local.yaml").exists()

    values = ctl.values()
    values["hp_below"] = "60"
    assert ctl.save(values, ctl.buff_rows()), ctl.error
    hp = load_profile(prof).potions["hp"]
    assert (hp.key, hp.cooldown, hp.below_ratio) == ("pageup", 1.0, 0.6)


def test_invalid_edits_are_never_written(tmp_path, shot):
    prof = _copy_profile(tmp_path, "example.yaml", "mine.yaml")
    before = open(prof, "rb").read()
    ctl = _ctl(tmp_path, shot, prof)
    assert ctl.load()
    values = ctl.values()
    values["attack_range"] = "abc"
    assert ctl.save(values, ctl.buff_rows()) is False
    assert ctl.error and ctl.error_code == "INVALID"
    assert "水平距離" in ctl.error
    values = ctl.values()
    values["waypoints"] = "[{y: 3}]"
    assert ctl.save(values, ctl.buff_rows()) is False
    assert "少了 x" in ctl.error
    assert open(prof, "rb").read() == before
    load_profile(prof)                                  # 下次啟動還讀得起來


def test_a_profile_that_cannot_load_locks_saving(tmp_path, shot):
    """以前 profile 壞掉時 save 先把空白表單寫進 local.yaml，才在 merge_into 炸掉。"""
    bad = tmp_path / "broken.yaml"
    bad.write_text("patrol: [unclosed\n", encoding="utf-8")
    ctl = _ctl(tmp_path, shot, str(bad))
    assert ctl.load() is False
    assert ctl.loaded_ok is False and ctl.profile is None
    assert not ctl.can_edit()
    blank = {k: "" for k in ctl.values()}
    assert ctl.save(blank, [("", "")] * 8) is False
    assert ctl.error_code == "NOT_LOADED"
    assert not (tmp_path / "local.yaml").exists()
    assert ctl.start(dry_run=True) is False
    view, _ = ctl.poll()
    assert view.phase == "not_loaded"
    assert view.action == ("notepad", "用記事本開啟")
    assert "broken.yaml" in view.detail


def test_templates_are_read_only_until_saved_as_your_own(tmp_path, shot):
    tmpl = _copy_profile(tmp_path, "example.yaml")
    before = open(tmpl, "rb").read()
    ctl = _ctl(tmp_path, shot, tmpl)
    assert ctl.load() and ctl.is_template
    values = ctl.values()
    values.update(attack_key="x", fps="12")
    assert ctl.save(values, ctl.buff_rows()) is False
    assert ctl.error_code == "TEMPLATE_READONLY"
    assert open(tmpl, "rb").read() == before
    local = yaml.safe_load((tmp_path / "local.yaml").read_text(encoding="utf-8"))
    assert local["loop"]["fps"] == 12                  # Config 的部分照存
    assert ctl.changes(values, ctl.buff_rows()) == ["attack_key"]

    path = ctl.save_as("我的地圖", values, ctl.buff_rows())
    assert path == str(tmp_path / "profiles" / "我的地圖.yaml")
    assert ctl.profile_path == path and not ctl.is_template
    text = open(path, encoding="utf-8").read()
    assert "# 地圖/職業 profile 範例" in text
    mine = load_profile(path)
    assert mine.name == "我的地圖" and mine.attack.key == "x"
    assert open(tmpl, "rb").read() == before
    assert ctl.changes(values, ctl.buff_rows()) == []
    labels = [label for label, _p in ctl.profile_choices()]
    assert labels[0] == "我的地圖" and labels[-1].endswith("（範本）")


def test_an_unchanged_template_can_still_start(tmp_path, shot):
    tmpl = _copy_profile(tmp_path, "example.yaml")
    ctl = _ctl(tmp_path, shot, tmpl)
    assert ctl.load()
    assert ctl.save(ctl.values(), ctl.buff_rows())
    assert ctl.start(dry_run=True)
    ctl.stop()
    assert _wait(lambda: not ctl.running)


def test_switching_maps_is_remembered_for_next_launch(tmp_path, shot):
    a = _copy_profile(tmp_path, "example.yaml", "a.yaml")
    b = _copy_profile(tmp_path, "multilevel.yaml", "b.yaml")
    ctl = _ctl(tmp_path, shot, a)
    assert ctl.load()
    assert ctl.switch_profile(b)
    state = setup.load_state(ctl.state_path)
    assert state["last_profile"] == os.path.abspath(b)
    assert setup.pick_profile("", state, str(tmp_path)) == os.path.abspath(b)


def test_skills_profiles_lock_the_attack_fields(tmp_path, shot):
    prof = _yaml(tmp_path / "skills.yaml", {
        "name": "s", "patrol": {"waypoints": [30, 90]},
        "skills": [{"key": "v", "type": "aoe"}, {"key": "ctrl"}]})
    before = open(prof, "rb").read()
    ctl = _ctl(tmp_path, shot, prof)
    assert ctl.load()
    locked = ctl.locked()
    assert set(locked) == set(controller_mod.ATTACK_FIELDS)
    assert "2 招：v、ctrl" in next(iter(locked.values()))
    values = ctl.values()
    assert values["attack_key"] == "v"                 # 顯示第一招
    values["attack_key"] = "x"
    assert ctl.changes(values, ctl.buff_rows()) == []
    assert ctl.save(values, ctl.buff_rows())
    assert open(prof, "rb").read() == before            # 寫進 attack: 也沒用，乾脆不寫


# ---- 明確的暫停／繼續／停止 ----

def test_pause_and_resume_are_explicit_and_say_why(ctl):
    assert ctl.load()
    assert ctl.start(dry_run=True)
    assert _wait(lambda: ctl.status().ticks > 2)
    assert ctl.pause() is True
    assert _wait(lambda: ctl.status().paused)
    st = ctl.status()
    assert st.paused_by_user and st.pause_reason == "你在控制台按了暫停"
    assert ctl.pause() is False                         # 第二下不會變成繼續
    view, _ = ctl.poll()
    assert view.phase == "paused_user" and view.primary == ("resume", "▶ 繼續", True)
    assert ctl.resume() is True
    assert _wait(lambda: not ctl.status().paused)
    st = ctl.status()
    assert st.pause_reason == "" and not st.paused_by_user
    assert ctl.resume() is False
    ctl.stop()
    assert _wait(lambda: not ctl.running)
    assert ctl.status().stop_reason == "你在控制台按了停止"
    view, _ = ctl.poll()
    assert view.phase == "ended" and view.state == "已停止"


def test_a_failed_preflight_says_why_and_what_to_do(tmp_path, shot):
    ctl = _ctl(tmp_path, shot, _yaml(tmp_path / "m.yaml", {
        "name": "t", "patrol": {"waypoints": [30, 90]}, "attack": {"key": "x"}}),
        hp_bar=[400, 60, 50, 8])
    assert ctl.load()
    assert ctl.start(dry_run=True)
    assert _wait(lambda: not ctl.running)
    assert ctl.status().preflight == "fail"
    view, _ = ctl.poll()
    assert view.phase == "not_started"
    assert "讀不到 HP" in view.detail
    assert view.action[0] == "calibrate"
    assert view.primary[0] == "start" and view.primary[2]


def test_a_good_run_is_starting_before_it_is_running(ctl):
    assert ctl.load()
    assert ctl.start(dry_run=True)
    view, _ = ctl.poll()
    assert view.phase == "starting" and view.primary[2] is False
    assert view.settings_locked and view.can_stop
    assert _wait(lambda: ctl.poll()[0].phase == "running")
    ctl.stop()
    assert _wait(lambda: not ctl.running)


def test_a_missing_window_is_shown_on_the_run_card(tmp_path, shot):
    def no_window(cfg):
        raise RuntimeError("找不到視窗")
    ctl = Controller(_config(tmp_path), _yaml(tmp_path / "m.yaml", {
        "name": "t", "patrol": {"waypoints": [30, 90]}}),
        logger=logging.getLogger("maplebot.test"), capture_factory=no_window)
    assert ctl.load()
    assert ctl.start() is False
    view, _ = ctl.poll()
    assert view.phase == "not_started"
    assert "找不到遊戲視窗「X」" in view.detail
    assert view.action[0] == "window"


def test_a_dry_run_ticks_off_the_last_checklist_step(ctl):
    assert ctl.load()
    assert setup.default_mode(ctl.steps()) == "dry"
    assert ctl.start(dry_run=True)
    assert _wait(lambda: ctl.status().ticks >= setup.DRY_OK_TICKS)
    ctl.poll()
    ctl.stop()
    assert _wait(lambda: not ctl.running)
    dry = [s for s in ctl.steps() if s.id == "dry"][0]
    assert dry.state == "done"
    assert setup.default_mode(ctl.steps()) == "live"


# ---- 工具一次一個 ----

def test_only_one_tool_runs_at_a_time(ctl):
    assert ctl.load()
    assert ctl.run_tool(["-c", "import time; time.sleep(1)"], "測試")
    assert ctl.tool_label == "測試" and ctl.busy
    assert ctl.run_tool(["-c", "pass"], "另一個") is False
    assert "測試還在執行" in ctl.error
    assert ctl.start(dry_run=True) is False
    assert "工具還在執行" in ctl.error
    view, _ = ctl.poll()
    assert view.phase == "tool" and view.settings_locked
    assert _wait(lambda: not ctl.tool_label, timeout=20)
    assert ctl.start(dry_run=True)
    ctl.stop()
    assert _wait(lambda: not ctl.running)


def test_a_failing_tool_keeps_its_last_stderr_line(ctl):
    assert ctl.load()
    gen = ctl.generation
    assert ctl.run_tool(["-c", "import sys; sys.stderr.write('boom happened\\n'); sys.exit(2)"],
                        "校正")
    assert _wait(lambda: not ctl.tool_label, timeout=20)
    assert ctl.tool_failed == ("校正", "boom happened")
    assert ctl.generation > gen
    view, _ = ctl.poll()
    assert view.phase == "failed"
    assert view.detail == "校正沒有完成：boom happened"
    assert view.action == ("calibrate", "再試一次")


def test_grab_nametag_asks_the_template_tool_for_the_nametag(ctl, monkeypatch):
    assert ctl.load()
    calls = []
    monkeypatch.setattr(ctl, "run_tool", lambda argv, label, **kw: calls.append(
        (argv, label, kw)) or True)
    assert ctl.grab_nametag()
    argv, label, kw = calls[0]
    ui = ctl.cfg.vision.ui_templates_dir
    assert argv == ["tools/grab_template.py", "--config", ctl.config_path,
                    "--dir", ui, "--name", "player_nametag"]
    assert label == "截角色名牌"
    assert kw["watch"] == os.path.join(ui, "player_nametag.png")


def test_grab_nametag_is_refused_while_running(ctl):
    assert ctl.load()
    assert ctl.start(dry_run=True)
    assert ctl.grab_nametag() is False
    ctl.stop()
    assert _wait(lambda: not ctl.running)


def test_a_cancelled_nametag_grab_offers_another_try(ctl, tmp_path):
    assert ctl.load()
    watch = str(tmp_path / "never_written.png")
    assert ctl.run_tool(["-c", "pass"], "截角色名牌", watch=watch)
    assert _wait(lambda: not ctl.tool_label, timeout=20)
    assert ctl.tool_cancelled == "截角色名牌"
    view, _ = ctl.poll()
    assert view.phase == "cancelled"
    assert view.action == ("nametag", "再截一次")


# ---- 辨識自檢報告 ----

def test_check_vision_asks_for_a_json_report(ctl, monkeypatch, tmp_path):
    assert ctl.load()
    calls = []
    monkeypatch.setattr(ctl, "run_tool", lambda argv, label, **kw: calls.append(
        (argv, label, kw)) or True)
    out = str(tmp_path / "logs" / "check.png")
    stale = tmp_path / "logs" / "check.json"
    stale.parent.mkdir()
    stale.write_text("{}", encoding="utf-8")
    assert ctl.check_vision(out)
    argv, label, kw = calls[0]
    assert argv[argv.index("--report") + 1] == str(stale)
    assert argv[argv.index("--snapshot") + 1] == out
    assert not stale.exists()                          # 舊報告不會被當成這一次的


def test_a_vision_report_opens_once_and_can_be_confirmed(ctl, tmp_path):
    assert ctl.load()
    good = VisionReport(hp=1.0, mp=1.0, exp=0.5, minimap_xy=(60, 20), screen_xy=(150, 100),
                        mobs=2, nametag_template=True)
    path = tmp_path / "check.json"
    path.write_text(good.to_json(), encoding="utf-8")
    assert ctl.read_vision_report(str(path))
    assert ctl.vision_result == good
    assert ctl.poll()[0].open_vision is True
    assert ctl.poll()[0].open_vision is False
    assert ctl.confirm_vision()
    state = setup.load_state(ctl.state_path)
    assert state["vision_ok"]["fp"] == ctl.regions_fp()
    vision = [s for s in ctl.steps() if s.id == "vision"][0]
    assert vision.state == "done"


def test_a_failing_vision_report_cannot_be_confirmed(ctl, tmp_path):
    assert ctl.load()
    path = tmp_path / "check.json"
    path.write_text(VisionReport(hp=None).to_json(), encoding="utf-8")
    assert ctl.read_vision_report(str(path))
    assert ctl.confirm_vision() is False
    assert setup.load_state(ctl.state_path)["vision_ok"] == {}


# ---- 日誌 ----

def test_the_log_handler_drops_debug_records(tmp_path, shot):
    logger = logging.getLogger("maplebot.test.debug")
    logger.setLevel(logging.DEBUG)
    c = Controller(_config(tmp_path), str(tmp_path / "none.yaml"), logger=logger)
    logger.debug("每個 tick 的等待")
    logger.warning("真的要看的")
    lines = c.drain_logs()
    assert [ln[11:] for ln in lines] == ["真的要看的"]
    assert lines[0].level == "warn" and lines[0].kind == "normal"


def test_tick_lines_stay_out_of_the_log_unless_asked(ctl):
    assert ctl.load()
    assert ctl.start(dry_run=True)
    assert _wait(lambda: ctl.status().ticks > 5)
    ctl.stop()
    assert _wait(lambda: not ctl.running)
    lines = list(ctl.history)
    assert lines and all(isinstance(ln, str) for ln in lines)
    assert all(ln.level in ("info", "warn", "error") for ln in lines)
    assert not any(ln.kind == "tick" for ln in lines)
    assert not any(ln.split("] ", 1)[1].startswith("tick ") for ln in lines)

    ctl.keep_ticks = True
    assert ctl.start(dry_run=True)
    assert _wait(lambda: ctl.status().ticks > 5)
    ctl.stop()
    assert _wait(lambda: not ctl.running)
    assert any(ln.kind == "tick" for ln in ctl.history)


def test_tool_output_is_marked_as_tool_lines(ctl):
    assert ctl.load()
    assert ctl.run_tool(["-c", "print('工具說的話')"], "測試")
    assert _wait(lambda: not ctl.tool_label, timeout=20)
    said = [ln for ln in ctl.history if "工具說的話" in ln]
    assert said and said[0].kind == "tool"


def test_draining_while_another_thread_logs_loses_nothing(tmp_path):
    logger = logging.getLogger("maplebot.test.race")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    c = Controller(_config(tmp_path), str(tmp_path / "none.yaml"), logger=logger)
    n = 10000
    t = threading.Thread(target=lambda: [logger.info("line %d", i) for i in range(n)])
    got = []
    t.start()
    while t.is_alive():
        got += c.drain_logs()
    t.join()
    got += c.drain_logs()
    assert len(got) == n


# ---- 遊戲視窗選單 ----

def test_game_window_candidates_skip_our_own_windows():
    titles = ["", "記事本", "mapleai 控制台", "maplebot debug (q to quit)",
              "▶ 執行中 0:01｜mapleai", "MapleSaga", "新楓之谷：經典版", "記事本"]
    assert controller_mod.game_window_candidates(titles) == [
        "MapleSaga", "新楓之谷：經典版", "記事本"]


def test_title_conflicts_lists_every_match():
    titles = ["MapleSaga", "新楓之谷：經典版", "maple 攻略 - 瀏覽器"]
    assert controller_mod.title_conflicts("maple", titles) == [
        "MapleSaga", "maple 攻略 - 瀏覽器"]
    assert controller_mod.title_conflicts("", titles) == []


def test_open_path_uses_the_injected_opener(ctl, tmp_path):
    opened = []
    assert ctl.open_path(str(tmp_path), opener=opened.append)
    assert opened == [str(tmp_path)]
    assert ctl.open_path("") is False


# ---- 未儲存狀態、環境檢查、準備清單快取 ----

def test_dirty_fields_are_described_with_the_file_values(tmp_path, shot):
    prof = _copy_profile(tmp_path, "example.yaml", "mine.yaml")
    ctl = _ctl(tmp_path, shot, prof)
    assert ctl.load()
    values, rows = ctl.values(), ctl.buff_rows()
    assert ctl.describe_changes(values, rows) == ""
    values.update(attack_key="x", attack_range="250", fps="12", waypoints="30, 90")
    rows = [("9", "60")] + rows[1:]
    assert ctl.changes(values, rows) == ["fps", "attack_key", "attack_range", "waypoints",
                                         "buffs"]
    assert ctl.describe_changes(values, rows) == "迴圈頻率、攻擊鍵、水平距離 等 5 項"
    assert ctl.describe_changes(values, rows, limit=5) == \
        "迴圈頻率、攻擊鍵、水平距離、巡邏點、循環 buff"
    assert ctl.baseline_value("attack_range") == "320"
    assert ctl.baseline_value("buff1") == "8 每 120 秒"
    assert ctl.baseline_value("buff2") == ""
    assert ctl.buff_overflow == 0


def test_a_failed_load_exposes_safe_defaults(tmp_path, shot):
    bad = tmp_path / "broken.yaml"
    bad.write_text("patrol: {waypoints: nope}\n", encoding="utf-8")
    ctl = _ctl(tmp_path, shot, str(bad))
    assert ctl.load() is False
    assert ctl.load_path == str(bad) and "auto" in ctl.load_error
    assert ctl.values() == settings_defaults()
    assert ctl.buff_rows() == [("", "")] * 8
    assert ctl.changes(ctl.values(), ctl.buff_rows()) == []
    assert ctl.problems(ctl.values(), ctl.buff_rows()) == {}
    assert ctl.cfg is not None                          # 全域設定還是讀得到


def settings_defaults():
    from maplebot.gui import settings
    return settings.defaults()


def test_refresh_readiness_assigns_one_result(ctl):
    assert ctl.load()
    assert ctl.readiness is None
    assert ctl.refresh_readiness()
    assert _wait(lambda: ctl.readiness is not None, timeout=60)
    titles = [title for title, _checks in ctl.readiness]
    assert titles[:2] == ["執行環境", "設定檔"]
    text, tone = setup.readiness_summary(ctl.readiness)
    assert text.startswith("環境檢查：")


def test_facts_are_cached_until_the_generation_changes(ctl, monkeypatch):
    assert ctl.load()
    calls = []
    real = ctl._collect_facts
    monkeypatch.setattr(ctl, "_collect_facts", lambda: calls.append(1) or real())
    f1 = ctl.facts(now=100.0)
    assert ctl.facts(now=101.0) is f1                   # 3 秒內、沒有新事件：不重查
    ctl.facts(now=104.0)                                # 過了 3 秒：重查視窗在不在
    ctl._bump()
    ctl.facts(now=104.5)                                # 有新事件：馬上重查
    assert len(calls) == 3
    assert f1.profile_name == "test" and f1.waypoints == "30, 90"
    assert f1.windows == window_mod.IS_WINDOWS


def test_poll_is_cheap_while_running(ctl):
    assert ctl.load()
    assert ctl.start(dry_run=True)
    assert _wait(lambda: ctl.status().ticks > 2)
    t0 = time.perf_counter()
    for _ in range(50):
        view, _lines = ctl.poll()
    per_poll = (time.perf_counter() - t0) / 50
    ctl.stop()
    assert _wait(lambda: not ctl.running)
    assert per_poll < 0.02, f"poll 花了 {per_poll * 1000:.1f} ms"



def test_a_template_save_only_logs_what_reached_local_yaml(tmp_path, shot):
    """範本只寫 Config 那一半；日誌列出攻擊鍵的話，看起來像範本也被改了。"""
    tmpl = _copy_profile(tmp_path, "example.yaml")
    ctl = _ctl(tmp_path, shot, tmpl)
    assert ctl.load()
    values = ctl.values()
    values.update(attack_key="x", fps="12")
    ctl.lines.clear()
    assert ctl.save(values, ctl.buff_rows()) is False
    saved = [ln for ln in ctl.lines if "已存檔" in ln]
    assert len(saved) == 1
    assert "迴圈頻率" in saved[0] and "攻擊鍵" not in saved[0]
