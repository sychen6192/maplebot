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

LOCAL_NAME = "local.yaml"
LOCAL_OVERRIDE = os.path.join("config", LOCAL_NAME)   # 僅供顯示訊息用


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
    mob_detector: str = "outline"   # outline | template | yolo | remote
    outline_black_level: int = 8       # 判定為描邊的最大亮度（JPEG 測試時調高到 12~20）
    outline_min_area: int = 300        # 太小的黑塊當雜訊（790px 寬為基準）
    outline_max_area: int = 20000      # 太大的當背景/UI（790px 寬為基準）
    outline_auto_scale: bool = True    # 依實際畫面寬度等比例縮放上面的門檻
    outline_close_kernel: int = 20     # 把斷續描邊連成整塊
    outline_player_box: Tuple[int, int] = (100, 140)   # 畫面中央挖掉的自己
    mob_match_threshold: float = 0.72
    yolo_model: str = ""
    yolo_confidence: float = 0.5
    remote_endpoint: str = ""          # mob_detector=remote：推理伺服器位址
    remote_timeout: float = 1.0
    remote_jpeg_quality: int = 80
    remote_max_width: int = 640        # 送出前先縮到這個寬度（0=不縮）
    mob_interval: float = 0.0          # 每幾秒才做一次怪物偵測（0=每個 tick 都做）
    # 只在角色周圍這個大小的框內找怪（None=整個 playfield）。
    # 角色永遠在畫面中央，打不到的地方本來就不用看——省時間也少誤判。
    mob_search_box: Optional[Tuple[int, int]] = None


@dataclass
class SafetyCfg:
    stop_key: str = "f12"
    pause_key: str = "f9"
    critical_hp_ratio: float = 0.25
    pause_when_players: bool = True
    lost_player_timeout: float = 5.0
    sound_alerts: bool = True         # 危險事件用 winsound 嗶聲提醒
    black_screen_pause: bool = True   # 黑屏（斷線/讀圖）自動暫停
    exp_stall_minutes: float = 10.0   # 幾分鐘沒賺到經驗就暫停（0=不檢查）


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
    sources: List[str] = field(default_factory=list)   # 實際載入了哪些檔案

    def region(self, name: str) -> Region:
        if name not in self.regions:
            raise ConfigError(f"config 缺少 regions.{name}，請先執行 tools/calibrate.py 校正")
        return self.regions[name]


@dataclass
class Waypoint:
    x: float                          # >1 = 小地圖絕對 px；<=1 = 佔小地圖寬度比例
    y: Optional[float] = None         # 同上（比例是佔小地圖高度）；None = 只對 x（單層地圖）
    descend: str = "rope"             # 往下的方式：rope（抓繩下降）| jump（下跳平台）
    keys: List[str] = field(default_factory=list)  # 抵達時依序敲的鍵（跳躍/技能等）


@dataclass
class PatrolCfg:
    waypoints: List[Waypoint] = field(default_factory=list)
    auto: bool = False                # waypoints: auto -> 開場撞牆量出可走範圍自動生成
    tolerance: int = 4
    step_seconds_per_px: float = 0.02
    max_step_seconds: float = 0.45
    stuck_seconds: float = 4.0        # 走了這麼久位置都沒變 -> 判定卡住
    stuck_px: int = 3
    jump_key: str = "alt"             # 卡住脫困用的跳躍鍵
    # --- waypoints: auto 的探邊參數 ---
    probe_seconds: float = 0.35       # 每次往同方向試探走多久
    probe_stall_px: int = 2           # x 變化在此之內視為沒動
    probe_stalls: int = 3             # 連續幾次沒動判定撞牆
    probe_margin_px: int = 6          # 量到的邊界往內縮多少當巡邏點
    probe_min_span_px: int = 12       # 可走範圍小於此值視為校正失敗（按鍵沒生效等）
    # --- 垂直移動（爬繩／下平台）---
    y_tolerance: int = 3              # 小地圖 y 差多少內算到位
    climb_seconds: float = 0.45       # 每步按住上/下鍵多久
    climb_stall_px: int = 2           # y 變化在此之內視為沒爬動
    climb_stalls: int = 3             # 連續幾步沒爬動 -> 判定沒抓到繩子
    climb_retries: int = 2            # 重新對位幾次後放棄這個巡邏點
    climb_up_key: str = "up"
    climb_down_key: str = "down"


@dataclass
class AttackCfg:
    """一個攻擊技能。多技能輪替時 profile.skills 就是一串這個。"""
    key: str = "x"
    type: str = "directional"         # directional（要面向）| aoe（原地放）
    range_px: int = 320
    vertical_range_px: int = 90
    cast_seconds: float = 0.6
    repeat: int = 1
    cooldown: float = 0.2
    min_mp: float = 0.0               # MP 低於此比例就不放技能（0=不檢查）
    min_mobs: int = 1                 # 範圍內至少幾隻才放（大絕別浪費在單隻怪）


@dataclass
class BuffCfg:
    key: str = ""
    every: float = 120.0
    cast_seconds: float = 1.2
    min_mp: float = 0.0               # MP 不夠時延後補 buff，等回魔


@dataclass
class LootCfg:
    """打完怪自動撿物。"""
    key: str = ""                     # 撿取鍵（楓谷預設 Z）
    every: float = 2.0                # 兩次撿取的最短間隔
    taps: int = 4                     # 一次按幾下（掉落物散落成一排）
    after_combat: float = 6.0         # 只在最後一次攻擊後這麼多秒內撿（0=隨時）


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
    attack: AttackCfg = field(default_factory=AttackCfg)   # 單技能寫法（相容舊 profile）
    skills: List[AttackCfg] = field(default_factory=list)  # 多技能輪替；空的話用 attack
    buffs: List[BuffCfg] = field(default_factory=list)
    potions: Dict[str, PotionCfg] = field(default_factory=dict)
    loot: LootCfg = field(default_factory=LootCfg)
    panic_return_key: str = ""        # 設定後，Panic 時會先按回城卷再停止

    def active_skills(self) -> List[AttackCfg]:
        """依優先權排好的技能清單。沒設定 skills 就退回單一 attack。"""
        return self.skills or [self.attack]


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


def _parse_attack(raw: dict, cfg: AttackCfg) -> AttackCfg:
    """把 YAML 的攻擊設定填進 AttackCfg。attack: 與 skills: 共用同一份解析。"""
    cfg.key = str(raw.get("key", cfg.key)).lower()
    cfg.type = str(raw.get("type", cfg.type)).lower()
    if cfg.type not in ("directional", "aoe"):
        raise ConfigError(f"attack.type 只能是 directional 或 aoe，拿到: {cfg.type!r}")
    cfg.range_px = int(raw.get("range_px", cfg.range_px))
    cfg.vertical_range_px = int(raw.get("vertical_range_px", cfg.vertical_range_px))
    cfg.cast_seconds = float(raw.get("cast_seconds", cfg.cast_seconds))
    cfg.repeat = int(raw.get("repeat", cfg.repeat))
    cfg.cooldown = float(raw.get("cooldown", cfg.cooldown))
    cfg.min_mp = float(raw.get("min_mp", cfg.min_mp))
    cfg.min_mobs = int(raw.get("min_mobs", cfg.min_mobs))
    return cfg


def _parse_waypoint(raw) -> Waypoint:
    if isinstance(raw, dict):
        if "x" not in raw:
            raise ConfigError(f"waypoint 少了 x: {raw!r}")
        keys = raw.get("keys", [])
        if not isinstance(keys, list):
            raise ConfigError(f"waypoint keys 必須是清單: {raw!r}")
        descend = str(raw.get("descend", "rope")).lower()
        if descend not in ("rope", "jump"):
            raise ConfigError(
                f"waypoint descend 只能是 rope（抓繩下降）或 jump（下跳平台），"
                f"拿到: {descend!r}")
        y = raw.get("y")
        return Waypoint(x=float(raw["x"]),
                        y=None if y is None else float(y),
                        descend=descend,
                        keys=[str(k).lower() for k in keys])
    if isinstance(raw, (int, float)):
        return Waypoint(x=float(raw))
    raise ConfigError(f"看不懂的 waypoint 格式: {raw!r}")


def resolve_local_path(config_path: str) -> str:
    """local.yaml 找的是**設定檔旁邊**那一個，不是當前目錄底下的。

    用相對於 CWD 的路徑會讓「從別的目錄執行」時靜靜地讀不到個人設定。
    """
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), LOCAL_NAME)


def load_config(path: str, local_path: Optional[str] = None) -> AppCfg:
    """local_path=None 自動找設定檔旁的 local.yaml；傳 "" 則完全不套用覆寫。"""
    data = _load_yaml(path)
    cfg = AppCfg()
    cfg.sources.append(path)
    if local_path is None:
        local_path = resolve_local_path(path)
    if local_path and os.path.exists(local_path):
        data = _deep_merge(data, _load_yaml(local_path))
        cfg.sources.append(local_path)

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
    if vc.mob_detector not in ("outline", "template", "yolo", "remote"):
        raise ConfigError(
            f"vision.mob_detector 只能是 outline / template / yolo / remote，"
            f"拿到: {vc.mob_detector!r}")
    vc.outline_black_level = int(v.get("outline_black_level", vc.outline_black_level))
    vc.outline_min_area = int(v.get("outline_min_area", vc.outline_min_area))
    vc.outline_max_area = int(v.get("outline_max_area", vc.outline_max_area))
    vc.outline_close_kernel = int(v.get("outline_close_kernel", vc.outline_close_kernel))
    vc.outline_auto_scale = bool(v.get("outline_auto_scale", vc.outline_auto_scale))
    if "outline_player_box" in v:
        pb = v["outline_player_box"]
        vc.outline_player_box = (int(pb[0]), int(pb[1]))
    vc.mob_match_threshold = float(v.get("mob_match_threshold", vc.mob_match_threshold))
    vc.yolo_model = str(v.get("yolo_model", vc.yolo_model))
    vc.yolo_confidence = float(v.get("yolo_confidence", vc.yolo_confidence))
    vc.remote_endpoint = str(v.get("remote_endpoint", vc.remote_endpoint))
    vc.remote_timeout = float(v.get("remote_timeout", vc.remote_timeout))
    vc.remote_jpeg_quality = int(v.get("remote_jpeg_quality", vc.remote_jpeg_quality))
    vc.remote_max_width = int(v.get("remote_max_width", vc.remote_max_width))
    vc.mob_interval = float(v.get("mob_interval", vc.mob_interval))
    if v.get("mob_search_box"):
        sb = v["mob_search_box"]
        vc.mob_search_box = (int(sb[0]), int(sb[1]))
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
    sc.exp_stall_minutes = float(s.get("exp_stall_minutes", sc.exp_stall_minutes))

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
    if isinstance(raw_wps, str):
        if raw_wps.strip().lower() != "auto":
            raise ConfigError(
                f"patrol.waypoints 是字串時只能是 auto（開場自動量測），拿到: {raw_wps!r}")
        p.patrol.auto = True
    else:
        p.patrol.waypoints = [_parse_waypoint(w) for w in raw_wps]
    pt = p.patrol
    pt.tolerance = int(pa.get("tolerance", pt.tolerance))
    pt.step_seconds_per_px = float(pa.get("step_seconds_per_px", pt.step_seconds_per_px))
    pt.max_step_seconds = float(pa.get("max_step_seconds", pt.max_step_seconds))
    pt.stuck_seconds = float(pa.get("stuck_seconds", pt.stuck_seconds))
    pt.stuck_px = int(pa.get("stuck_px", pt.stuck_px))
    pt.jump_key = str(pa.get("jump_key", pt.jump_key)).lower()
    pt.probe_seconds = float(pa.get("probe_seconds", pt.probe_seconds))
    pt.probe_stall_px = int(pa.get("probe_stall_px", pt.probe_stall_px))
    pt.probe_stalls = int(pa.get("probe_stalls", pt.probe_stalls))
    pt.probe_margin_px = int(pa.get("probe_margin_px", pt.probe_margin_px))
    pt.probe_min_span_px = int(pa.get("probe_min_span_px", pt.probe_min_span_px))
    pt.y_tolerance = int(pa.get("y_tolerance", pt.y_tolerance))
    pt.climb_seconds = float(pa.get("climb_seconds", pt.climb_seconds))
    pt.climb_stall_px = int(pa.get("climb_stall_px", pt.climb_stall_px))
    pt.climb_stalls = int(pa.get("climb_stalls", pt.climb_stalls))
    pt.climb_retries = int(pa.get("climb_retries", pt.climb_retries))
    pt.climb_up_key = str(pa.get("climb_up_key", pt.climb_up_key)).lower()
    pt.climb_down_key = str(pa.get("climb_down_key", pt.climb_down_key)).lower()

    _parse_attack(data.get("attack", {}), p.attack)
    for raw in data.get("skills", []) or []:
        if not isinstance(raw, dict):
            raise ConfigError(f"skills 的每一項都要是 mapping，拿到: {raw!r}")
        p.skills.append(_parse_attack(raw, AttackCfg()))
    if p.skills and not all(s.key for s in p.skills):
        raise ConfigError("skills 裡每個技能都必須有 key")

    for b in data.get("buffs", []) or []:
        p.buffs.append(BuffCfg(
            key=str(b.get("key", "")).lower(),
            every=float(b.get("every", 120.0)),
            cast_seconds=float(b.get("cast_seconds", 1.2)),
            min_mp=float(b.get("min_mp", 0.0)),
        ))

    lt = data.get("loot", {}) or {}
    p.loot.key = str(lt.get("key", p.loot.key)).lower()
    p.loot.every = float(lt.get("every", p.loot.every))
    p.loot.taps = int(lt.get("taps", p.loot.taps))
    p.loot.after_combat = float(lt.get("after_combat", p.loot.after_combat))

    for kind, pot in (data.get("potions") or {}).items():
        p.potions[kind] = PotionCfg(
            key=str(pot.get("key", "")).lower(),
            below_ratio=float(pot.get("below_ratio", 0.0)),
            cooldown=float(pot.get("cooldown", 1.0)),
        )
    if not p.patrol.auto and not p.patrol.waypoints:
        raise ConfigError(
            "profile 至少要有一個 patrol.waypoints 巡邏點，"
            "或設 patrol.waypoints: auto 讓程式開場自己量")
    return p
