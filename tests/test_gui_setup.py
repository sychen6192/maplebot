"""準備清單、開跑前把關、控制台狀態檔與 gui.py 的路徑解析（maplebot/gui/setup.py）。"""
import os
import sys
import types
from dataclasses import replace

from maplebot.diagnostics.doctor import FAIL, OK, WARN, Check
from maplebot.gui import live, setup
from maplebot.gui.setup import SetupFacts

FP = setup.regions_fingerprint({"minimap": [1, 2, 3, 4]}, (800, 600))

# 什麼都做好了的事實
READY = SetupFacts(
    windows=True, window_title="新楓之谷", window_found="新楓之谷：經典版",
    window_size=(800, 600), title_matches=("新楓之谷：經典版",), calibrated_for=(800, 600),
    nametag=True, regions_fp=FP, vision_fp=FP, profile_name="弓箭手訓練場一",
    profile_file="弓箭手訓練場一.yaml", template=False, waypoints="30, 90", dry_ok=True)


def _step(steps, sid):
    return next(s for s in steps if s.id == sid)


def test_a_fresh_machine_starts_at_step_one():
    steps = setup.evaluate(SetupFacts(profile_name="範例", template=True))
    assert [s.n for s in steps] == list(range(1, 8))
    first = steps[0]
    assert (first.id, first.state, first.primary) == ("window", "next", True)
    assert sum(s.primary for s in steps) == 1           # 只有一個 Primary 按鈕
    assert _step(steps, "profile").state == "warn"
    assert "範本「範例」" in _step(steps, "profile").detail
    assert _step(steps, "profile").action == "save_as"
    assert setup.tab_title(steps) == "準備 1/7"          # 只有巡邏路線（auto）算完成


def test_calibrated_without_a_nametag_points_at_the_nametag():
    steps = setup.evaluate(replace(READY, nametag=False, vision_fp=""))
    nametag = _step(steps, "nametag")
    assert (nametag.state, nametag.primary, nametag.action) == ("next", True, "nametag")
    assert "會把自己當成怪" in nametag.detail
    assert _step(steps, "vision").state == "todo" and not _step(steps, "vision").primary


def test_a_resized_window_warns_with_both_sizes():
    s = _step(setup.evaluate(replace(READY, window_size=(1366, 768))), "calibrate")
    assert s.state == "warn"
    assert "1366x768" in s.detail and "800x600" in s.detail
    s = _step(setup.evaluate(replace(READY, calibrated_for=None)), "calibrate")
    assert (s.state, s.button) == ("next", "開始校正")


def test_recalibrating_sends_the_vision_check_back_to_todo():
    other = setup.regions_fingerprint({"minimap": [9, 9, 9, 9]}, (800, 600))
    s = _step(setup.evaluate(replace(READY, regions_fp=other)), "vision")
    assert s.state in ("todo", "next")
    assert "校正改過了" in s.detail


def test_off_windows_step_one_explains_why():
    s = _step(setup.evaluate(replace(READY, windows=False)), "window")
    assert s.state == "warn" and "不是 Windows" in s.detail


def test_two_matching_windows_are_a_warning():
    s = _step(setup.evaluate(replace(READY, window_title="maple",
                                     title_matches=("MapleSaga", "maple 攻略"))), "window")
    assert s.state == "warn"
    assert "有 2 個視窗標題都含「maple」，會抓第一個「MapleSaga」" in s.detail


def test_a_busy_tool_marks_its_step():
    s = _step(setup.evaluate(replace(READY, tool="截角色名牌")), "nametag")
    assert s.state == "busy" and "截角色名牌中" in s.detail


def test_route_step_follows_describe_waypoints():
    assert _step(setup.evaluate(replace(READY, waypoints="auto")), "route").state == "done"
    assert _step(setup.evaluate(replace(READY, waypoints="30")), "route").state == "warn"
    assert _step(setup.evaluate(replace(READY, waypoints="abc")), "route").state == "fail"


def test_a_broken_profile_shows_on_step_five():
    s = _step(setup.evaluate(replace(READY, loaded=False, load_error="壞了\n第二行")), "profile")
    assert (s.state, s.action) == ("fail", "notepad")
    assert s.detail == "設定檔讀不起來：壞了"


def test_everything_done():
    steps = setup.evaluate(READY)
    assert setup.progress(steps) == (7, 7)
    assert setup.tab_title(steps) == "準備 √"
    assert not any(s.primary for s in steps)


def test_step_copy_stays_within_the_glyph_whitelist():
    for facts in (SetupFacts(), READY, replace(READY, windows=False, template=True),
                  replace(READY, loaded=False, load_error="x"), replace(READY, tool="校正")):
        for s in setup.evaluate(facts):
            for text in (s.title, s.detail, s.button):
                assert live.symbols_ok(text), text
    for glyph, tone in setup.STEP_ICON.values():
        assert glyph in live.ALLOWED_SYMBOLS


# ---- 開跑前把關、預設模式 ----

def test_live_runs_name_what_is_missing():
    steps = setup.evaluate(replace(READY, nametag=False, vision_fp=""))
    blockers = setup.start_blockers(steps, None, live=True)
    assert blockers == ["3 截角色名牌——沒有它可能把自己當成怪站著打",
                        "4 辨識自檢——血條讀錯會莫名停機"]
    assert setup.start_blockers(steps, None, live=False) == []


def test_live_runs_also_name_doctor_failures():
    readiness = [("執行環境", [Check("套件 opencv-python", FAIL, "沒裝（影像處理）", "pip")]),
                 ("遊戲視窗", [Check("視窗大小 vs 校正值", FAIL, "x", "y")])]
    blockers = setup.start_blockers(setup.evaluate(READY), readiness, live=True)
    assert blockers == ["環境檢查：套件 opencv-python——沒裝（影像處理）"]


def test_default_mode_is_dry_until_a_dry_run_passed():
    assert setup.default_mode(setup.evaluate(replace(READY, dry_ok=False))) == "dry"
    assert setup.default_mode(setup.evaluate(READY)) == "live"


# ---- 環境檢查摘要 ----

def test_readiness_summary_and_rows():
    readiness = [
        ("執行環境", [Check("Python 版本", OK, "3.11"), Check("平台", WARN, "linux", "x"),
                     Check("選配 psutil", WARN, "沒裝", "pip install psutil")]),
        ("設定檔", [Check("ROI 是否在畫面內", FAIL, "超出",
                          "python tools/calibrate.py --write 重新框選"),
                    Check("角色名牌", WARN, "還沒截角色名牌", "…")]),
    ]
    text, tone = setup.readiness_summary(readiness)
    assert text == "環境檢查：1 項通過、1 項提醒、1 項必須修" and tone == "bad"
    rows = setup.readiness_rows(readiness)
    assert len(rows) == 1                               # 選配與清單已經在講的都不列
    glyph, tone, text, action = rows[0]
    assert (glyph, tone) == ("×", "bad")
    assert text == "region 是否在畫面內：超出——準備 › 2 校正 重新框選"
    assert action == ("calibrate", "去校正 →")
    assert setup.readiness_summary(None) == ("環境檢查中…", "muted")
    lines = setup.readiness_lines(readiness)
    assert lines[0] == ("plain", "【執行環境】")
    assert all(live.symbols_ok(t) for _tone, t in lines)


# ---- 狀態檔 ----

def test_state_round_trips_and_survives_corruption(tmp_path):
    path = str(tmp_path / "gui_state.json")
    assert setup.load_state(path)["last_profile"] == ""        # 沒有檔案 = 預設值
    data = setup.load_state(path)
    data.update(last_profile="/x/我的地圖.yaml", geometry="1100x700+10+10",
                vision_ok={"fp": FP, "at": 1.0})
    assert setup.save_state(path, data)
    assert setup.load_state(path) == data
    (tmp_path / "gui_state.json").write_text("{not json", encoding="utf-8")
    assert setup.load_state(path) == setup._defaults()
    (tmp_path / "gui_state.json").write_text('{"last_profile": 3, "dry_ok": []}',
                                             encoding="utf-8")
    assert setup.load_state(path) == setup._defaults()         # 型別不對的鍵用預設
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


def test_state_lives_next_to_the_config(tmp_path):
    assert setup.state_path(str(tmp_path / "default.yaml")) == \
        str(tmp_path / "gui_state.json")


def test_pick_profile_prefers_cli_then_last_then_example(tmp_path):
    last = tmp_path / "last.yaml"
    last.write_text("name: x", encoding="utf-8")
    folder = str(tmp_path / "profiles")
    state = {"last_profile": str(last)}
    assert setup.pick_profile("/cli.yaml", state, folder) == "/cli.yaml"
    assert setup.pick_profile("", state, folder) == str(last)
    assert setup.pick_profile(None, {"last_profile": str(tmp_path / "gone.yaml")}, folder) \
        == os.path.join(folder, "example.yaml")


def test_tool_outcome_reads_the_mtime():
    assert setup.tool_outcome(None, 5.0, 0) == "saved"
    assert setup.tool_outcome(4.0, 5.0, 0) == "saved"
    assert setup.tool_outcome(4.0, 4.0, 0) == "cancelled"
    assert setup.tool_outcome(None, None, 0) == "cancelled"
    assert setup.tool_outcome(4.0, 5.0, 1) == "failed"


def test_regions_fingerprint_is_order_independent():
    a = setup.regions_fingerprint({"a": (1, 2, 3, 4), "b": [5, 6, 7, 8]}, (800, 600))
    b = setup.regions_fingerprint({"b": [5, 6, 7, 8], "a": [1, 2, 3, 4]}, [800, 600])
    assert a == b and len(a) == 12
    assert a != setup.regions_fingerprint({"a": [1, 2, 3, 4], "b": [5, 6, 7, 8]}, None)


# ---- gui.py ----

def test_gui_resolves_paths_before_moving_to_the_repo(tmp_path, monkeypatch):
    """捷徑的「開始位置」在別處時，相對路徑要先變成絕對路徑，再 chdir 回程式資料夾。"""
    import gui
    calls = []
    fake = types.ModuleType("maplebot.gui.app")
    fake.main = lambda config, profile: calls.append((config, profile, os.getcwd())) or 0
    monkeypatch.setitem(sys.modules, "maplebot.gui.app", fake)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "mine.yaml").write_text("name: x", encoding="utf-8")
    assert gui.main(["--profile", "mine.yaml"]) == 0
    config, profile, cwd = calls[0]
    assert profile == str(tmp_path / "mine.yaml")
    assert config == os.path.join(gui.HERE, "config", "default.yaml")
    assert cwd == gui.HERE


def test_resolve_launch_falls_back_to_the_example(tmp_path):
    base = tmp_path / "repo"
    (base / "config").mkdir(parents=True)
    config, profile = setup.resolve_launch(None, None, str(base))
    assert config == str(base / "config" / "default.yaml")
    assert profile == str(base / "config" / "profiles" / "example.yaml")
