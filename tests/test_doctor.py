"""環境自檢：每一項檢查的判定，以及「訊息有沒有講到怎麼修」。

自檢工具的價值全在訊息上——`❌ 找不到模型` 沒有用，
`❌ 找不到模型 → 改回 outline 或看 docs/YOLO_TRAINING.md` 才有用。
所以這裡不只斷言狀態，也斷言 fix 欄位有指向具體的動作。
"""
from pathlib import Path
import sys

import pytest
import yaml

from maplebot.config import load_config, load_profile
from maplebot.diagnostics import doctor


def _cfg(tmp_path, **overrides):
    data = {
        "window": {"title": "楓之谷", "calibrated_for": [800, 600]},
        "regions": {"minimap": [18, 98, 128, 58], "hp_bar": [223, 609, 105, 10],
                    "mp_bar": [332, 609, 103, 10], "playfield": [8, 60, 790, 520]},
    }
    data.update(overrides)
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return load_config(str(p), local_path="")


def _profile(tmp_path, **overrides):
    data = {"name": "t", "patrol": {"waypoints": [30, 90]}, "attack": {"key": "ctrl"},
            "potions": {"hp": {"key": "pageup", "below_ratio": 0.5}}}
    data.update(overrides)
    p = tmp_path / "prof.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return load_profile(str(p))


def _by_name(checks, name):
    return next(c for c in checks if c.name == name)


# ---- Python / 套件 ----

def test_python_version_gate():
    assert doctor.check_python((3, 11)).status == doctor.OK
    old = doctor.check_python((3, 7))
    assert old.status == doctor.FAIL
    assert "3.9" in old.detail


def test_missing_required_package_is_fatal_and_names_the_pip_command():
    checks = doctor.check_modules(lambda m: False)
    numpy = _by_name(checks, "套件 numpy")
    assert numpy.status == doctor.FAIL
    assert "pip install numpy" in numpy.fix


def test_missing_optional_package_is_only_a_warning():
    checks = doctor.check_modules(lambda m: m != "psutil")
    psutil = _by_name(checks, "選配 psutil")
    assert psutil.status == doctor.WARN
    assert "psutil" in psutil.fix


def test_everything_installed_is_all_green():
    checks = doctor.check_modules(lambda m: True)
    assert all(c.status == doctor.OK for c in checks)


# ---- ROI ----

def test_required_regions_present(tmp_path):
    checks = doctor.check_regions(_cfg(tmp_path))
    assert _by_name(checks, "必要 ROI").status == doctor.OK


def test_missing_region_points_at_calibrate(tmp_path):
    cfg = _cfg(tmp_path, regions={"hp_bar": [1, 1, 10, 10], "playfield": [0, 0, 50, 50]})
    check = _by_name(doctor.check_regions(cfg), "必要 ROI")
    assert check.status == doctor.FAIL
    assert "minimap" in check.detail
    assert "calibrate.py" in check.fix


def test_minimap_auto_counts_as_configured(tmp_path):
    cfg = _cfg(tmp_path, regions={"minimap": "auto", "hp_bar": [1, 1, 10, 10],
                                  "mp_bar": [1, 1, 10, 10],
                                  "playfield": [0, 0, 50, 50]})
    assert _by_name(doctor.check_regions(cfg), "必要 ROI").status == doctor.OK


def test_region_outside_the_calibrated_window_is_fatal(tmp_path):
    cfg = _cfg(tmp_path, window={"title": "x", "calibrated_for": [800, 600]},
               regions={"minimap": [18, 98, 128, 58], "hp_bar": [223, 609, 105, 10],
                        "mp_bar": [332, 609, 103, 10],
                        "playfield": [8, 60, 900, 520]})     # 8+900 > 800
    check = _by_name(doctor.check_regions(cfg), "ROI 是否在畫面內")
    assert check.status == doctor.FAIL
    assert "playfield" in check.detail


def test_minimap_overlapping_playfield_is_not_a_warning(tmp_path):
    """小地圖本來就畫在遊戲畫面上。把它當警告的話，每個人的預設設定
    一跑自檢就跳假警報。"""
    check = _by_name(doctor.check_regions(_cfg(tmp_path)), "playfield 是否乾淨")
    assert check.status == doctor.OK


def test_hp_bar_inside_playfield_is_a_warning(tmp_path):
    cfg = _cfg(tmp_path, regions={"minimap": [18, 98, 128, 58],
                                  "hp_bar": [100, 100, 105, 10],   # 落在 playfield 裡
                                  "mp_bar": [332, 609, 103, 10],
                                  "playfield": [8, 60, 790, 520]})
    check = _by_name(doctor.check_regions(cfg), "playfield 是否乾淨")
    assert check.status == doctor.WARN
    assert "hp_bar" in check.detail


def test_missing_calibrated_for_is_a_warning(tmp_path):
    cfg = _cfg(tmp_path, window={"title": "x"})
    assert _by_name(doctor.check_regions(cfg),
                    "window.calibrated_for").status == doctor.WARN


# ---- 偵測器 ----

def test_outline_needs_nothing(tmp_path):
    assert doctor.check_detector(_cfg(tmp_path)).status == doctor.OK


def test_yolo_without_a_model_path_is_fatal(tmp_path):
    cfg = _cfg(tmp_path, vision={"mob_detector": "yolo"})
    check = doctor.check_detector(cfg)
    assert check.status == doctor.FAIL
    assert "outline" in check.fix          # 一定要給一條「現在就能跑」的退路


def test_yolo_with_a_missing_file_is_fatal(tmp_path):
    cfg = _cfg(tmp_path, vision={"mob_detector": "yolo", "yolo_model": "nope.pt"})
    assert doctor.check_detector(cfg, exists=lambda p: False).status == doctor.FAIL


def test_yolo_accepts_an_official_pretrained_name(tmp_path):
    """yolo11n.pt 是「名稱」不是路徑，ultralytics 會自己下載——
    不能因為本機沒這個檔就報錯。"""
    cfg = _cfg(tmp_path, vision={"mob_detector": "yolo", "yolo_model": "yolo11n.pt"})
    assert doctor.check_detector(cfg, exists=lambda p: False).status == doctor.OK


def test_yolo_accepts_onnx(tmp_path):
    cfg = _cfg(tmp_path, vision={"mob_detector": "yolo", "yolo_model": "best.onnx"})
    assert doctor.check_detector(cfg, exists=lambda p: True).status == doctor.OK


def test_template_mode_without_templates_is_fatal(tmp_path):
    cfg = _cfg(tmp_path, vision={"mob_detector": "template"})
    check = doctor.check_detector(cfg, listdir=lambda d: [])
    assert check.status == doctor.FAIL
    assert "grab_template" in check.fix


def test_template_mode_with_templates_passes(tmp_path):
    cfg = _cfg(tmp_path, vision={"mob_detector": "template"})
    check = doctor.check_detector(cfg, listdir=lambda d: ["snail.png", "notes.txt"])
    assert check.status == doctor.OK
    assert "1 個模板" in check.detail


def test_remote_mode_reports_the_endpoint(tmp_path):
    cfg = _cfg(tmp_path, vision={"mob_detector": "remote",
                                 "remote_endpoint": "http://x/detect"})
    assert doctor.check_detector(cfg).status == doctor.OK


# ---- Profile ----

def test_profile_with_everything_set(tmp_path):
    checks = doctor.check_profile(_profile(tmp_path), 0.25)
    assert all(c.status == doctor.OK for c in checks)


def test_profile_without_an_attack_key_is_fatal(tmp_path):
    prof = _profile(tmp_path, attack={"key": ""})
    assert _by_name(doctor.check_profile(prof), "攻擊鍵").status == doctor.FAIL


def test_profile_without_waypoints_is_fatal():
    """load_profile 本身就會擋掉空路線，所以這條走不到 YAML——但 GUI 會在
    記憶體裡直接組 Profile，自檢仍該擋。"""
    from maplebot.config import Profile

    check = _by_name(doctor.check_profile(Profile(name="x")), "巡邏路線")
    assert check.status == doctor.FAIL
    assert "auto" in check.fix


def test_auto_waypoints_count_as_a_route(tmp_path):
    prof = _profile(tmp_path, patrol={"waypoints": "auto"})
    assert _by_name(doctor.check_profile(prof), "巡邏路線").status == doctor.OK


def test_potion_threshold_below_the_panic_line_is_fatal(tmp_path):
    """這組設定的症狀是「一掉血就停機」，而人會以為是辨識壞了：
    喝藥門檻比停機線低的話，永遠先觸發停機、輪不到喝藥。"""
    prof = _profile(tmp_path, potions={"hp": {"key": "pageup", "below_ratio": 0.2}})
    check = _by_name(doctor.check_profile(prof, 0.25), "藥水門檻 vs 危險線")
    assert check.status == doctor.FAIL


def test_potion_threshold_above_the_panic_line_is_fine(tmp_path):
    checks = doctor.check_profile(_profile(tmp_path), 0.25)
    assert not any(c.name == "藥水門檻 vs 危險線" for c in checks)


def test_no_potion_configured_is_only_a_warning(tmp_path):
    prof = _profile(tmp_path, potions={})
    assert _by_name(doctor.check_profile(prof, 0.25), "補血藥").status == doctor.WARN


# ---- 彙總 ----

def test_report_counts_and_exit_condition():
    rep = doctor.Report()
    rep.add(doctor.Check("a", doctor.OK))
    rep.add(doctor.Check("b", doctor.WARN))
    rep.add(doctor.Check("c", doctor.FAIL))
    assert (rep.count(doctor.OK), rep.count(doctor.WARN), rep.count(doctor.FAIL)) == (1, 1, 1)
    assert rep.failed is True
    assert "1 項必須修" in rep.summary()


def test_warnings_alone_do_not_fail():
    rep = doctor.Report()
    rep.add(doctor.Check("a", doctor.WARN))
    assert rep.failed is False


def test_fix_is_only_printed_for_problems():
    assert "→" not in doctor.Check("a", doctor.OK, "fine", "do something").line()
    assert "→" in doctor.Check("a", doctor.FAIL, "bad", "do something").line()


def test_writable_check_on_a_real_directory(tmp_path):
    check = doctor.check_writable(str(tmp_path / "logs"))
    assert check.status == doctor.OK
    assert not (tmp_path / "logs" / ".doctor_write_test").exists()   # 探針要清掉


# ---- 角色名牌 ----

def test_missing_nametag_is_a_warning_that_names_both_ways_to_fix_it(tmp_path):
    """沒截名牌不會讓 bot 起不來，但角色會被當成怪打——要講清楚去哪裡補：
    CLI 的指令（problem_for 也靠 player_nametag 這個字認出來）跟控制台的那一步。"""
    check = doctor.check_nametag(str(tmp_path / "ui"))
    assert check.status == doctor.WARN
    assert check.detail == "還沒截角色名牌"
    assert "grab_template.py" in check.fix and "--name player_nametag" in check.fix
    assert str(tmp_path / "ui") in check.fix
    assert "準備" in check.fix


def test_either_nametag_file_counts(tmp_path):
    for name in ("player_nametag.png", "player_feature.png"):
        ui = tmp_path / name.split(".")[0]
        ui.mkdir()
        (ui / name).write_bytes(b"png")
        assert doctor.check_nametag(str(ui)).status == doctor.OK


def test_nametag_check_uses_the_injected_exists(tmp_path):
    seen = []
    check = doctor.check_nametag("ui", exists=lambda p: seen.append(p) or False)
    assert check.status == doctor.WARN
    assert len(seen) == 2


def test_nametag_files_match_what_the_locator_loads():
    """doctor 為了不連帶載入 cv2 自己抄了一份檔名，兩邊不能分家。"""
    from maplebot.vision.nametag import FEATURE_NAMES
    assert set(doctor.NAMETAG_FILES) == set(FEATURE_NAMES)


# ---- run_all（tools/doctor.py 與 GUI 共用的組合）----

def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return str(p)


def _cfg_path(tmp_path):
    # 這組 region 的血條在 y=609，校正尺寸要裝得下，否則「ROI 是否在畫面內」會 FAIL
    return _write(tmp_path, "cfg.yaml", {
        "window": {"title": "楓之谷", "calibrated_for": [1024, 768]},
        "regions": {"minimap": [18, 98, 128, 58], "hp_bar": [223, 609, 105, 10],
                    "mp_bar": [332, 609, 103, 10], "playfield": [8, 60, 790, 520]},
    })


def _profile_path(tmp_path):
    return _write(tmp_path, "prof.yaml", {
        "name": "t", "patrol": {"waypoints": [30, 90]}, "attack": {"key": "ctrl"},
        "potions": {"hp": {"key": "pageup", "below_ratio": 0.5}}})


def _names(sections):
    return [(title, [c.name for c in checks]) for title, checks in sections]


def test_run_all_is_the_same_composition_as_the_cli(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)              # check_writable 會建 logs/
    cfg_path, prof_path = _cfg_path(tmp_path), _profile_path(tmp_path)
    sections = doctor.run_all(cfg_path, prof_path, lambda m: True,
                              window_finder=lambda title: None)

    cfg = load_config(cfg_path)
    expected = [
        ("執行環境", ["Python 版本", "平台", "套件 numpy", "套件 opencv-python",
                      "套件 PyYAML", "選配 mss", "選配 psutil", "選配 matplotlib",
                      "選配 ultralytics", "選配 requests", "可寫入 logs/"]),
        ("設定檔", ["全域設定", "必要 ROI", "ROI 是否在畫面內", "playfield 是否乾淨",
                    "怪物偵測", "角色名牌",
                    # psutil 有沒有裝決定這項叫「遊戲行程」還是「遊戲行程監看」
                    doctor.check_game_process(cfg).name]),
        (f"Profile（{prof_path}）", ["Profile", "攻擊鍵", "巡邏路線", "補血藥", "射程設定"]),
    ]
    if sys.platform == "win32":              # 非 Windows 整段跳過
        expected.append(("遊戲視窗", ["遊戲視窗"]))
    assert _names(sections) == expected
    assert doctor.config_failed(sections) is False
    nametag = _by_name(sections[1][1], "角色名牌")
    assert nametag.status == doctor.WARN     # conftest 把 UI 模板目錄指到空資料夾


def test_run_all_stops_when_the_config_cannot_be_read(tmp_path, monkeypatch):
    """全域設定讀不起來時後面每一項都要 cfg，硬跑只會多一串無關的錯。"""
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / "bad.yaml"
    bad.write_text("window: [unclosed\n", encoding="utf-8")
    sections = doctor.run_all(str(bad), _profile_path(tmp_path), lambda m: True)
    assert [t for t, _ in sections] == ["執行環境", "設定檔"]
    only = sections[1][1]
    assert [(c.name, c.status) for c in only] == [("全域設定", doctor.FAIL)]
    assert str(bad) in only[0].fix
    assert doctor.config_failed(sections) is True


def test_run_all_reports_an_unreadable_source_shot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sections = doctor.run_all(_cfg_path(tmp_path), _profile_path(tmp_path),
                              lambda m: True, window_finder=lambda t: None,
                              source=str(tmp_path / "nope.png"))
    title, checks = sections[3]
    assert title == "辨識（用 --source 的截圖驗）"
    assert [(c.name, c.status) for c in checks] == [("截圖", doctor.FAIL)]


# ---- tools/doctor.py ----

def _doctor_cli():
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tools", "doctor.py")
    spec = importlib.util.spec_from_file_location("doctor_cli", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_exits_1_on_a_broken_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cli = _doctor_cli()
    monkeypatch.setattr(cli, "_can_import", lambda m: True)
    bad = tmp_path / "bad.yaml"
    bad.write_text("window: [unclosed\n", encoding="utf-8")
    assert cli.main(["--config", str(bad), "--profile",
                     _profile_path(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "設定檔讀不起來，後面的檢查沒有意義" in out
    assert "── Profile" not in out


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows 上會真的去找遊戲視窗，找不到就是 exit 1")
def test_cli_prints_every_check_of_run_all_in_order(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cli = _doctor_cli()
    monkeypatch.setattr(cli, "_can_import", lambda m: True)
    cfg_path, prof_path = _cfg_path(tmp_path), _profile_path(tmp_path)
    assert cli.main(["--config", cfg_path, "--profile", prof_path]) == 0
    out = capsys.readouterr().out

    pos = 0
    for title, checks in doctor.run_all(cfg_path, prof_path, lambda m: True):
        pos = out.index(f"── {title} ", pos)
        for c in checks:
            pos = out.index("  " + c.line(), pos)
    assert "項提醒" in out[pos:]


# ---- 射程、追擊、後退、排除範圍、找怪範圍互相打架 ----

def _reach(**prof):
    from maplebot.config import AppCfg, Profile
    cfg = AppCfg()
    cfg.regions = {"playfield": [0, 0, 790, 400]}     # 1:1，數字直接對得上
    p = Profile()
    p.attack.range_px = prof.get("range_px", 110)
    p.chase_px = prof.get("chase_px", 0)
    p.keep_away_px = prof.get("keep_away_px", 0)
    if "box" in prof:
        cfg.vision.mob_search_box = prof["box"]
    return [c for c in doctor.check_reach(cfg, p) if c.status != doctor.OK]


def test_reach_settings_that_agree_are_ok():
    from maplebot.config import AppCfg, Profile
    cfg, p = AppCfg(), Profile()
    p.attack.range_px, p.chase_px = 55, 120
    checks = doctor.check_reach(cfg, p)
    assert [c.status for c in checks] == [doctor.OK]


def test_keep_away_inside_the_self_exclusion_never_fires():
    """法師範本的 45：貼身的怪在 ±50 就被當成角色濾掉，後退永遠不會觸發。"""
    (c,) = _reach(range_px=110, keep_away_px=45)
    assert "keep_away_px=45" in c.detail and "outline_player_box" in c.fix


def test_keep_away_beyond_the_range_means_never_attacking():
    problems = _reach(range_px=110, keep_away_px=120)
    assert any("不小於" in c.detail and "range_px=110" in c.detail for c in problems)


def test_chase_shorter_than_the_range_never_chases():
    problems = _reach(range_px=110, chase_px=100)
    assert any("chase_px=100" in c.detail for c in problems)


def test_a_search_box_narrower_than_the_chase_is_reported_in_real_pixels():
    problems = _reach(range_px=55, chase_px=280, box=(400, 300))
    (c,) = problems
    assert "400" in c.detail and "561" in c.fix


def test_an_ult_that_needs_several_mobs_is_not_the_chase_yardstick():
    """多技能：大絕 300 要 3 隻才放，不會擋追擊（fsm 只看單隻怪就會放的招）。"""
    from maplebot.config import AppCfg, AttackCfg, Profile
    cfg = AppCfg()
    cfg.regions = {"playfield": [0, 0, 790, 400]}
    p = Profile()
    p.chase_px = 280
    p.skills = [AttackCfg(key="ctrl", range_px=55), AttackCfg(key="v", range_px=300, min_mobs=3)]
    assert [c.status for c in doctor.check_reach(cfg, p)] == [doctor.OK]


def test_keep_away_advice_is_in_its_own_units_when_scaling_is_off():
    """attack_auto_scale 關掉時 keep_away_px 是實際像素，建議值也要用同一個單位。"""
    from maplebot.config import AppCfg, Profile
    cfg = AppCfg()
    cfg.regions = {"playfield": [0, 0, 1580, 800]}     # 2 倍寬：排除範圍半寬 100px
    p = Profile()
    p.attack.range_px, p.keep_away_px, p.attack_auto_scale = 300, 90, False
    (c,) = [c for c in doctor.check_reach(cfg, p) if c.status == doctor.WARN]
    assert "半寬 100px" in c.detail and "至少設 102" in c.fix


def test_the_shipped_ranged_template_passes_the_reach_check():
    from maplebot.config import AppCfg, load_profile
    p = load_profile(str(Path(__file__).resolve().parents[1] / "config" / "profiles"
                         / "範本-遠程站樁（法師標賊）.yaml"))
    cfg = AppCfg()
    cfg.regions = {"playfield": [0, 0, 790, 400]}
    assert [c.status for c in doctor.check_reach(cfg, p)] == [doctor.OK]
