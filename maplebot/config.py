"""載入與驗證 YAML 設定（全域 config + 地圖 profile）。"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import yaml

Region = Tuple[int, int, int, int]  # x, y, w, h（遊戲視窗 client 區座標）


class ConfigError(Exception):
    pass


def _region(raw, name: str) -> Region:
    if not (isinstance(raw, (list, tuple)) and len(raw) == 4):
        raise ConfigError(f"regions.{name} 必須是 [x, y, w, h]，拿到: {raw!r}")
    x, y, w, h = (int(v) for v in raw)
    if w <= 0 or h <= 0:
        raise ConfigError(f"regions.{name} 的寬高必須為正: {raw!r}")
    return (x, y, w, h)


@dataclass
class VisionCfg:
    minimap_player_rgb: Tuple[int, int, int] = (255, 255, 0)
    minimap_other_rgb: Tuple[int, int, int] = (255, 0, 0)
    color_tolerance: int = 60
    min_dot_pixels: int = 2
    bar_colors: Dict[str, str] = field(default_factory=lambda: {"hp": "red", "mp": "blue", "exp": "yellow"})
    mob_detector: str = "template"  # template | yolo
    mob_match_threshold: float = 0.72
    yolo_model: str = ""
    yolo_confidence: float = 0.5


@dataclass
class SafetyCfg:
    stop_key: str = "f12"
    pause_key: str = "f9"
    critical_hp_ratio: float = 0.25
    pause_when_players: bool = True
    lost_player_timeout: float = 5.0


@dataclass
class AdvisorCfg:
    enabled: bool = False
    endpoint: str = "http://127.0.0.1:8000/v1/chat/completions"
    model: str = "Qwen/Qwen2.5-VL-7B-Instruct"
    interval: float = 20.0
    timeout: float = 15.0


@dataclass
class AppCfg:
    window_title: str = "MapleStory"
    fps: float = 8.0
    regions: Dict[str, Region] = field(default_factory=dict)
    vision: VisionCfg = field(default_factory=VisionCfg)
    safety: SafetyCfg = field(default_factory=SafetyCfg)
    advisor: AdvisorCfg = field(default_factory=AdvisorCfg)

    def region(self, name: str) -> Region:
        if name not in self.regions:
            raise ConfigError(f"config 缺少 regions.{name}，請先執行 tools/calibrate.py 校正")
        return self.regions[name]


@dataclass
class PatrolCfg:
    waypoints_x: List[int] = field(default_factory=list)
    tolerance: int = 4
    step_seconds_per_px: float = 0.02
    max_step_seconds: float = 0.45


@dataclass
class AttackCfg:
    key: str = "x"
    range_px: int = 320
    vertical_range_px: int = 90
    cast_seconds: float = 0.6
    repeat: int = 1
    cooldown: float = 0.2


@dataclass
class BuffCfg:
    key: str = ""
    every: float = 120.0
    cast_seconds: float = 1.2


@dataclass
class PotionCfg:
    key: str = ""
    below_ratio: float = 0.0
    cooldown: float = 1.0


@dataclass
class Profile:
    name: str = "unnamed"
    templates_dir: str = "data/templates/mobs"
    patrol: PatrolCfg = field(default_factory=PatrolCfg)
    attack: AttackCfg = field(default_factory=AttackCfg)
    buffs: List[BuffCfg] = field(default_factory=list)
    potions: Dict[str, PotionCfg] = field(default_factory=dict)


def _load_yaml(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise ConfigError(f"找不到設定檔: {path}")
    except yaml.YAMLError as e:
        raise ConfigError(f"設定檔 {path} 格式錯誤: {e}")
    if not isinstance(data, dict):
        raise ConfigError(f"設定檔 {path} 的頂層必須是 mapping")
    return data


def load_config(path: str) -> AppCfg:
    data = _load_yaml(path)
    cfg = AppCfg()

    win = data.get("window", {})
    cfg.window_title = str(win.get("title", cfg.window_title))
    cfg.fps = float(data.get("loop", {}).get("fps", cfg.fps))

    for name, raw in (data.get("regions") or {}).items():
        cfg.regions[name] = _region(raw, name)

    v = data.get("vision", {})
    vc = cfg.vision
    vc.minimap_player_rgb = tuple(v.get("minimap_player_rgb", vc.minimap_player_rgb))  # type: ignore
    vc.minimap_other_rgb = tuple(v.get("minimap_other_rgb", vc.minimap_other_rgb))  # type: ignore
    vc.color_tolerance = int(v.get("color_tolerance", vc.color_tolerance))
    vc.min_dot_pixels = int(v.get("min_dot_pixels", vc.min_dot_pixels))
    vc.bar_colors.update(v.get("bars", {}))
    vc.mob_detector = str(v.get("mob_detector", vc.mob_detector))
    vc.mob_match_threshold = float(v.get("mob_match_threshold", vc.mob_match_threshold))
    vc.yolo_model = str(v.get("yolo_model", vc.yolo_model))
    vc.yolo_confidence = float(v.get("yolo_confidence", vc.yolo_confidence))

    s = data.get("safety", {})
    sc = cfg.safety
    sc.stop_key = str(s.get("stop_key", sc.stop_key)).lower()
    sc.pause_key = str(s.get("pause_key", sc.pause_key)).lower()
    sc.critical_hp_ratio = float(s.get("critical_hp_ratio", sc.critical_hp_ratio))
    sc.pause_when_players = bool(s.get("pause_when_players", sc.pause_when_players))
    sc.lost_player_timeout = float(s.get("lost_player_timeout", sc.lost_player_timeout))

    a = data.get("advisor", {})
    ac = cfg.advisor
    ac.enabled = bool(a.get("enabled", ac.enabled))
    ac.endpoint = str(a.get("endpoint", ac.endpoint))
    ac.model = str(a.get("model", ac.model))
    ac.interval = float(a.get("interval", ac.interval))
    ac.timeout = float(a.get("timeout", ac.timeout))
    return cfg


def load_profile(path: str) -> Profile:
    data = _load_yaml(path)
    p = Profile()
    p.name = str(data.get("name", p.name))
    p.templates_dir = str(data.get("templates_dir", p.templates_dir))

    pa = data.get("patrol", {})
    p.patrol.waypoints_x = [int(v) for v in pa.get("waypoints_x", [])]
    p.patrol.tolerance = int(pa.get("tolerance", p.patrol.tolerance))
    p.patrol.step_seconds_per_px = float(pa.get("step_seconds_per_px", p.patrol.step_seconds_per_px))
    p.patrol.max_step_seconds = float(pa.get("max_step_seconds", p.patrol.max_step_seconds))

    at = data.get("attack", {})
    p.attack.key = str(at.get("key", p.attack.key)).lower()
    p.attack.range_px = int(at.get("range_px", p.attack.range_px))
    p.attack.vertical_range_px = int(at.get("vertical_range_px", p.attack.vertical_range_px))
    p.attack.cast_seconds = float(at.get("cast_seconds", p.attack.cast_seconds))
    p.attack.repeat = int(at.get("repeat", p.attack.repeat))
    p.attack.cooldown = float(at.get("cooldown", p.attack.cooldown))

    for b in data.get("buffs", []) or []:
        p.buffs.append(BuffCfg(
            key=str(b.get("key", "")).lower(),
            every=float(b.get("every", 120.0)),
            cast_seconds=float(b.get("cast_seconds", 1.2)),
        ))

    for kind, pot in (data.get("potions") or {}).items():
        p.potions[kind] = PotionCfg(
            key=str(pot.get("key", "")).lower(),
            below_ratio=float(pot.get("below_ratio", 0.0)),
            cooldown=float(pot.get("cooldown", 1.0)),
        )
    if not p.patrol.waypoints_x:
        raise ConfigError("profile 至少要有一個 patrol.waypoints_x 巡邏點")
    return p
