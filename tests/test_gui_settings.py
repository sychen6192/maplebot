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
