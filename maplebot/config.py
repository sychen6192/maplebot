"""載入與驗證 YAML 設定（全域 config + 地圖 profile）。

支援 config/local.yaml 覆寫層（參考 MapleStoryAutoLevelUp 的
config_default + config_custom 設計）：個人設定放 local.yaml，
不用動版本控管的 default.yaml。
"""
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import yaml

Region = Tuple[int, int, int, int]  # x, y, w, h（遊戲視窗 client 區座標）

LOCAL_OVERRIDE = os.path.join("config", "local.yaml")


class ConfigError(Exception):
    pass


def _region(raw, name: str) -> Region:
    if not (isinstance(raw, (list, tuple)) and len(raw) == 4):
        raise ConfigError(f"regions.{name} 必須是 [x, y, w, h]，拿到: {raw!r}")
    x, y, w, h = (int(v) for v in raw)
    if w <= 0 or h <= 0:
        raise ConfigError(f"regions.{name} 的寬高必須為正: {raw!r}")
    return (x, y, w, h)


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class VisionCfg:
    minimap_player_rgb: Tuple[int, int, int] = (255, 255, 0)
    minimap_other_rgb: Tuple[int, int, int] = (255, 0, 0)
    color_tolerance: int = 60
    min_dot_pixels: int = 2
    max_dot_pixels: int = 60          # 大於此面積的色塊視為地形而非玩家點
    ui_templates_dir: str = "data/templates/ui"
    minimap_border: int = 6           # auto 定位小地圖時向內縮的邊框厚度
    bar_colors: Dict[str, str] = field(default_factory=lambda: {"hp": "red", "mp": "blue", "exp": "yellow"})
    mob_detector: str = "template"  # template | yolo | remote
    mob_match_threshold: float = 0.72
    yolo_model: str = ""
    yolo_confidence: float = 0.5
    remote_endpoint: str = ""          # mob_detector=remote：推理伺服器位址
    remote_timeout: float = 1.0
    remote_jpeg_quality: int = 80
    remote_max_width: int = 640        # 送出前先縮到這個寬度（0=不縮）
    mob_interval: float = 0.0          # 每幾秒才做一次怪物偵測（0=每個 tick 都做）


@dataclass
class SafetyCfg:
    stop_key: str = "f12"
    pause_key: str = "f9"
    critical_hp_ratio: float = 0.25
    pause_when_players: bool = True
    lost_player_timeout: float = 5.0
    sound_alerts: bool = True         # 危險事件用 winsound 嗶聲提醒
    black_screen_pause: bool = True   # 黑屏（斷線/讀圖）自動暫停


@dataclass
class AdvisorCfg:
    enabled: bool = False
    endpoint: str = "http://127.0.0.1:11434/v1/chat/completions"  # Ollama 預設埠
    model: str = "qwen2.5vl:7b"
    interval: float = 20.0
    timeout: float = 15.0


@dataclass
class AppCfg:
    window_title: str = "MapleStory"
    capture_method: str = "auto"      # auto | printwindow | screen
    fps: float = 8.0
    regions: Dict[str, Region] = field(default_factory=dict)
    minimap_auto: bool = False        # regions.minimap: auto 時用角落模板自動定位
    vision: VisionCfg = field(default_factory=VisionCfg)
    safety: SafetyCfg = field(default_factory=SafetyCfg)
    advisor: AdvisorCfg = field(default_factory=AdvisorCfg)

    def region(self, name: str) -> Region:
        if name not in self.regions:
            raise ConfigError(f"config 缺少 regions.{name}，請先執行 tools/calibrate.py 校正")
        return self.regions[name]


@dataclass
class Waypoint:
    x: float                          # >1 = 小地圖絕對 px；<=1 = 佔小地圖寬度比例
    keys: List[str] = field(default_factory=list)  # 抵達時依序敲的鍵（跳躍/技能等）


@dataclass
class PatrolCfg:
    waypoints: List[Waypoint] = field(default_factory=list)
    tolerance: int = 4
    step_seconds_per_px: float = 0.02
    max_step_seconds: float = 0.45
    stuck_seconds: float = 4.0        # 走了這麼久位置都沒變 -> 判定卡住
    stuck_px: int = 3
    jump_key: str = "alt"             # 卡住脫困用的跳躍鍵


@dataclass
class AttackCfg:
    key: str = "x"
    type: str = "directional"         # directional（要面向）| aoe（原地放）
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
    panic_return_key: str = ""        # 設定後，Panic 時會先按回城卷再停止


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


def _parse_waypoint(raw) -> Waypoint:
    if isinstance(raw, dict):
        if "x" not in raw:
            raise ConfigError(f"waypoint 少了 x: {raw!r}")
        keys = raw.get("keys", [])
        if not isinstance(keys, list):
            raise ConfigError(f"waypoint keys 必須是清單: {raw!r}")
        return Waypoint(x=float(raw["x"]), keys=[str(k).lower() for k in keys])
    if isinstance(raw, (int, float)):
        return Waypoint(x=float(raw))
    raise ConfigError(f"看不懂的 waypoint 格式: {raw!r}")


def load_config(path: str, local_path: str = LOCAL_OVERRIDE) -> AppCfg:
    data = _load_yaml(path)
    if local_path and os.path.exists(local_path):
        data = _deep_merge(data, _load_yaml(local_path))
    cfg = AppCfg()

    win = data.get("window", {})
    cfg.window_title = str(win.get("title", cfg.window_title))
    cfg.capture_method = str(win.get("capture", cfg.capture_method)).lower()
    if cfg.capture_method not in ("auto", "printwindow", "screen"):
        raise ConfigError(
            f"window.capture 只能是 auto / printwindow / screen，"
            f"拿到: {cfg.capture_method!r}")
    cfg.fps = float(data.get("loop", {}).get("fps", cfg.fps))

    for name, raw in (data.get("regions") or {}).items():
        if name == "minimap" and raw == "auto":
            cfg.minimap_auto = True
            continue
        cfg.regions[name] = _region(raw, name)

    v = data.get("vision", {})
    vc = cfg.vision
    vc.minimap_player_rgb = tuple(v.get("minimap_player_rgb", vc.minimap_player_rgb))  # type: ignore
    vc.minimap_other_rgb = tuple(v.get("minimap_other_rgb", vc.minimap_other_rgb))  # type: ignore
    vc.color_tolerance = int(v.get("color_tolerance", vc.color_tolerance))
    vc.min_dot_pixels = int(v.get("min_dot_pixels", vc.min_dot_pixels))
    vc.max_dot_pixels = int(v.get("max_dot_pixels", vc.max_dot_pixels))
    vc.ui_templates_dir = str(v.get("ui_templates_dir", vc.ui_templates_dir))
    vc.minimap_border = int(v.get("minimap_border", vc.minimap_border))
    vc.bar_colors.update(v.get("bars", {}))
    vc.mob_detector = str(v.get("mob_detector", vc.mob_detector)).lower()
    if vc.mob_detector not in ("template", "yolo", "remote"):
        raise ConfigError(
            f"vision.mob_detector 只能是 template / yolo / remote，"
            f"拿到: {vc.mob_detector!r}")
    vc.mob_match_threshold = float(v.get("mob_match_threshold", vc.mob_match_threshold))
    vc.yolo_model = str(v.get("yolo_model", vc.yolo_model))
    vc.yolo_confidence = float(v.get("yolo_confidence", vc.yolo_confidence))
    vc.remote_endpoint = str(v.get("remote_endpoint", vc.remote_endpoint))
    vc.remote_timeout = float(v.get("remote_timeout", vc.remote_timeout))
    vc.remote_jpeg_quality = int(v.get("remote_jpeg_quality", vc.remote_jpeg_quality))
    vc.remote_max_width = int(v.get("remote_max_width", vc.remote_max_width))
    vc.mob_interval = float(v.get("mob_interval", vc.mob_interval))
    if vc.mob_detector == "remote" and not vc.remote_endpoint:
        raise ConfigError("vision.mob_detector=remote 必須設定 vision.remote_endpoint")

    s = data.get("safety", {})
    sc = cfg.safety
    sc.stop_key = str(s.get("stop_key", sc.stop_key)).lower()
    sc.pause_key = str(s.get("pause_key", sc.pause_key)).lower()
    sc.critical_hp_ratio = float(s.get("critical_hp_ratio", sc.critical_hp_ratio))
    sc.pause_when_players = bool(s.get("pause_when_players", sc.pause_when_players))
    sc.lost_player_timeout = float(s.get("lost_player_timeout", sc.lost_player_timeout))
    sc.sound_alerts = bool(s.get("sound_alerts", sc.sound_alerts))
    sc.black_screen_pause = bool(s.get("black_screen_pause", sc.black_screen_pause))

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
    p.panic_return_key = str(data.get("panic_return_key", "")).lower()

    pa = data.get("patrol", {})
    raw_wps = pa.get("waypoints", pa.get("waypoints_x", []))
    p.patrol.waypoints = [_parse_waypoint(w) for w in raw_wps]
    p.patrol.tolerance = int(pa.get("tolerance", p.patrol.tolerance))
    p.patrol.step_seconds_per_px = float(pa.get("step_seconds_per_px", p.patrol.step_seconds_per_px))
    p.patrol.max_step_seconds = float(pa.get("max_step_seconds", p.patrol.max_step_seconds))
    p.patrol.stuck_seconds = float(pa.get("stuck_seconds", p.patrol.stuck_seconds))
    p.patrol.stuck_px = int(pa.get("stuck_px", p.patrol.stuck_px))
    p.patrol.jump_key = str(pa.get("jump_key", p.patrol.jump_key)).lower()

    at = data.get("attack", {})
    p.attack.key = str(at.get("key", p.attack.key)).lower()
    p.attack.type = str(at.get("type", p.attack.type)).lower()
    if p.attack.type not in ("directional", "aoe"):
        raise ConfigError(f"attack.type 只能是 directional 或 aoe，拿到: {p.attack.type!r}")
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
    if not p.patrol.waypoints:
        raise ConfigError("profile 至少要有一個 patrol.waypoints 巡邏點")
    return p
