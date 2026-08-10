"""UI 欄位 <-> 設定檔的雙向轉換。

GUI 的每個輸入框對應這裡的一個扁平欄位（字串或數字），存檔時再拆回
config/local.yaml 與 profile 兩個檔案。分開放是因為這一層完全不碰 tkinter，
可以直接單元測試——UI 的 bug 有一半都出在「填了沒存到」「存了讀不回來」。
"""
import os
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ..config import AppCfg, Profile

BUFF_SLOTS = 8       # 跟商業腳本一樣提供 8 組循環按鍵

# UI 欄位 -> (預設值, 型別)
FIELDS: Dict[str, Tuple[Any, type]] = {
    "window_title": ("新楓之谷：經典版", str),
    "fps": (8.0, float),
    "attack_key": ("ctrl", str),
    "attack_type": ("directional", str),
    "attack_seconds": (0.6, float),
    "attack_range": (320, int),
    "attack_vrange": (90, int),
    "attack_repeat": (1, int),
    "loot_key": ("z", str),
    "jump_key": ("alt", str),
    "climb_up_key": ("up", str),
    "climb_down_key": ("down", str),
    "hp_key": ("pageup", str),
    "hp_below": (50, int),             # 百分比，跟商業腳本的 UI 一致
    "mp_key": ("pagedown", str),
    "mp_below": (30, int),
    "waypoints": ("auto", str),        # "auto" 或 "30, 90" 或錄製產生的多行 YAML
    "critical_hp": (25, int),
    "critical_hp_frames": (3, int),
    "exp_stall_minutes": (10.0, float),
    "max_runtime_minutes": (0.0, float),   # 0 = 不限
    "pause_when_players": (True, bool),
    "sound_alerts": (True, bool),
    "mob_interval": (0.0, float),
    "outline_black_level": (8, int),
    "outline_min_area": (300, int),
    "filter_followers": (False, bool),
    "panic_return_key": ("", str),
}


def defaults() -> Dict[str, Any]:
    return {k: v for k, (v, _) in FIELDS.items()}


def coerce(values: Dict[str, Any]) -> Dict[str, Any]:
    """UI 送回來的都是字串，轉成正確型別；轉不動就用預設值（不讓 UI 炸掉）。"""
    out = defaults()
    for key, (default, typ) in FIELDS.items():
        if key not in values:
            continue
        raw = values[key]
        try:
            if typ is bool:
                out[key] = raw if isinstance(raw, bool) else \
                    str(raw).strip().lower() in ("1", "true", "yes", "on")
            elif typ is str:
                out[key] = str(raw).strip()
            else:
                out[key] = typ(str(raw).strip())
        except (TypeError, ValueError):
            out[key] = default
    return out


def buffs_from(rows) -> List[Dict[str, Any]]:
    """UI 的 8 組 (鍵, 每幾秒) -> profile 的 buffs 清單，沒填的略過。"""
    out = []
    for key, every in rows:
        key = str(key).strip().lower()
        if not key:
            continue
        try:
            secs = float(str(every).strip())
        except ValueError:
            continue
        if secs > 0:
            out.append({"key": key, "every": secs, "cast_seconds": 1.2})
    return out


def from_config(cfg: AppCfg, profile: Profile) -> Dict[str, Any]:
    """已載入的設定 -> UI 欄位。"""
    v = defaults()
    atk = profile.active_skills()[0]
    hp = profile.potions.get("hp")
    mp = profile.potions.get("mp")
    v.update(
        window_title=cfg.window_title,
        fps=cfg.fps,
        attack_key=atk.key,
        attack_type=atk.type,
        attack_seconds=atk.cast_seconds,
        attack_range=atk.range_px,
        attack_vrange=atk.vertical_range_px,
        attack_repeat=atk.repeat,
        loot_key=profile.loot.key,
        jump_key=profile.patrol.jump_key,
        climb_up_key=profile.patrol.climb_up_key,
        climb_down_key=profile.patrol.climb_down_key,
        hp_key=hp.key if hp else "",
        hp_below=int(round((hp.below_ratio if hp else 0) * 100)),
        mp_key=mp.key if mp else "",
        mp_below=int(round((mp.below_ratio if mp else 0) * 100)),
        waypoints=waypoints_text(profile),
        critical_hp=int(round(cfg.safety.critical_hp_ratio * 100)),
        critical_hp_frames=cfg.safety.critical_hp_frames,
        exp_stall_minutes=cfg.safety.exp_stall_minutes,
        max_runtime_minutes=cfg.safety.max_runtime_minutes,
        pause_when_players=cfg.safety.pause_when_players,
        sound_alerts=cfg.safety.sound_alerts,
        mob_interval=cfg.vision.mob_interval,
        outline_black_level=cfg.vision.outline_black_level,
        outline_min_area=cfg.vision.outline_min_area,
        filter_followers=cfg.vision.filter_followers,
        panic_return_key=profile.panic_return_key,
    )
    return v


def buff_rows(profile: Profile) -> List[Tuple[str, str]]:
    rows = [(b.key, str(b.every)) for b in profile.buffs][:BUFF_SLOTS]
    return rows + [("", "")] * (BUFF_SLOTS - len(rows))


def waypoints_text(profile: Profile) -> str:
    if profile.patrol.auto:
        return "auto"
    pts = profile.patrol.waypoints
    if not pts:
        return "auto"
    if all(p.y is None and not p.keys for p in pts):
        return ", ".join(str(int(p.x)) for p in pts)
    return yaml.safe_dump([_wp_dict(p) for p in pts],
                          allow_unicode=True, default_flow_style=True).strip()


def _wp_dict(p) -> dict:
    d: Dict[str, Any] = {"x": int(p.x)}
    if p.y is not None:
        d["y"] = int(p.y)
    if p.descend != "rope":
        d["descend"] = p.descend
    if p.keys:
        d["keys"] = list(p.keys)
    return d


def parse_waypoints(text: str):
    """UI 的巡邏點欄位 -> profile YAML 用的值。

    支援三種寫法：auto、"30, 90"、以及錄製產生的 YAML 清單。
    """
    text = (text or "").strip()
    if not text or text.lower() == "auto":
        return "auto"
    if "\n" not in text and ":" not in text and "{" not in text:
        try:
            return [int(float(part)) for part in text.replace("，", ",").split(",")
                    if part.strip()]
        except ValueError:
            return "auto"
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return "auto"
    return data if isinstance(data, list) and data else "auto"


# (YAML 區段, YAML 欄位) -> (UI 欄位, 轉換函式)
_PCT = lambda n: round(n / 100.0, 4)          # noqa: E731  百分比 -> 比例

LOCAL_MAP = {
    ("window", "title"): ("window_title", None),
    ("loop", "fps"): ("fps", None),
    ("vision", "mob_interval"): ("mob_interval", None),
    ("vision", "outline_black_level"): ("outline_black_level", None),
    ("vision", "outline_min_area"): ("outline_min_area", None),
    ("vision", "filter_followers"): ("filter_followers", None),
    ("safety", "critical_hp_ratio"): ("critical_hp", _PCT),
    ("safety", "critical_hp_frames"): ("critical_hp_frames", None),
    ("safety", "exp_stall_minutes"): ("exp_stall_minutes", None),
    ("safety", "max_runtime_minutes"): ("max_runtime_minutes", None),
    ("safety", "pause_when_players"): ("pause_when_players", None),
    ("safety", "sound_alerts"): ("sound_alerts", None),
}

PROFILE_MAP = {
    ("patrol", "waypoints"): ("waypoints", parse_waypoints),
    ("patrol", "jump_key"): ("jump_key", None),
    ("patrol", "climb_up_key"): ("climb_up_key", None),
    ("patrol", "climb_down_key"): ("climb_down_key", None),
    ("attack", "key"): ("attack_key", None),
    ("attack", "type"): ("attack_type", None),
    ("attack", "range_px"): ("attack_range", None),
    ("attack", "vertical_range_px"): ("attack_vrange", None),
    ("attack", "cast_seconds"): ("attack_seconds", None),
    ("attack", "repeat"): ("attack_repeat", None),
    ("loot", "key"): ("loot_key", None),
}


def _build(table, v: Dict[str, Any], present) -> dict:
    """只輸出 UI 真的有給的欄位。

    畫面上沒有的欄位絕對不能寫進檔案——不然使用者按一次「儲存設定」，
    那些他從沒看過的設定就會被悄悄改成預設值。
    """
    out: Dict[str, Any] = {}
    for (section, key), (field, convert) in table.items():
        if field not in present:
            continue
        value = v[field]
        out.setdefault(section, {})[key] = convert(value) if convert else value
    return out


def to_yaml(values: Dict[str, Any], buffs: Optional[List[Dict[str, Any]]],
            profile_name: str) -> Tuple[dict, dict]:
    """UI 欄位 -> (local.yaml 內容, profile.yaml 內容)。"""
    v = coerce(values)
    present = set(values)
    local = _build(LOCAL_MAP, v, present)
    prof = _build(PROFILE_MAP, v, present)
    prof["name"] = profile_name
    if buffs is not None:
        prof["buffs"] = buffs

    potions = {}
    if "hp_key" in present and v["hp_key"]:
        potions["hp"] = {"key": v["hp_key"], "below_ratio": _PCT(v["hp_below"])}
    if "mp_key" in present and v["mp_key"]:
        potions["mp"] = {"key": v["mp_key"], "below_ratio": _PCT(v["mp_below"])}
    if potions:
        prof["potions"] = potions
    if "panic_return_key" in present and v["panic_return_key"]:
        prof["panic_return_key"] = v["panic_return_key"]
    return local, prof


def merge_into(path: str, patch: dict) -> dict:
    """讀出既有 YAML、深層合併 patch 後寫回。

    local.yaml 裡還有 regions（校正結果）等 UI 沒有的欄位，直接覆寫會把它們
    洗掉——使用者按一次「保存」就要重新校正，這是絕對不能發生的事。
    """
    data = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    merged = _deep_merge(data, patch)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)
    return merged


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
