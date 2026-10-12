"""UI 欄位 <-> 設定檔的轉換。

「填了沒存到」「存了讀不回來」「按一次儲存把校正洗掉」是 GUI 最常見的三種
bug，這裡把它們全部釘死。
"""
import yaml

from maplebot.config import load_config, load_profile
from maplebot.gui import settings


def _write(path, data):
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return str(path)


BASE = {
    "window": {"title": "MapleSaga"},
    "regions": {"minimap": [1, 2, 30, 20], "hp_bar": [0, 0, 10, 5],
                "playfield": [0, 0, 100, 100]},
}
PROFILE = {
    "name": "test map",
    "patrol": {"waypoints": [30, 90]},
    "attack": {"key": "x"},
}


def test_defaults_cover_every_field():
    v = settings.defaults()
    assert set(v) == set(settings.FIELDS)


def test_coerce_turns_ui_strings_into_numbers():
    v = settings.coerce({"attack_range": "250", "attack_seconds": "0.4",
                         "pause_when_players": "false", "attack_key": " X "})
    assert v["attack_range"] == 250
    assert v["attack_seconds"] == 0.4
    assert v["pause_when_players"] is False
    assert v["attack_key"] == "X"


def test_coerce_falls_back_instead_of_raising():
    """使用者在數字欄位打了字，UI 不能整個炸掉。"""
    v = settings.coerce({"attack_range": "abc", "fps": ""})
    assert v["attack_range"] == settings.FIELDS["attack_range"][0]
    assert v["fps"] == settings.FIELDS["fps"][0]


def test_round_trip_through_files(tmp_path):
    base = _write(tmp_path / "default.yaml", BASE)
    prof = _write(tmp_path / "mymap.yaml", PROFILE)
    local_path = str(tmp_path / "local.yaml")

    values = settings.defaults()
    values.update(attack_key="v", attack_range=250, hp_key="s", hp_below=60,
                  waypoints="12, 44", critical_hp=30, window_title="MyWindow")
    local, prof_doc = settings.to_yaml(values, settings.buffs_from(
        [("8", "120"), ("9", "180"), ("", "")]), "test map")
    settings.merge_into(local_path, local)
    settings.merge_into(prof, prof_doc)

    cfg = load_config(base, local_path=local_path)
    profile = load_profile(prof)
    assert cfg.window_title == "MyWindow"
    assert cfg.safety.critical_hp_ratio == 0.3
    assert profile.attack.key == "v"
    assert profile.attack.range_px == 250
    assert profile.potions["hp"].below_ratio == 0.6
    assert [int(w.x) for w in profile.patrol.waypoints] == [12, 44]
    assert [b.key for b in profile.buffs] == ["8", "9"]

    back = settings.from_config(cfg, profile)
    assert back["attack_key"] == "v"
    assert back["hp_below"] == 60
    assert back["waypoints"] == "12, 44"
    assert settings.buff_rows(profile)[:2] == [("8", "120.0"), ("9", "180.0")]


def test_saving_keeps_the_calibrated_regions(tmp_path):
    """按一次儲存就要重新校正 = 最不可原諒的 bug。"""
    local_path = str(tmp_path / "local.yaml")
    _write(tmp_path / "local.yaml", {"regions": {"minimap": [9, 9, 9, 9]},
                                     "window": {"title": "Old"}})
    local, _ = settings.to_yaml(settings.defaults(), [], "m")
    merged = settings.merge_into(local_path, local)
    assert merged["regions"]["minimap"] == [9, 9, 9, 9]
    assert merged["window"]["title"] == settings.FIELDS["window_title"][0]


def test_waypoints_auto_survives_the_round_trip(tmp_path):
    prof = _write(tmp_path / "p.yaml", PROFILE)
    _, doc = settings.to_yaml({**settings.defaults(), "waypoints": "auto"}, [], "m")
    settings.merge_into(prof, doc)
    profile = load_profile(prof)
    assert profile.patrol.auto is True
    assert settings.waypoints_text(profile) == "auto"


def test_recorded_multilevel_route_survives_the_round_trip(tmp_path):
    prof = _write(tmp_path / "p.yaml", PROFILE)
    text = "[{x: 30, y: 40}, {x: 60, y: 20, keys: ['9']}]"
    _, doc = settings.to_yaml({**settings.defaults(), "waypoints": text}, [], "m")
    settings.merge_into(prof, doc)
    profile = load_profile(prof)
    assert [(int(w.x), int(w.y)) for w in profile.patrol.waypoints] == [(30, 40), (60, 20)]
    assert profile.patrol.waypoints[1].keys == ["9"]


def test_garbage_waypoints_fall_back_to_auto():
    assert settings.parse_waypoints("???") == "auto"
    assert settings.parse_waypoints("") == "auto"
    assert settings.parse_waypoints("30, 90") == [30, 90]


def test_empty_buff_rows_are_dropped():
    assert settings.buffs_from([("", "120"), ("8", ""), ("9", "0"), ("7", "60")]) == [
        {"key": "7", "every": 60.0, "cast_seconds": 1.2}]


def test_fields_without_a_widget_are_never_written(tmp_path):
    """畫面上沒有的欄位不能寫進檔案。

    這正是實際踩到的 bug：UI 少了「視窗標題」輸入框，但存檔照樣寫
    window.title，使用者按一次「儲存設定」就被改成預設值。
    """
    prof = _write(tmp_path / "p.yaml", PROFILE)
    local_path = str(tmp_path / "local.yaml")
    _write(tmp_path / "local.yaml", {"window": {"title": "我的視窗"}})

    partial = {"attack_key": "v"}          # UI 只提供了這一個欄位
    local, prof_doc = settings.to_yaml(partial, None, "m")
    assert "window" not in local
    assert "buffs" not in prof_doc
    settings.merge_into(local_path, local)
    settings.merge_into(prof, prof_doc)

    cfg = load_config(_write(tmp_path / "base.yaml", BASE), local_path=local_path)
    assert cfg.window_title == "我的視窗"          # 沒被動到
    assert load_profile(prof).attack.key == "v"    # 有給的照樣寫進去


def test_every_field_reaches_a_yaml_key():
    """FIELDS 裡的每個欄位都要有去處，不然填了等於沒填。"""
    mapped = {f for f, _ in settings.LOCAL_MAP.values()}
    mapped |= {f for f, _ in settings.PROFILE_MAP.values()}
    mapped |= {"hp_key", "hp_below", "mp_key", "mp_below", "panic_return_key"}
    assert set(settings.FIELDS) - mapped == set()


def test_attack_key_defaults_to_ctrl():
    assert settings.FIELDS["attack_key"][0] == "ctrl"


def test_max_runtime_survives_the_round_trip(tmp_path):
    base = _write(tmp_path / "default.yaml", BASE)
    prof_path = _write(tmp_path / "map.yaml", PROFILE)
    values = settings.defaults()
    values["max_runtime_minutes"] = 240
    local, prof = settings.to_yaml(values, None, "test map")
    settings.merge_into(str(tmp_path / "local.yaml"), local)
    settings.merge_into(prof_path, prof)

    cfg = load_config(base, local_path=str(tmp_path / "local.yaml"))
    assert cfg.safety.max_runtime_minutes == 240
    assert settings.from_config(cfg, load_profile(prof_path))["max_runtime_minutes"] == 240


def test_saving_does_not_wipe_the_monitor_and_report_sections(tmp_path):
    """monitor / report 沒有出現在 UI 上。UI 上沒有的東西按一次「儲存設定」
    就被清空的話，使用者根本不知道發生了什麼事。"""
    local_path = str(tmp_path / "local.yaml")
    settings.merge_into(local_path, {"monitor": {"interval": 20.0},
                                     "report": {"chart": False}})
    local, _ = settings.to_yaml(settings.defaults(), None, "m")
    merged = settings.merge_into(local_path, local)
    assert merged["monitor"]["interval"] == 20.0
    assert merged["report"]["chart"] is False


# ---------------------------------------------------------------------------
# 只寫改過的欄位、讀回寫出不失真、驗證、範本、保留註解
# ---------------------------------------------------------------------------

import os
import shutil
from pathlib import Path

import pytest

from maplebot.brain import fsm
from maplebot.brain.state import GameState
from maplebot.config import AppCfg, BuffCfg

PROFILES = Path(__file__).resolve().parent.parent / "config" / "profiles"
ROPE_MAP = PROFILES / "弓箭手訓練場一-1366x768.yaml"
EXAMPLE = PROFILES / "example.yaml"


def _copy(src, tmp_path, name="map.yaml"):
    dst = tmp_path / name
    shutil.copyfile(src, dst)
    return str(dst)


def test_normalize_treats_equivalent_values_as_equal():
    assert settings.normalize("attack_seconds", "0.60") == \
        settings.normalize("attack_seconds", 0.6)
    assert settings.normalize("exp_stall_minutes", 10.0) == \
        settings.normalize("exp_stall_minutes", "10")
    assert settings.normalize("attack_key", " Ctrl ") == "ctrl"
    assert settings.normalize("pause_when_players", True) == "true"
    # 轉不動的保留原字串：算成「有改」，交給 validate 擋
    assert settings.normalize("attack_range", "abc") == "abc"


def test_display_round_trips_through_normalize():
    """填回欄位的字串一比對就變「已修改」的話，dirty 標記永遠消不掉。"""
    for field, value in settings.defaults().items():
        if settings.SPECS[field].kind in ("bool", "choice"):
            continue                       # 勾選框與下拉選單不是用文字填回去的
        assert settings.normalize(field, settings.display(field, value)) == \
            settings.normalize(field, value)
    assert settings.display("attack_range", 320.0) == "320"
    assert settings.display("attack_seconds", 0.6) == "0.6"
    assert settings.display("attack_type", "aoe") == "原地範圍（不用轉身）"


def test_changed_fields_only_reports_real_edits():
    base = {f: settings.normalize(f, v) for f, v in settings.defaults().items()}
    values = settings.defaults()
    values.update(attack_range="320", attack_seconds="0.60", sound_alerts=False,
                  attack_key="x")
    assert settings.changed_fields(base, values) == ["attack_key", "sound_alerts"]
    assert settings.changed_fields(base, settings.defaults()) == []


def test_buff_rows_changed_pads_both_sides():
    assert not settings.buff_rows_changed([("8", "120.0")],
                                          [("8", "120")] + [("", "")] * 7)
    assert settings.buff_rows_changed([("8", "120")], [("8", "60")])
    assert settings.buff_rows_changed([], [("", ""), ("9", "30")])


def test_with_coupled_adds_the_other_half_of_a_potion():
    assert settings.with_coupled(["hp_below", "attack_key"]) == \
        ["hp_below", "hp_key", "attack_key"]


def test_waypoints_keep_every_field_through_the_round_trip(tmp_path):
    """1366x768 那條 x=54 的繩（tolerance 1、grab: run）以前存一次就沒了；
    比例座標 0.25 被 int() 成 0。"""
    prof = _write(tmp_path / "p.yaml", {**PROFILE, "patrol": {"waypoints": [
        {"x": 0.25, "y": 40},
        {"x": 54, "y": 56, "tolerance": 1, "grab": "run", "run_up": 3},
        {"x": 102, "y": 90, "descend": "jump", "keys": ["9", "alt"]},
    ]}})
    before = load_profile(prof)
    text = settings.waypoints_text(before)
    assert text.count("\n") == 2                       # 一行一個點
    assert settings.parse_waypoints_strict(text)[1] == ""

    _, doc = settings.to_yaml({"waypoints": text}, None, "m")
    settings.merge_into(prof, doc)
    after = load_profile(prof)
    assert after.patrol.waypoints == before.patrol.waypoints
    assert after.patrol.waypoints[0].x == 0.25


def test_single_level_route_still_renders_as_one_line(tmp_path):
    prof = _write(tmp_path / "p.yaml", {**PROFILE, "patrol": {"waypoints": [12, 44]}})
    assert settings.waypoints_text(load_profile(prof)) == "12, 44"


@pytest.mark.parametrize("src", [ROPE_MAP, EXAMPLE], ids=["1366x768", "example"])
def test_saving_every_field_of_a_repo_profile_is_lossless(tmp_path, src):
    path = _copy(src, tmp_path)
    before = load_profile(path)
    values = settings.from_config(AppCfg(), before)
    _, doc = settings.to_yaml(values, settings.buffs_from(
        settings.buff_rows(before), before.buffs), before.name)
    settings.merge_into(path, doc)
    after = load_profile(path)
    assert after.patrol == before.patrol
    assert after.buffs == before.buffs
    assert after.potions == before.potions
    assert after.attack == before.attack
    assert after.panic_return_key == before.panic_return_key


def test_rope_waypoint_survives_a_full_save(tmp_path):
    path = _copy(ROPE_MAP, tmp_path)
    profile = load_profile(path)
    _, doc = settings.to_yaml(settings.from_config(AppCfg(), profile), None, profile.name)
    settings.merge_into(path, doc)
    rope = [w for w in load_profile(path).patrol.waypoints if w.x == 54][0]
    assert (rope.grab, rope.tolerance) == ("run", 1)


def test_parse_waypoints_accepts_spaces_and_ratios():
    assert settings.parse_waypoints("30 90") == [30, 90]
    assert settings.parse_waypoints("30，90") == [30, 90]
    assert settings.parse_waypoints("0.25, 0.75") == [0.25, 0.75]
    assert settings.parse_waypoints("???") == "auto"


def test_strict_waypoints_use_the_loaders_message():
    value, err = settings.parse_waypoints_strict("[{y: 3}]")
    assert value is None and "少了 x" in err
    assert settings.parse_waypoints_strict("auto") == ("auto", "")
    assert settings.parse_waypoints_strict("abc")[1].startswith("看不懂「abc」")
    assert settings.parse_waypoints_strict("[]")[1]
    assert settings.parse_waypoints_strict("- {x: 5, tolerance: abc}")[1]


def test_describe_waypoints():
    auto = settings.describe_waypoints("auto")
    assert auto.level == "ok" and "探邊" in auto.text
    assert "2 個巡邏點" in settings.describe_waypoints("30, 90").text
    one = settings.describe_waypoints("30")
    assert one.level == "warn" and "1 個巡邏點" in one.text
    # 會捲動的小地圖上巡邏點本來就能超出小地圖寬度（issue #7），不能警告
    assert settings.describe_waypoints("300, 20").level == "ok"
    assert settings.describe_waypoints("0.25, 0.75").level == "info"
    assert settings.describe_waypoints("abc").level == "error"
    levels = settings.describe_waypoints("- {x: 1, y: 56}\n- {x: 9, y: 72}\n- {x: 9, y: 90}")
    assert "3 層" in levels.text


def test_buffs_keep_cast_seconds_and_min_mp():
    existing = [BuffCfg(key="8", every=120, cast_seconds=1.5, min_mp=0.2)]
    existing += [BuffCfg(key=str(i), every=60) for i in range(9)]   # 共 10 組
    rows = [("8", "90")] + [("", "")] * 7
    out = settings.buffs_from(rows, existing)
    assert out[0] == {"key": "8", "every": 90.0, "cast_seconds": 1.5, "min_mp": 0.2}
    # UI 只有 8 格，第 9 組以後原封不動接在後面
    assert [b["key"] for b in out[1:]] == ["7", "8"]
    assert out[1] == {"key": "7", "every": 60, "cast_seconds": 1.2, "min_mp": 0.0}
    assert settings.buffs_from([("7", "60")]) == [
        {"key": "7", "every": 60.0, "cast_seconds": 1.2}]


def test_example_buff_keeps_its_cast_time(tmp_path):
    path = _copy(EXAMPLE, tmp_path)
    profile = load_profile(path)
    rows = settings.buff_rows(profile)
    rows[0] = ("8", "100")
    _, doc = settings.to_yaml({}, settings.buffs_from(rows, profile.buffs), profile.name)
    settings.merge_into(path, doc)
    assert load_profile(path).buffs[0].cast_seconds == 1.5


def test_cleared_potion_key_and_panic_key_are_written(tmp_path):
    prof = _write(tmp_path / "p.yaml", {**PROFILE, "panic_return_key": "home",
                                        "potions": {"hp": {"key": "s", "below_ratio": 0.5}}})
    _, doc = settings.to_yaml({"hp_key": "", "hp_below": 50, "panic_return_key": ""},
                              None, "m")
    assert doc["potions"]["hp"] == {"key": "", "below_ratio": 0.0}
    assert doc["panic_return_key"] == ""
    settings.merge_into(prof, doc)
    profile = load_profile(prof)
    assert profile.panic_return_key == ""
    assert settings.from_config(AppCfg(), profile)["hp_key"] == ""

    # fsm 只看 below_ratio 決定喝不喝：比例留著的話 bot 會一直按空鍵
    rt = fsm.Runtime()
    for t in range(20):
        state = GameState(ts=1000.0 + t, hp=0.1, mp=0.9, minimap_xy=(60, 30),
                          minimap_size=(130, 60))
        action = fsm.decide(state, AppCfg(), profile, rt, 1000.0 + t, (400, 260))
        assert not isinstance(action, fsm.DrinkPotion)


def test_changing_only_the_threshold_keeps_the_potion_key(tmp_path):
    prof = _write(tmp_path / "p.yaml", {**PROFILE, "potions": {
        "hp": {"key": "s", "below_ratio": 0.5, "cooldown": 0.7}}})
    _, doc = settings.to_yaml({"hp_below": 60}, None, "m")
    settings.merge_into(prof, doc)
    hp = load_profile(prof).potions["hp"]
    assert (hp.key, hp.below_ratio, hp.cooldown) == ("s", 0.6, 0.7)


def _ok_values(**kw):
    v = settings.defaults()
    v.update(kw)
    return v


EMPTY_ROWS = [("", "")] * settings.BUFF_SLOTS


def test_validate_reports_bad_numbers():
    p = settings.validate(_ok_values(attack_range="abc"), EMPTY_ROWS)
    assert p["attack_range"].level == "error"
    assert "abc" in p["attack_range"].text and "10–2000" in p["attack_range"].text
    assert "整數" in settings.validate(_ok_values(attack_repeat="0.5"),
                                      EMPTY_ROWS)["attack_repeat"].text
    assert "attack_seconds" in settings.validate(_ok_values(attack_seconds="nan"),
                                                 EMPTY_ROWS)


def test_validate_potion_line_must_be_above_the_panic_line():
    p = settings.validate(_ok_values(hp_key="s", hp_below=20, critical_hp=25), EMPTY_ROWS)
    assert p["hp_below"].level == "error" and "停機線" in p["hp_below"].text
    assert p["critical_hp"].level == "error"
    # 沒綁藥水鍵：門檻用不到，不檢查
    assert settings.validate(_ok_values(hp_key="", hp_below=0), EMPTY_ROWS) == {}


def test_validate_buff_rows_and_required_keys():
    rows = [("8", "x"), ("", "60"), ("9", "3")] + [("", "")] * 5
    p = settings.validate(_ok_values(), rows)
    assert p["buff1"].level == "error"
    assert p["buff2"].level == "warn"
    assert p["buff3"].level == "warn"
    assert settings.validate(_ok_values(attack_key=""), EMPTY_ROWS)["attack_key"].level == "error"


@pytest.mark.parametrize("wp", ["auto", "40, 95",
                                "- {x: 74, y: 72, tolerance: 1}\n- {x: 54, y: 56, grab: run}"])
def test_validate_accepts_the_defaults(wp):
    assert settings.validate(_ok_values(waypoints=wp), EMPTY_ROWS) == {}


def test_validate_rejects_waypoints_the_loader_would_reject():
    p = settings.validate(_ok_values(waypoints="[{y: 3}]"), EMPTY_ROWS)
    assert p["waypoints"].level == "error" and "少了 x" in p["waypoints"].text


def test_untouched_out_of_range_values_only_warn():
    """repo 裡的舊地圖可能本來就超出新範圍；沒改過的值不能讓它開不了。"""
    values = _ok_values(attack_range=5000)
    assert settings.validate(values, EMPTY_ROWS, changed=[])["attack_range"].level == "warn"
    assert settings.validate(values, EMPTY_ROWS,
                             changed=["attack_range"])["attack_range"].level == "error"


def test_binding_a_potion_key_checks_its_threshold_strictly():
    """改了藥水鍵、門檻還是 0：存進去的話這瓶藥永遠不會喝。"""
    p = settings.validate(_ok_values(hp_key="s", hp_below=0), EMPTY_ROWS,
                          changed=["hp_key"])
    assert p["hp_below"].level == "error"


def test_key_conflicts():
    rows = [("f12", "60")] + [("", "")] * 7
    p = settings.validate(_ok_values(), rows)
    assert p["buff1"].level == "error" and "停止熱鍵" in p["buff1"].text
    p = settings.validate(_ok_values(attack_key="f9"), EMPTY_ROWS)
    assert p["attack_key"].level == "error" and "暫停熱鍵" in p["attack_key"].text
    rows = [("z", "60")] + [("", "")] * 7
    p = settings.validate(_ok_values(loot_key="z"), rows)
    assert p["loot_key"].level == "warn" and p["buff1"].level == "warn"
    # 熱鍵改過的話照設定檔的熱鍵檢查
    p = settings.key_conflicts(_ok_values(attack_key="f9"), EMPTY_ROWS,
                               {"pause": "暫停", "f8": "停止"})
    assert p == {}


def test_first_error_follows_the_form_order():
    problems = {"buff2": settings.Problem("error", "x"),
                "attack_range": settings.Problem("error", "y"),
                "attack_key": settings.Problem("warn", "z")}
    assert settings.first_error(problems) == "attack_range"
    assert settings.first_error({"attack_key": settings.Problem("warn", "z")}) is None


def test_specs_and_groups_cover_every_field_once():
    assert set(settings.SPECS) == set(settings.FIELDS)
    listed = [f for _tab, _group, fields in settings.GROUPS for f in fields]
    assert sorted(listed) == sorted(settings.FIELDS)
    for _tab, _group, fields in settings.GROUPS:
        assert len({settings.scope(f) for f in fields}) == 1
    local = {f for f, _ in settings.LOCAL_MAP.values()}
    for f in settings.FIELDS:
        assert (settings.scope(f) == "config") == (f in local)
    assert settings.PROFILE_FIELDS == (set(settings.FIELDS) - local) | {"buffs"}
    for f, spec in settings.SPECS.items():
        assert spec.label and spec.help
        if spec.kind in ("int", "float"):
            assert spec.lo is not None and spec.hi is not None
    assert settings.tab_of("buff3") == "巡邏與 buff"
    assert settings.tab_of("window_title") == "準備"


def test_range_help_tells_users_not_to_rescale_by_hand():
    """ADR 0003：1366 寬的視窗填 320 是對的，不要讓使用者自己「修正」它。"""
    assert "790" in settings.SPECS["attack_range"].help
    tip = settings.tooltip_text("attack_range", 320,
                                settings.Problem("error", "水平距離要填"))
    assert "範圍 10–2000 px" in tip and "檔案裡是 320" in tip and "水平距離要填" in tip
    assert "檔案裡是空的" in settings.tooltip_text("loot_key", "")


def test_settings_copy_renders_in_tk():
    """Tcl 8.6 只吃 BMP，U+FE0F 會變成方框。"""
    texts = [s.label + s.unit + s.help for s in settings.SPECS.values()]
    texts += [label for opts in settings.CHOICES.values() for _v, label in opts]
    texts += list(settings.GLYPHS.values()) + [settings.BUFF_HELP]
    for t in texts:
        assert all(ord(c) <= 0xFFFF and c != "️" for c in t), t


@pytest.mark.parametrize("path", [
    "example.yaml", "config/profiles/multilevel.yaml",
    "config\\profiles\\範本-近戰（戰士刀賊）.yaml",
    str(PROFILES / "範本-近戰（戰士刀賊）.yaml")])
def test_templates_are_recognised(path):
    assert settings.is_template(path)


def test_user_maps_are_not_templates():
    assert not settings.is_template(str(PROFILES / "弓箭手訓練場一.yaml"))


def test_profile_filename(tmp_path):
    folder = str(tmp_path)
    assert settings.profile_filename("弓箭手 訓練場/一?", folder) == \
        os.path.join(folder, "弓箭手-訓練場-一.yaml")
    taken = {os.path.join(folder, "我的地圖.yaml"), os.path.join(folder, "我的地圖-2.yaml")}
    assert settings.profile_filename("我的地圖", folder, exists=taken.__contains__) == \
        os.path.join(folder, "我的地圖-3.yaml")
    assert settings.profile_filename("我的地圖", folder,
                                     exists=lambda p: False).endswith("我的地圖.yaml")
    with pytest.raises(ValueError):
        settings.profile_filename("  ", folder)
    with pytest.raises(ValueError):
        settings.profile_filename("範本-偷改", folder)


def test_profile_choices_list_user_maps_before_templates(tmp_path):
    _write(tmp_path / "example.yaml", {"name": "範例"})
    _write(tmp_path / "b-map.yaml", {"name": "B 地圖"})
    (tmp_path / "a-broken.yaml").write_text("name: [", encoding="utf-8")
    (tmp_path / "notes.md").write_text("x", encoding="utf-8")
    choices = settings.profile_choices(str(tmp_path))
    assert [label for label, _ in choices] == ["a-broken", "B 地圖", "範例（範本）"]
    assert choices[1][1] == os.path.join(str(tmp_path), "b-map.yaml")


def test_profile_choices_disambiguate_same_names(tmp_path):
    _write(tmp_path / "one.yaml", {"name": "同名"})
    _write(tmp_path / "two.yaml", {"name": "同名"})
    labels = [label for label, _ in settings.profile_choices(str(tmp_path))]
    assert len(set(labels)) == 2


def test_patch_scalars_keeps_every_comment(tmp_path):
    text = ROPE_MAP.read_text(encoding="utf-8")
    patch = {"name": "弓箭手訓練場一（1366x768）", "attack": {"range_px": 250},
             "potions": {"hp": {"key": "s", "below_ratio": 0.6}}}
    out = settings.patch_scalars(text, patch)
    assert out is not None
    comments = [ln for ln in text.splitlines() if "#" in ln]
    assert [ln for ln in out.splitlines() if "#" in ln] == comments
    assert yaml.safe_load(out) == settings._deep_merge(yaml.safe_load(text), patch)
    changed = [(a, b) for a, b in zip(text.splitlines(), out.splitlines()) if a != b]
    assert len(changed) == 2


def test_patch_scalars_keeps_trailing_comments_in_place():
    text = "attack:\n  range_px: 320             # 水平範圍\n  key: ctrl\n"
    out = settings.patch_scalars(text, {"attack": {"range_px": 50, "key": ""}})
    assert out == "attack:\n  range_px: 50              # 水平範圍\n  key: ''\n"


def test_patch_scalars_gives_up_when_it_cannot_be_exact():
    example = EXAMPLE.read_text(encoding="utf-8")
    # potions 是 flow 風格：{ key: pageup, below_ratio: 0.50, ... }
    assert settings.patch_scalars(example, {"potions": {"hp": {"below_ratio": 0.6}}}) is None
    # 檔案裡沒有的 key
    assert settings.patch_scalars(example, {"attack": {"no_such_key": 1}}) is None
    assert settings.patch_scalars("a: [", {"a": 1}) is None


def test_write_patch_edits_in_place_or_backs_up_once(tmp_path):
    path = _copy(EXAMPLE, tmp_path)
    original = Path(path).read_text(encoding="utf-8")
    seen = set()
    assert settings.write_patch(path, {"attack": {"range_px": 250}}, seen) is None
    assert Path(path).read_text(encoding="utf-8").count("#") == original.count("#")
    assert not os.path.exists(path + ".bak")

    # 巡邏點是同一行的 [40, 95]：換掉不會少任何註解，所以也不用備份
    assert settings.write_patch(path, {"patrol": {"waypoints": [1, 2]}}, seen) is None
    assert load_profile(path).patrol.waypoints[1].x == 2
    assert Path(path).read_text(encoding="utf-8").count("#") == original.count("#")

    # buff 區塊裡有註解：換掉會少註解 -> 先備份（備份的是改之前、還有註解的版本）
    bak = settings.write_patch(path, {"buffs": [{"key": "8", "every": 60.0}]}, seen)
    assert bak == path + ".bak"
    assert "range_px: 250" in Path(bak).read_text(encoding="utf-8")
    assert load_profile(path).buffs[0].every == 60.0
    backed_up = Path(bak).read_text(encoding="utf-8")

    # 區塊裡已經沒註解了，再換一次不會少任何東西，不用再報備份
    assert settings.write_patch(path, {"buffs": [{"key": "9", "every": 30.0}]}, seen) is None

    # 下一個 session（新的 seen）退回整份重寫（potions 是 flow 風格）：
    # 照樣報出備份在哪，但 .bak 不能被蓋成已經沒註解的版本
    again = settings.write_patch(path, {"potions": {"hp": {"below_ratio": 0.6}}}, set())
    assert again == path + ".bak"
    assert Path(bak).read_text(encoding="utf-8") == backed_up


def test_a_list_edit_only_replaces_that_block(tmp_path):
    """錄一次路線不能把檔案其他地方記錄實測數據的註解洗掉。"""
    src = PROFILES / "弓箭手訓練場一-1366x768.yaml"
    path = _copy(src, tmp_path)
    text = Path(path).read_text(encoding="utf-8")
    route = [{"x": 10, "y": 90}, {"x": 80, "y": 90, "tolerance": 1}]
    new = settings.patch_scalars(text, {"patrol": {"waypoints": route}})
    assert new is not None
    outside = [ln for ln in text.splitlines() if ln.lstrip().startswith("#")
               and "y_tolerance 2" in ln]
    assert outside and all(ln in new for ln in outside)
    assert yaml.safe_load(new)["patrol"]["waypoints"] == route
    assert yaml.safe_load(new)["patrol"]["tolerance"] == yaml.safe_load(text)["patrol"]["tolerance"]
    # 清單換回 auto 也是只動那一塊
    auto = settings.patch_scalars(text, {"patrol": {"waypoints": "auto"}})
    assert yaml.safe_load(auto)["patrol"]["waypoints"] == "auto"
    assert all(ln in auto for ln in outside)


def test_write_patch_with_nothing_to_write_does_not_create_the_file(tmp_path):
    local = str(tmp_path / "local.yaml")
    settings.write_patch(local, {}, set())
    assert not os.path.exists(local)
    settings.write_patch(local, {"loop": {"fps": 10.0}}, set())
    assert yaml.safe_load(Path(local).read_text(encoding="utf-8")) == {"loop": {"fps": 10.0}}


def test_write_patch_keeps_crlf_line_endings(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_bytes(b"attack:\r\n  range_px: 320  # x\r\n")
    settings.write_patch(str(path), {"attack": {"range_px": 50}}, set())
    assert path.read_bytes() == b"attack:\r\n  range_px: 50   # x\r\n"


def test_a_key_name_the_game_cannot_receive_is_an_error():
    """打錯字的鍵名（或擷取被打斷留下的提示字）存進去，正式跑第一下就 KeyError。"""
    values = dict(settings.defaults(), attack_key="按一個鍵…", loot_key="ctlr")
    problems = settings.validate(values, [("f13", "60")] + [("", "")] * 7, changed=[])
    assert problems["attack_key"].level == "error"
    assert "ctlr" in problems["loot_key"].text
    assert problems["buff1"].level == "error"
    assert not settings.validate(settings.defaults(), [("8", "120")] + [("", "")] * 7)
