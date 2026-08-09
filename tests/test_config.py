"""設定載入：local.yaml 覆寫、waypoint 解析、attack.type 驗證。"""
import pytest

from maplebot.config import ConfigError, load_config, load_profile


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return str(path)


BASE_CFG = """
window: { title: "MapleSaga" }
regions:
  minimap: [1, 2, 30, 20]
  playfield: [0, 0, 100, 100]
safety: { critical_hp_ratio: 0.25 }
"""


def test_local_override_merges_deeply(tmp_path):
    base = _write(tmp_path / "default.yaml", BASE_CFG)
    local = _write(tmp_path / "local.yaml", """
window: { title: "MyWindow" }
safety: { critical_hp_ratio: 0.4 }
""")
    cfg = load_config(base, local_path=local)
    assert cfg.window_title == "MyWindow"
    assert cfg.safety.critical_hp_ratio == 0.4
    assert cfg.regions["minimap"] == (1, 2, 30, 20)  # 沒覆寫的保留


def test_local_yaml_found_next_to_config_not_cwd(tmp_path, monkeypatch):
    """從別的目錄執行時也要讀得到個人設定——用 CWD 相對路徑會靜靜地漏掉。"""
    cfgdir = tmp_path / "config"
    cfgdir.mkdir()
    _write(cfgdir / "default.yaml", BASE_CFG)
    _write(cfgdir / "local.yaml", 'window: { title: "FromLocal" }\n')

    elsewhere = tmp_path / "somewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    cfg = load_config(str(cfgdir / "default.yaml"))
    assert cfg.window_title == "FromLocal"
    assert len(cfg.sources) == 2


def test_sources_records_what_was_loaded(tmp_path):
    base = _write(tmp_path / "default.yaml", BASE_CFG)
    cfg = load_config(base, local_path="")
    assert cfg.sources == [base]          # 沒有覆寫檔時看得出來


def test_minimap_auto(tmp_path):
    base = _write(tmp_path / "default.yaml", """
regions:
  minimap: auto
  playfield: [0, 0, 100, 100]
""")
    cfg = load_config(base, local_path="")
    assert cfg.minimap_auto is True
    assert "minimap" not in cfg.regions


def test_profile_waypoint_formats(tmp_path):
    path = _write(tmp_path / "p.yaml", """
patrol:
  waypoints:
    - 40
    - 0.75
    - { x: 95, keys: [ALT, x] }
""")
    p = load_profile(path)
    wps = p.patrol.waypoints
    assert wps[0].x == 40 and wps[0].keys == []
    assert wps[1].x == 0.75
    assert wps[2].x == 95 and wps[2].keys == ["alt", "x"]


def test_profile_legacy_waypoints_x_still_works(tmp_path):
    path = _write(tmp_path / "p.yaml", "patrol: { waypoints_x: [10, 20] }\n")
    p = load_profile(path)
    assert [w.x for w in p.patrol.waypoints] == [10, 20]


def test_profile_bad_attack_type(tmp_path):
    path = _write(tmp_path / "p.yaml", """
patrol: { waypoints: [10] }
attack: { type: melee }
""")
    with pytest.raises(ConfigError):
        load_profile(path)


def test_profile_requires_waypoints(tmp_path):
    path = _write(tmp_path / "p.yaml", "name: x\n")
    with pytest.raises(ConfigError):
        load_profile(path)


def test_profile_skills_list(tmp_path):
    path = _write(tmp_path / "p.yaml", """
patrol: { waypoints: [10] }
skills:
  - { key: V, type: aoe, cooldown: 30, min_mobs: 3, min_mp: 0.2, range_px: 400 }
  - { key: X, cooldown: 0.2 }
""")
    p = load_profile(path)
    assert [s.key for s in p.skills] == ["v", "x"]
    assert p.skills[0].type == "aoe" and p.skills[0].min_mobs == 3
    assert p.skills[1].min_mobs == 1          # 預設值
    assert p.active_skills() is p.skills


def test_profile_without_skills_falls_back_to_attack(tmp_path):
    path = _write(tmp_path / "p.yaml",
                  "patrol: { waypoints: [10] }\nattack: { key: c }\n")
    p = load_profile(path)
    assert p.skills == []
    assert [s.key for s in p.active_skills()] == ["c"]


def test_profile_skill_requires_key(tmp_path):
    path = _write(tmp_path / "p.yaml",
                  "patrol: { waypoints: [10] }\nskills: [{ cooldown: 5, key: '' }]\n")
    with pytest.raises(ConfigError):
        load_profile(path)


def test_profile_skill_bad_type(tmp_path):
    path = _write(tmp_path / "p.yaml",
                  "patrol: { waypoints: [10] }\nskills: [{ key: v, type: beam }]\n")
    with pytest.raises(ConfigError):
        load_profile(path)


def test_profile_waypoints_auto(tmp_path):
    path = _write(tmp_path / "p.yaml", "patrol: { waypoints: auto }\n")
    p = load_profile(path)
    assert p.patrol.auto is True
    assert p.patrol.waypoints == []   # 開場自己量，不用先寫


def test_profile_waypoints_bad_string(tmp_path):
    path = _write(tmp_path / "p.yaml", "patrol: { waypoints: everywhere }\n")
    with pytest.raises(ConfigError):
        load_profile(path)


def test_profile_waypoint_y_and_descend(tmp_path):
    path = _write(tmp_path / "p.yaml", """
patrol:
  waypoints:
    - { x: 88, y: 0.35 }
    - { x: 60, y: 44, descend: JUMP }
    - 40
""")
    wps = load_profile(path).patrol.waypoints
    assert wps[0].y == 0.35 and wps[0].descend == "rope"
    assert wps[1].y == 44 and wps[1].descend == "jump"
    assert wps[2].y is None            # 單層寫法不受影響


def test_profile_bad_descend(tmp_path):
    path = _write(tmp_path / "p.yaml",
                  "patrol: { waypoints: [{ x: 10, descend: teleport }] }\n")
    with pytest.raises(ConfigError):
        load_profile(path)


def test_profile_climb_and_probe_knobs(tmp_path):
    path = _write(tmp_path / "p.yaml", """
patrol:
  waypoints: auto
  probe_margin_px: 10
  climb_retries: 5
  climb_up_key: UP
""")
    pt = load_profile(path).patrol
    assert pt.probe_margin_px == 10
    assert pt.climb_retries == 5
    assert pt.climb_up_key == "up"


def test_follower_and_attack_stall_knobs_load(tmp_path):
    base = _write(tmp_path / "default.yaml", BASE_CFG + """
vision:
  filter_followers: true
  follower_min_shift_px: 200
  follower_tol_px: 30
  follower_hits: 5
  player_move_px: 3
safety:
  attack_stall_seconds: 20
  attack_break_seconds: 1.5
""")
    cfg = load_config(base)
    assert cfg.vision.filter_followers is True
    assert cfg.vision.follower_min_shift_px == 200
    assert cfg.vision.follower_tol_px == 30
    assert cfg.vision.follower_hits == 5
    assert cfg.vision.player_move_px == 3
    assert cfg.safety.attack_stall_seconds == 20
    assert cfg.safety.attack_break_seconds == 1.5


def test_shipped_default_yaml_has_no_dead_keys():
    """config/default.yaml 裡每個鍵都要真的還在用。

    真實踩過的坑：欄位改名後 default.yaml 沒跟著改，舊鍵被靜靜忽略，
    而同一段裡寫死的 filter_followers: true 反而蓋掉了程式的新預設值——
    「我明明把預設關掉了」跟使用者實際跑到的行為對不起來。
    """
    import dataclasses
    import yaml as _yaml

    from maplebot.config import AppCfg, SafetyCfg, VisionCfg

    with open("config/default.yaml", encoding="utf-8") as f:
        doc = _yaml.safe_load(f)

    aliases = {"bars": "bar_colors"}
    sections = {
        "vision": VisionCfg,
        "safety": SafetyCfg,
        "window": None,      # window 的鍵名跟欄位名不一樣，另外列
    }
    for section, cls in sections.items():
        if cls is None:
            continue
        known = {f.name for f in dataclasses.fields(cls)}
        for key in (doc.get(section) or {}):
            assert aliases.get(key, key) in known, \
                f"config/default.yaml 的 {section}.{key} 已經沒有對應的設定欄位了"

    assert set(doc.get("window") or {}) <= {"title", "capture", "calibrated_for"}
    assert set(doc.get("loop") or {}) <= {"fps"}
    assert set(doc.get("advisor") or {}) <= {
        f.name for f in dataclasses.fields(AppCfg().advisor.__class__)}


def test_shipped_default_yaml_agrees_with_the_code_default():
    """會影響行為的開關，YAML 跟程式預設值不能各說各話。"""
    from maplebot.config import VisionCfg
    cfg = load_config("config/default.yaml", local_path="")
    assert cfg.vision.filter_followers is VisionCfg().filter_followers is False
