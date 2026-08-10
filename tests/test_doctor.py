"""環境自檢：每一項檢查的判定，以及「訊息有沒有講到怎麼修」。

自檢工具的價值全在訊息上——`❌ 找不到模型` 沒有用，
`❌ 找不到模型 → 改回 outline 或看 docs/YOLO_TRAINING.md` 才有用。
所以這裡不只斷言狀態，也斷言 fix 欄位有指向具體的動作。
"""
import yaml

from maplebot import doctor
from maplebot.config import load_config, load_profile


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
