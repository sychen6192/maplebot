"""UI 欄位 <-> 設定檔的雙向轉換、欄位規格表與存檔前驗證。

GUI 的每個輸入框對應這裡的一個扁平欄位（字串或數字），存檔時再拆回
config/local.yaml 與 profile 兩個檔案。分開放是因為這一層完全不碰 tkinter，
可以直接單元測試——UI 的 bug 有一半都出在「填了沒存到」「存了讀不回來」。

實測過的存檔破壞（在 scratch 複本上）都在這一層修掉：
- 巡邏點每存一次就掉 tolerance / grab / run_up，比例座標 0.25 被 int() 成 0；
- buff 的 cast_seconds 被重設成 1.2、min_mp 不見；
- 清空的藥水鍵存完又變回舊鍵；
- safe_dump 把 profile 裡記錄實測數據的註解全部洗掉。
"""
import math
import os
import re
import shutil
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

from ..config import AppCfg, ConfigError, Profile, parse_waypoint
from ..control.input_win import SCANCODES

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
    """UI 送回來的都是字串，轉成正確型別；轉不動就用預設值（不讓 UI 炸掉）。

    這是最後一道網，不是驗證：validate() 擋下的值根本不該走到這裡。
    """
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
            elif typ is int:
                out[key] = _to_int(str(raw).strip())
            else:
                out[key] = typ(str(raw).strip())
        except (TypeError, ValueError):
            out[key] = default
    return out


def _to_int(text: str) -> int:
    # validate() 把 "3.0" 當成合法整數放行；這裡若還是 int("3.0") 就會丟
    # ValueError 退回預設值——使用者填 3、檔案裡卻寫成 1，正是要根絕的那種 bug
    try:
        return int(text)
    except ValueError:
        f = float(text)
        if not f.is_integer():
            raise
        return int(f)


# ---------------------------------------------------------------------------
# 數字格式：比較、顯示、寫檔共用同一個
# ---------------------------------------------------------------------------

def _fmt(x: float) -> str:
    """'%g' 風格但不丟精度：0.6 -> '0.6'、320.0 -> '320'。

    normalize 與 display 必須用同一個格式——display 若四捨五入到 6 位，
    填回欄位的字串跟基準比對就永遠不相等，欄位一打開就顯示「已修改」。
    """
    if math.isfinite(x) and x.is_integer():
        return str(int(x))
    return repr(x)


def _num_value(x: float):
    """整數值寫成 int，YAML 裡才是 30 而不是 30.0。"""
    return int(x) if math.isfinite(x) and x.is_integer() else x


# ---------------------------------------------------------------------------
# buff
# ---------------------------------------------------------------------------

def buffs_from(rows, existing=None) -> List[Dict[str, Any]]:
    """UI 的 8 組 (鍵, 每幾秒) -> profile 的 buffs 清單，沒填的略過。

    existing 是目前 profile 的 BuffCfg 清單。UI 只有「鍵」和「秒數」兩格，
    以前每存一次 cast_seconds 就被重設成 1.2、min_mp 直接消失
    （example.yaml 的 1.5 秒實測被改掉）。同一個鍵的 buff 沿用原本的這兩項；
    UI 放不下的第 9 組以後原封不動接在後面。existing=None 時輸出跟以前一字不差。
    """
    old: Dict[str, Any] = {}
    for b in (existing or [])[:BUFF_SLOTS]:
        old.setdefault(str(b.key).strip().lower(), b)
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
            prev = old.get(key)
            if prev is None:
                out.append({"key": key, "every": secs, "cast_seconds": 1.2})
            else:
                out.append({"key": key, "every": secs,
                            "cast_seconds": prev.cast_seconds, "min_mp": prev.min_mp})
    for b in (existing or [])[BUFF_SLOTS:]:
        out.append({"key": b.key, "every": b.every,
                    "cast_seconds": b.cast_seconds, "min_mp": b.min_mp})
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


# ---------------------------------------------------------------------------
# 巡邏點
# ---------------------------------------------------------------------------

def waypoints_text(profile: Profile) -> str:
    """profile 的巡邏點 -> UI 那格文字。

    單層、沒有額外欄位的路線維持 "12, 44"；其餘一行一個點
    （`- {x: 74, y: 72, tolerance: 1}`），錄出來的 9 點三層路線才看得清楚。
    """
    if profile.patrol.auto:
        return "auto"
    pts = profile.patrol.waypoints
    if not pts:
        return "auto"
    dicts = [_wp_dict(p) for p in pts]
    if all(list(d) == ["x"] for d in dicts):
        return ", ".join(_fmt(p.x) for p in pts)
    return waypoint_lines(dicts)


def waypoint_lines(dicts: Iterable[dict]) -> str:
    """巡邏點 dict 清單 -> 一行一個的 YAML 區塊序列。錄製結果也用這個格式。"""
    return "\n".join(
        "- " + yaml.safe_dump(d, default_flow_style=True, sort_keys=False,
                              allow_unicode=True, width=1 << 16).strip()
        for d in dicts)


def _wp_dict(p) -> dict:
    """Waypoint -> YAML dict，每個跟預設值不同的欄位都要寫出來。

    以前只寫 x/y/descend/keys：1366x768 那條 x=54 的繩（tolerance 1、
    grab: run，commit 8e80be0 實測調出來的）按一次儲存就變回站定跳抓。
    """
    d: Dict[str, Any] = {"x": _num_value(p.x)}
    if p.y is not None:
        d["y"] = _num_value(p.y)
    if p.tolerance is not None:
        d["tolerance"] = p.tolerance
    if p.grab != "stand":
        d["grab"] = p.grab
    if p.run_up != 4:
        d["run_up"] = p.run_up
    if p.descend != "rope":
        d["descend"] = p.descend
    if p.keys:
        d["keys"] = list(p.keys)
    return d


_WP_HINT = "寫法：30, 90　或錄製產生的清單"


def _waypoint_numbers(text: str) -> Optional[list]:
    """"30, 90"、"30，90"、"30 90" -> [30, 90]；比例值 0.25 保持小數。"""
    parts = [p for p in re.split(r"[,，\s]+", text) if p]
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if not nums or not all(math.isfinite(n) for n in nums):
        return None
    return [_num_value(n) for n in nums]


def _is_simple(text: str) -> bool:
    return not any(c in text for c in "\n:{[")


def parse_waypoints(text: str):
    """UI 的巡邏點欄位 -> profile YAML 用的值。

    支援三種寫法：auto、"30, 90"（逗號、全形逗號或空白都行）、
    以及錄製產生的 YAML 清單。看不懂就回 auto——要擋下來請用
    parse_waypoints_strict。
    """
    text = (text or "").strip()
    if not text or text.lower() == "auto":
        return "auto"
    if _is_simple(text):
        nums = _waypoint_numbers(text)
        return nums if nums is not None else "auto"
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return "auto"
    return data if isinstance(data, list) and data else "auto"


def parse_waypoints_strict(text: str) -> Tuple[Any, str]:
    """跟 parse_waypoints 一樣，但每一點都過載入器自己的解析：(值, 錯誤訊息)。

    成功時錯誤訊息是 ""；失敗時值是 None，訊息就是 load_profile 會丟的那一句
    （例如「waypoint 少了 x: {'y': 3}」）。以前不合法的巡邏點會先被寫進檔案，
    下次啟動才在 load_profile 炸掉。
    """
    raw = (text or "").strip()
    if not raw or raw.lower() == "auto":
        return "auto", ""
    if _is_simple(raw):
        nums = _waypoint_numbers(raw)
        if nums is None:
            return None, f"看不懂「{_clip(raw)}」——{_WP_HINT}"
        return nums, ""
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None, f"看不懂「{_clip(raw)}」——{_WP_HINT}"
    if not isinstance(data, list):
        return None, f"巡邏點要是一串點或 auto，看不懂「{_clip(raw)}」——{_WP_HINT}"
    if not data:
        return None, "至少要有一個巡邏點，或改用自動探邊（auto）"
    for item in data:
        try:
            parse_waypoint(item)
        except ConfigError as e:
            return None, str(e)
        except (TypeError, ValueError) as e:      # 例如 tolerance: abc 的 int() 失敗
            return None, f"waypoint 數值看不懂: {item!r}（{e}）"
    return data, ""


def _clip(text: str, n: int = 24) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n] + "…"


@dataclass(frozen=True)
class Problem:
    """一個欄位的問題。validate 只用 'error' | 'warn'；
    describe_waypoints 另外會用 'ok' | 'info'。text 不含符號，符號見 GLYPHS。"""
    level: str
    text: str


GLYPHS = {"ok": "√", "info": "・", "warn": "！", "error": "×"}


def describe_waypoints(text: str) -> Problem:
    """巡邏點欄位底下那一行即時摘要。

    刻意**不**警告「x 超出小地圖寬度」：會捲動的大地圖上，巡邏點本來就可以
    超出小地圖寬度（runner.py 的捲動座標，issue #7）。
    """
    value, err = parse_waypoints_strict(text)
    if err:
        return Problem("error", err)
    if value == "auto":
        return Problem("ok", "自動探邊：開場往左右走到撞牆，自己量巡邏範圍")
    pts = [parse_waypoint(w) for w in value]
    n = len(pts)
    if n == 1:
        return Problem("warn", "只有 1 個巡邏點，角色會站在原地——至少 2 個點，或改用自動探邊")
    levels = sorted({p.y for p in pts if p.y is not None})
    if len(levels) >= 2:
        # 錄製原始輸出可以有 21 層（爬繩途中都被當成樓層），全列出來一行放不下
        ys = "／".join(_fmt(y) for y in levels) if len(levels) <= 5 \
            else f"{_fmt(levels[0])}–{_fmt(levels[-1])}"
        msg = f"{n} 個巡邏點、{len(levels)} 層（y {ys}）"
    else:
        xs = [p.x for p in pts]
        msg = f"{n} 個巡邏點（單層）：x {_fmt(min(xs))} ↔ {_fmt(max(xs))}"
    if any(p.x <= 1 or (p.y is not None and p.y <= 1) for p in pts):
        return Problem("info", msg + "。0.25 這種 ≤ 1 的值是小地圖寬度的比例")
    return Problem("ok", msg)


# ---------------------------------------------------------------------------
# UI 欄位 <-> YAML
# ---------------------------------------------------------------------------

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

LOCAL_FIELDS = frozenset(f for f, _ in LOCAL_MAP.values())
# Profile 範圍的欄位，外加 'buffs'（controller.changes() 用這個名字回報 buff 有改）。
# 範本唯讀規則靠它判斷「這次存檔會不會動到範本」
PROFILE_FIELDS = frozenset(set(FIELDS) - LOCAL_FIELDS) | {"buffs"}


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


def _potion(v: Dict[str, Any], present, key_field: str, pct_field: str) -> dict:
    """藥水欄位 -> potions.hp / potions.mp 的 patch（只含有給的那一半）。

    清空藥水鍵要寫成 {key: '', below_ratio: 0}：fsm.decide 判斷喝不喝藥只看
    below_ratio、不看鍵（fsm.py 的 hp_pot 分支），比例留著的話 bot 會一直去按
    一個空鍵。以前則是根本不寫，存完重載又變回舊的鍵。
    """
    out: Dict[str, Any] = {}
    if key_field in present:
        out["key"] = v[key_field]
        if not v[key_field]:
            out["below_ratio"] = 0.0
            return out
    if pct_field in present:
        out["below_ratio"] = _PCT(v[pct_field])
    return out


def to_yaml(values: Dict[str, Any], buffs: Optional[List[Dict[str, Any]]],
            profile_name: str) -> Tuple[dict, dict]:
    """UI 欄位 -> (local.yaml 內容, profile.yaml 內容)。只寫 values 裡有的欄位。"""
    v = coerce(values)
    present = set(values)
    local = _build(LOCAL_MAP, v, present)
    prof = _build(PROFILE_MAP, v, present)
    prof["name"] = profile_name
    if buffs is not None:
        prof["buffs"] = buffs

    potions = {}
    for kind, key_field, pct_field in (("hp", "hp_key", "hp_below"),
                                       ("mp", "mp_key", "mp_below")):
        pot = _potion(v, present, key_field, pct_field)
        if pot:
            potions[kind] = pot
    if potions:
        prof["potions"] = potions
    if "panic_return_key" in present:
        # 清空也要寫：留空 = 停機時直接停，不先按回城卷
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


# ---------------------------------------------------------------------------
# 只寫改過的欄位
# ---------------------------------------------------------------------------

# 藥水的鍵與門檻是同一個 potions.hp 區塊的兩半，改一半另一半也要一起送，
# 驗證時也要一起當成「改過」（填了鍵、門檻還是 0 的話藥永遠不會喝）
COUPLED: Dict[str, str] = {
    "hp_key": "hp_below", "hp_below": "hp_key",
    "mp_key": "mp_below", "mp_below": "mp_key",
}


def with_coupled(fields: Iterable[str]) -> List[str]:
    """fields 加上 COUPLED 的另一半，保持原順序、不重複。"""
    out: List[str] = []
    for f in fields:
        for g in (f, COUPLED.get(f)):
            if g and g not in out:
                out.append(g)
    return out


def _kind(field: str) -> str:
    spec = SPECS.get(field)
    return spec.kind if spec else "text"


def normalize(field: str, raw: Any) -> str:
    """比較用的標準字串：'0.60'、0.6、'0.6' 都是 '0.6'。

    數字欄位轉 float 再格式化；按鍵去空白轉小寫（載入器本來就會轉小寫）；
    bool 是 'true' / 'false'；其他字串去頭尾空白。轉不動的數字保留原字串，
    所以會被當成「有改」，交給 validate 擋下。
    """
    if raw is None:
        raw = ""
    typ = FIELDS.get(field, ("", str))[1]
    if typ is bool:
        if isinstance(raw, bool):
            return "true" if raw else "false"
        return "true" if str(raw).strip().lower() in ("1", "true", "yes", "on") else "false"
    text = str(raw).strip()
    if typ in (int, float):
        try:
            return _fmt(float(text))
        except ValueError:
            return text
    if _kind(field) == "key":
        return text.lower()
    return text


def changed_fields(baseline: Dict[str, str], values: Dict[str, Any]) -> List[str]:
    """values 裡跟基準不同的欄位（FIELDS 的順序）。兩邊都會再 normalize 一次。"""
    return [f for f in FIELDS if f in values and
            (f not in baseline or normalize(f, values[f]) != normalize(f, baseline[f]))]


def _norm_rows(rows) -> List[Tuple[str, str]]:
    out = []
    for key, secs in rows:
        s = str(secs if secs is not None else "").strip()
        try:
            s = _fmt(float(s))
        except ValueError:
            pass
        out.append((str(key if key is not None else "").strip().lower(), s))
    return out + [("", "")] * (BUFF_SLOTS - len(out))


def buff_rows_changed(a, b) -> bool:
    """兩組 buff 列是否不同。兩邊都補到 8 列、秒數 '120.0' 與 '120' 視為相同。"""
    return _norm_rows(a) != _norm_rows(b)


# ---------------------------------------------------------------------------
# 欄位規格表
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Spec:
    """一個 UI 欄位的標籤、單位、範圍與說明。表單、驗證、tooltip 都讀這張表。

    kind: key | choice | int | float | bool | text | waypoints
    """
    label: str
    unit: str = ""
    lo: Optional[float] = None
    hi: Optional[float] = None
    kind: str = "text"
    help: str = ""


_KEY_HELP = "點一下再按遊戲裡的鍵；Esc 取消、右鍵清空"

SPECS: Dict[str, Spec] = {
    "window_title": Spec(
        "遊戲視窗", kind="text",
        help="標題含這段字的第一個視窗就是遊戲。太短（例如 maple）可能抓到別的視窗"),
    "attack_key": Spec(
        "攻擊鍵", kind="key", help="打怪用的技能鍵。" + _KEY_HELP),
    "attack_type": Spec(
        "攻擊方式", kind="choice",
        help="面向攻擊：先轉向怪物那一側再打（大多數職業）。"
             "原地範圍：不轉身直接放（祭司、主教的範圍技）"),
    "attack_seconds": Spec(
        "按住時間", "秒", 0.05, 5, "float",
        "每次攻擊按住技能鍵多久。太短技能放不出來，太長會拖慢下一次判斷"),
    "attack_repeat": Spec(
        "連發", "下", 1, 10, "int", "一次判斷連續放幾下。怪血厚就調高"),
    "attack_range": Spec(
        "水平距離", "px", 10, 2000, "int",
        "怪在左右這麼多 px 內才打。以 790 px 寬的 playfield 為準，視窗比較大時"
        "自動等比放大——1366 寬的視窗填 320 是對的，不用自己換算。"
        "量法：辨識自檢圖上的青色框"),
    "attack_vrange": Spec(
        "垂直距離", "px", 0, 600, "int",
        "怪在上下這麼多 px 內才打，擋掉樓上樓下打不到的怪。"
        "跟水平距離一樣以 790 px 寬的 playfield 為準自動縮放"),
    "jump_key": Spec(
        "跳躍鍵", kind="key",
        help="卡住時跳一下脫困、助跑跳抓繩、往下跳平台都用這個鍵。" + _KEY_HELP),
    "climb_up_key": Spec("上爬鍵", kind="key", help="爬繩往上。" + _KEY_HELP),
    "climb_down_key": Spec("下爬鍵", kind="key", help="爬繩往下。" + _KEY_HELP),
    "loot_key": Spec(
        "撿物鍵", kind="key", help="打完怪撿掉落物。留空 = 不撿。" + _KEY_HELP),
    "panic_return_key": Spec(
        "停機時回城卷", kind="key",
        help="停機前先按這個鍵回城，角色才不會站在怪堆裡繼續被打。"
             "留空 = 直接停機。" + _KEY_HELP),
    "waypoints": Spec(
        "巡邏點", kind="waypoints",
        help="auto = 開場往左右走到撞牆，自己量巡邏範圍（探邊）。"
             "或寫小地圖 x 座標：30, 90；多層地圖用「錄製路線」產生清單。"
             "≤ 1 的值是小地圖寬度的比例"),
    "hp_key": Spec(
        "HP 藥水", kind="key",
        help="HP 低於喝藥線就按這個鍵。留空 = 不自動喝。" + _KEY_HELP),
    "hp_below": Spec(
        "HP 喝藥線", "%", 1, 99, "int",
        "HP 低於這個百分比就喝。要高於停機線，不然每次都要先掉進停機線才開始喝，"
        "搶救時間只剩幾秒"),
    "mp_key": Spec(
        "MP 藥水", kind="key",
        help="MP 低於喝藥線就按這個鍵。留空 = 不自動喝。" + _KEY_HELP),
    "mp_below": Spec(
        "MP 喝藥線", "%", 1, 99, "int", "MP 低於這個百分比就喝"),
    "critical_hp": Spec(
        "停機線", "%", 0, 90, "int",
        "HP 連續低於這條線、補血也拉不回來就停機（結束整場，不能繼續）。"
        "低於這條線時還是會先灌藥搶救"),
    "critical_hp_frames": Spec(
        "低血幀數", "幀", 1, 60, "int",
        "要連續這麼多幀都讀到低於停機線才算數，免得血條被特效蓋住一幀就誤判"),
    "exp_stall_minutes": Spec(
        "沒經驗進帳", "分鐘", 0, 600, "float",
        "這麼多分鐘經驗都沒漲就暫停（不是停機，按暫停熱鍵可以繼續）。0 = 不檢查"),
    "max_runtime_minutes": Spec(
        "最長掛機", "分鐘", 0, 2880, "float",
        "跑滿這麼多分鐘就自己收工。0 = 不限。睡前開的 bot 不該一直跑到白天"),
    "pause_when_players": Spec(
        "小地圖有其他玩家時暫停", kind="bool",
        help="小地圖上出現其他玩家就暫停動作，人離開後自動繼續"),
    "sound_alerts": Spec(
        "危險事件嗶聲", kind="bool", help="停機、暫停、黑屏這類事件用嗶聲提醒（Windows）"),
    "fps": Spec(
        "迴圈頻率", "次/秒", 1, 60, "float",
        "每秒看幾次畫面、做幾次判斷。8 就夠用；調高吃 CPU，偵測跟不上時也不會真的變快"),
    "outline_black_level": Spec(
        "描邊黑度", "", 1, 80, "int",
        "抓不到怪 → 調高；框到背景 → 調低（8→4）。即時擷取一般 0~8、JPEG 截圖 12~20，"
        "有些客戶端的描邊不是純黑，要 16~32 才抓得到"),
    "outline_min_area": Spec(
        "最小面積", "px²", 10, 50000, "int",
        "比這小的黑框不當成怪物。以 790 px 寬的 playfield 為準自動縮放"),
    "mob_interval": Spec(
        "偵測間隔", "秒", 0, 10, "float",
        "每隔幾秒才重新找一次怪，中間沿用上次的框。0 = 每次都偵測；大視窗偵測太慢時才調"),
    "filter_followers": Spec(
        "排除跟隨物（需鏡頭會捲動）", kind="bool",
        help="跟著角色移動、不是怪物的東西（實務上是寵物）。要鏡頭會捲動的地圖才分得出來"),
}

BUFF_HELP = ("每隔這麼多秒按一次這個鍵補 buff。施放期間不打怪，掛在繩上時也不放；"
             "沒綁鍵的那組會略過。" + _KEY_HELP)

CHOICES: Dict[str, List[Tuple[str, str]]] = {
    "attack_type": [("directional", "面向攻擊（先轉身再打）"),
                    ("aoe", "原地範圍（不用轉身）")],
}

# (分頁, 群組, 欄位)。同一群組只放同一個 scope 的欄位，標章才不會標錯。
# 準備分頁只有 window_title，由 view 畫在第 1 步；buff 不是 FIELDS，
# 由 view 畫在「巡邏與 buff」分頁的「循環 buff」群組。
GROUPS: List[Tuple[str, str, List[str]]] = [
    ("準備", "遊戲視窗", ["window_title"]),
    ("打怪", "攻擊", ["attack_key", "attack_type", "attack_seconds", "attack_repeat",
                     "attack_range", "attack_vrange"]),
    ("打怪", "移動與撿物", ["jump_key", "climb_up_key", "climb_down_key", "loot_key",
                          "panic_return_key"]),
    ("巡邏與 buff", "巡邏點", ["waypoints"]),
    ("藥水與安全", "藥水", ["hp_key", "hp_below", "mp_key", "mp_below"]),
    ("藥水與安全", "停機與暫停", ["critical_hp", "critical_hp_frames", "exp_stall_minutes",
                              "max_runtime_minutes", "pause_when_players",
                              "sound_alerts"]),
    ("辨識", "辨識", ["fps", "outline_black_level", "outline_min_area", "mob_interval",
                    "filter_followers"]),
]
BUFF_TAB = "巡邏與 buff"

KEY_FIELDS: List[str] = [f for f in FIELDS if SPECS[f].kind == "key"]



def _form_order() -> List[str]:
    out: List[str] = []
    for _tab, _group, fields in GROUPS:
        out += fields
        if "waypoints" in fields:
            out += [f"buff{i}" for i in range(1, BUFF_SLOTS + 1)]
    return out


# 表單由上到下的順序（buff 在巡邏點後面），「前往第一個錯誤」照這個走
FORM_ORDER: List[str] = _form_order()


def scope(field: str) -> str:
    """'config'（這台電腦，local.yaml）或 'profile'（這張地圖）。

    直接由 LOCAL_MAP 推出來，群組標章就不可能跟實際寫入的檔案對不上。
    """
    return "config" if field in LOCAL_FIELDS else "profile"


def tab_of(key: str) -> str:
    """欄位名或 'buff1'..'buff8' / 'buffs' 所在的分頁標題。"""
    if key.startswith("buff"):
        return BUFF_TAB
    for tab, _group, fields in GROUPS:
        if key in fields:
            return tab
    raise KeyError(key)


def display(field: str, value: Any) -> str:
    """人看的值：數字 '320'、'0.6'；選項顯示中文標籤；bool 是 開／關。

    數字格式跟 normalize 相同，填回欄位再比對不會變成「已修改」。
    """
    kind = _kind(field)
    if kind == "bool":
        return "開" if normalize(field, value) == "true" else "關"
    if kind in ("int", "float"):
        return normalize(field, value)
    text = "" if value is None else str(value).strip()
    if kind == "choice":
        return dict(CHOICES.get(field, [])).get(text, text)
    return text


def _fmt_range(spec: Spec) -> str:
    return f"{_fmt(float(spec.lo))}–{_fmt(float(spec.hi))}"


def tooltip_text(field: str, baseline_value: Any = None,
                 problem: Optional[Problem] = None) -> str:
    """tooltip 內容：說明、範圍、目前的問題、（有改時）檔案裡的值。

    baseline_value 只在欄位已修改時傳，None = 沒改。
    """
    spec = SPECS.get(field)
    lines = [spec.help if spec else BUFF_HELP]
    if spec and spec.lo is not None and spec.hi is not None:
        lines.append(f"範圍 {_fmt_range(spec)}" + (f" {spec.unit}" if spec.unit else ""))
    if problem is not None:
        lines.append(problem.text)
    if baseline_value is not None:
        shown = display(field, baseline_value) if spec else str(baseline_value).strip()
        lines.append(f"已修改（檔案裡是 {shown}）" if shown else "已修改（檔案裡是空的）")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 存檔前驗證
# ---------------------------------------------------------------------------

DEFAULT_HOTKEYS = {"f9": "暫停", "f12": "停止"}
_HOTKEY_EFFECT = {"暫停": "把自己暫停", "停止": "把自己停掉"}

_REQUIRED = {
    "attack_key": "攻擊鍵沒綁——角色只會走、不會打",
    "window_title": "要選遊戲視窗",
}


def _put(out: Dict[str, Problem], key: str, p: Problem) -> None:
    """同一格只留一個問題；錯誤蓋過警告，先到的同級問題優先。"""
    old = out.get(key)
    if old is None or (old.level != "error" and p.level == "error"):
        out[key] = p


def _parse_number(field: str, raw: Any) -> Optional[float]:
    kind = SPECS[field].kind
    try:
        x = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or (kind == "int" and not x.is_integer()):
        return None
    return x


def _number_problem(field: str, raw: Any, strict: bool) -> Optional[Problem]:
    spec = SPECS[field]
    x = _parse_number(field, raw)
    if x is not None and spec.lo <= x <= spec.hi:
        return None
    shown = str(raw).strip()
    if strict:
        word = "整數" if spec.kind == "int" else "數字"
        return Problem("error", f"{spec.label}要填 {_fmt_range(spec)} 的{word}（現在是「{shown}」）")
    # 沒動過、從檔案讀進來的值：舊地圖照樣能跑，只提醒
    return Problem("warn", f"{spec.label}一般是 {_fmt_range(spec)}（檔案裡是「{shown}」）")


def _key(v: Any) -> str:
    return "" if v is None else str(v).strip().lower()


def key_conflicts(values: Dict[str, Any], buff_rows,
                  hotkeys: Optional[Dict[str, str]] = None) -> Dict[str, Problem]:
    """bot 要按的鍵撞到熱鍵 -> 錯誤；兩個動作綁同一個鍵 -> 兩邊都警告。

    安全熱鍵是用 GetAsyncKeyState 輪詢的，它也看得到 bot 自己送出的按鍵：
    buff 綁 F12 等於每次補 buff 就把自己停掉。這是唯一一道熱鍵把關，
    按鍵擷取時不另外擋。
    """
    hotkeys = DEFAULT_HOTKEYS if hotkeys is None else {
        _key(k): name for k, name in hotkeys.items()}
    roles: List[Tuple[str, str, str]] = []           # (問題 key, 顯示名, 鍵)
    for f in KEY_FIELDS:
        k = _key(values.get(f))
        if k:
            roles.append((f, SPECS[f].label, k))
    for i, (k, _secs) in enumerate(list(buff_rows)[:BUFF_SLOTS], 1):
        k = _key(k)
        if k:
            roles.append((f"buff{i}", f"buff {i}", k))

    out: Dict[str, Problem] = {}
    for slot, _label, k in roles:
        name = hotkeys.get(k)
        if name:
            effect = _HOTKEY_EFFECT.get(name, f"觸發{name}")
            _put(out, slot, Problem(
                "error", f"{k.upper()} 是{name}熱鍵，bot 按下去就會{effect}——換一個鍵"))
    for slot, label, k in roles:
        others = [lb for s, lb, kk in roles if kk == k and s != slot]
        if others:
            names = "「" + "」「".join(others) + "」"
            _put(out, slot, Problem(
                "warn", f"「{label}」跟{names}都綁 {k}——遊戲裡同一個鍵只能做一件事"))
    return out


def validate(values: Dict[str, Any], buff_rows, *,
             changed: Optional[Iterable[str]] = None,
             hotkeys: Optional[Dict[str, str]] = None) -> Dict[str, Problem]:
    """存檔／開始前的檢查。回傳 {欄位名或 'buff1'..'buff8': Problem}，沒問題是 {}。

    changed=None 表示全部從嚴。有給 changed 時，超出範圍只有在改過的欄位
    （含 COUPLED 的另一半、'buffs'）才是錯誤；沒動過的是警告——repo 裡
    既有的地圖可能本來就超出新的範圍，不能因此開不了。
    必填、選項、巡邏點、喝藥線不高於停機線（跟 doctor 的 FAIL 一致）、
    按鍵撞到熱鍵，則不論有沒有改都是錯誤。
    """
    strict_all = changed is None
    touched = set() if changed is None else set(with_coupled(changed))
    out: Dict[str, Problem] = {}

    def strict(f: str) -> bool:
        return strict_all or f in touched

    for f, msg in _REQUIRED.items():
        if f in values and not str(values[f] or "").strip():
            _put(out, f, Problem("error", msg))

    for f in FIELDS:
        if SPECS[f].kind not in ("int", "float") or f not in values:
            continue
        if f in ("hp_below", "mp_below") and not _key(values.get(COUPLED[f])):
            continue                                  # 沒綁藥水鍵，門檻用不到
        p = _number_problem(f, values[f], strict(f))
        if p:
            _put(out, f, p)

    for f, options in CHOICES.items():
        if f in values and str(values[f]).strip() not in dict(options):
            allowed = "、".join(label for _v, label in options)
            _put(out, f, Problem("error", f"{SPECS[f].label}只能選：{allowed}"))

    if "waypoints" in values:
        _value, err = parse_waypoints_strict(str(values["waypoints"] or ""))
        if err:
            _put(out, "waypoints", Problem("error", err))

    # 喝藥線不高於停機線：每次都要掉進停機線才開始喝，只剩停機前那幾秒能搶救
    #（doctor.check_profile 的 FAIL）
    if _key(values.get("hp_key")):
        h = _parse_number("hp_below", values.get("hp_below"))
        c = _parse_number("critical_hp", values.get("critical_hp"))
        if h is not None and c is not None and 0 < h <= c:
            msg = (f"喝藥門檻 {_fmt(h)}% 要高於停機線 {_fmt(c)}%"
                   "——不然每次都要先掉進停機線才開始喝，搶救時間只剩幾秒")
            _put(out, "hp_below", Problem("error", msg))
            _put(out, "critical_hp", Problem("error", msg))

    buffs_strict = strict_all or "buffs" in touched
    for i, (k, secs) in enumerate(list(buff_rows)[:BUFF_SLOTS], 1):
        k = _key(k)
        s_text = str(secs if secs is not None else "").strip()
        try:
            s = float(s_text) if s_text else None
        except ValueError:
            s = None
        if s is not None and not math.isfinite(s):
            s = None
        slot = f"buff{i}"
        if k and (s is None or s <= 0):
            level = "error" if buffs_strict else "warn"
            _put(out, slot, Problem(level, f"buff {i}：要填間隔秒數，不然不會施放"))
        elif not k and s_text:
            _put(out, slot, Problem("warn", f"buff {i}：沒綁鍵，這組會略過"))
        elif k and s is not None and s < 5:
            _put(out, slot, Problem(
                "warn", f"buff {i}：間隔不到 5 秒，放 buff 時不打怪，幾乎沒空打怪"))

    # 送不出去的鍵名（手改 YAML 打錯字、或按鍵擷取被打斷留下的提示字）要在存檔前
    # 擋下：存進去的話預演看不出來，正式跑第一次按鍵就 KeyError 整個停掉
    named = [(f, values.get(f)) for f in KEY_FIELDS if f in values]
    named += [(f"buff{i}", k) for i, (k, _s) in enumerate(list(buff_rows)[:BUFF_SLOTS], 1)]
    for slot, k in named:
        k = _key(k)
        if k and k not in SCANCODES:
            label = SPECS[slot].label if slot in SPECS else slot.replace("buff", "buff ")
            _put(out, slot, Problem("error", f"{label}「{k}」不是送得進遊戲的按鍵——點一下欄位重新按一次"))

    for slot, p in key_conflicts(values, buff_rows, hotkeys).items():
        _put(out, slot, p)
    return out


def first_error(problems: Dict[str, Problem]) -> Optional[str]:
    """表單由上到下第一個錯誤的欄位名（或 'buffN'）；沒有錯誤回 None。"""
    for key in FORM_ORDER:
        p = problems.get(key)
        if p is not None and p.level == "error":
            return key
    for key, p in problems.items():               # 不在表單上的 key 排最後
        if p.level == "error":
            return key
    return None


# ---------------------------------------------------------------------------
# 範本與檔名
# ---------------------------------------------------------------------------

# 大家共用的範例檔。存檔直接改進去的話，下次 git pull 會衝突，別人拿到的
# 範例也變成你的設定（README 以前專門警告過這個陷阱）
TEMPLATE_NAMES = ("example.yaml", "multilevel.yaml")
TEMPLATE_PREFIX = "範本-"


def _basename(path: str) -> str:
    # Windows 路徑在 Linux 上跑測試時 os.path.basename 不認反斜線
    return re.split(r"[\\/]", path)[-1]


def is_template(path: str) -> bool:
    name = _basename(path)
    return name.lower() in TEMPLATE_NAMES or name.startswith(TEMPLATE_PREFIX)


_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f\s]+')


def profile_filename(name: str, folder: str, exists=os.path.exists) -> str:
    """地圖名稱 -> folder 底下還沒被用掉的 .yaml 路徑。

    Windows 不允許的字元與空白換成 '-'，中文照留；已存在就接 -2、-3。
    空白名稱或會被當成範本的名稱丟 ValueError。
    """
    stem = re.sub(r"\.ya?ml$", "", (name or "").strip(), flags=re.IGNORECASE)
    stem = _ILLEGAL.sub("-", stem).strip("-. ")
    if not stem:
        raise ValueError("地圖名稱不能是空的")
    if is_template(stem + ".yaml"):
        raise ValueError(f"「{stem}」是範本用的名稱，換一個名字")
    path = os.path.join(folder, stem + ".yaml")
    n = 2
    while exists(path):
        path = os.path.join(folder, f"{stem}-{n}.yaml")
        n += 1
    return path


def _yaml_name(path: str, stem: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return stem
    name = data.get("name") if isinstance(data, dict) else None
    return str(name).strip() if name is not None and str(name).strip() else stem


def profile_choices(folder: str) -> List[Tuple[str, str]]:
    """地圖選單：[(顯示名稱, 路徑)]，自己的地圖在前、範本在後並標「（範本）」。

    名稱取 YAML 的 name，讀不到就用檔名；同名的接上檔名分辨。
    """
    try:
        files = sorted(f for f in os.listdir(folder) if f.endswith((".yaml", ".yml")))
    except OSError:
        return []
    items = []
    for f in files:
        path = os.path.join(folder, f)
        stem = os.path.splitext(f)[0]
        items.append((_yaml_name(path, stem), stem, path, is_template(f)))
    counts: Dict[str, int] = {}
    for name, *_ in items:
        counts[name] = counts.get(name, 0) + 1
    out = []
    for want_template in (False, True):
        for name, stem, path, tmpl in items:
            if tmpl != want_template:
                continue
            label = name if counts[name] == 1 or name == stem else f"{name}（{stem}）"
            out.append((label + ("（範本）" if tmpl else ""), path))
    return out


# ---------------------------------------------------------------------------
# 改數值時保留 YAML 註解
# ---------------------------------------------------------------------------

_MISSING = object()


def _leaves(patch: dict, prefix=()):
    for k, v in patch.items():
        if isinstance(v, dict):
            yield from _leaves(v, prefix + (k,))
        else:
            yield prefix + (k,), v


def _lookup(data: Any, path: Sequence[str]) -> Any:
    for k in path:
        if not isinstance(data, dict) or k not in data:
            return _MISSING
        data = data[k]
    return data


def _scalar_text(value: Any) -> Optional[str]:
    """單一純量的 YAML 寫法（'' 會加引號、'8' 保持字串）；清單或 dict 回 None。"""
    if isinstance(value, (dict, list, tuple)):
        return None
    s = yaml.safe_dump(value, allow_unicode=True, width=1 << 16)
    if s.endswith("\n...\n"):
        s = s[:-len("\n...\n")]
    s = s.rstrip("\n")
    return None if "\n" in s else s


def _content(line: str) -> str:
    return line.rstrip("\r\n")


def _indent_of(content: str) -> int:
    return len(content) - len(content.lstrip(" "))


def _is_skip(content: str) -> bool:
    s = content.strip()
    return not s or s.startswith("#")


def _key_rest(content: str, key: str) -> Optional[str]:
    """這行是 `key:` 的話回傳冒號後面的部分，否則 None。"""
    s = content.lstrip(" ")
    head = key + ":"
    if not s.startswith(head):
        return None
    rest = s[len(head):]
    return rest if (not rest or rest[0] in " \t") else None


def _find_key_line(lines: List[str], path: Sequence[str]) -> Optional[int]:
    """照 path 一層層找 block 風格的 `key:` 行；flow 風格、找不到、重複都回 None。"""
    start, end = 0, len(lines)
    for depth, key in enumerate(path):
        child = None
        hit = None
        for i in range(start, end):
            c = _content(lines[i])
            if _is_skip(c):
                continue
            ind = _indent_of(c)
            if child is None:
                child = ind
            if ind != child or _key_rest(c, key) is None:
                continue
            if hit is not None:
                return None                         # 重複的 key：不猜
            hit = i
        if hit is None:
            return None
        if depth == len(path) - 1:
            return hit
        rest = _key_rest(_content(lines[hit]), key).strip()
        if rest and not rest.startswith("#"):
            return None                             # `potions: {hp: ...}` 這種 flow 風格
        start, end = hit + 1, hit + 1
        while end < len(lines):
            c = _content(lines[end])
            if not _is_skip(c) and _indent_of(c) <= child:
                break
            end += 1
    return None


def _split_value(body: str) -> Optional[Tuple[str, str]]:
    """'0.6   # 註解' -> ('0.6', '   # 註解')；引號沒收尾回 None。"""
    if body[0] in "'\"":
        q, i = body[0], 1
        while i < len(body):
            if q == '"' and body[i] == "\\":
                i += 2
                continue
            if body[i] == q:
                if q == "'" and body[i + 1:i + 2] == "'":
                    i += 2
                    continue
                break
            i += 1
        else:
            return None
        value = body[:i + 1]
    else:
        m = re.search(r"[ \t]#", body)
        value = body[:m.start() if m else len(body)].rstrip()
    return value, body[len(value):]


def _replace_value(line: str, key: str, new: str) -> Optional[str]:
    c = _content(line)
    eol = line[len(c):]
    indent = c[:_indent_of(c)]
    rest = _key_rest(c, key)
    body = rest.lstrip(" \t")
    gap = rest[:len(rest) - len(body)]
    if not body or body[0] in "#[{|>&*!":
        return None                                 # 值在下一行、flow 集合、區塊字串、錨點
    split = _split_value(body)
    if split is None:
        return None
    old, tail = split
    if tail:
        # 盡量讓行尾註解留在原本那一欄
        comment = tail.lstrip(" \t")
        pad = max(1, len(tail) - len(comment) + len(old) - len(new))
        tail = " " * pad + comment
    return f"{indent}{key}:{gap}{new}{tail}{eol}"


def _block_end(lines: List[str], idx: int) -> int:
    """`key:` 那行底下屬於它的區塊到哪一行為止（不含）。

    YAML 的清單可以跟 key 同一層縮排（`  waypoints:` 底下接 `  - x: 24`），
    所以同縮排但以 '- ' 開頭的也算。最後一個項目後面、縮排更深的註解是那個
    項目的說明，一起帶走；空行和縮排更淺的註解留給下一個 key。
    """
    key_ind = _indent_of(_content(lines[idx]))
    last = idx
    j = idx + 1
    while j < len(lines):
        c = _content(lines[j])
        if _is_skip(c):
            j += 1
            continue
        s = c.lstrip(" ")
        if _indent_of(c) > key_ind or (_indent_of(c) == key_ind
                                        and (s == "-" or s.startswith("- "))):
            last = j
            j += 1
            continue
        break
    j = last + 1
    while j < len(lines):
        c = _content(lines[j])
        if not c.strip() or not c.strip().startswith("#") or _indent_of(c) <= key_ind:
            break
        last = j
        j += 1
    return last + 1


def _item_text(item: Any) -> Optional[str]:
    if isinstance(item, (dict, list)):
        s = yaml.safe_dump(item, default_flow_style=True, allow_unicode=True,
                           sort_keys=False, width=1 << 16).strip()
        return None if "\n" in s else s
    return _scalar_text(item)


def _same_line_value(body: str) -> Optional[Tuple[str, str]]:
    """key 同一行的值與行尾註解：'auto  # x' 或 '[30, 90]  # x'。"""
    if body.startswith("["):
        end = body.find("]")
        if end < 0:
            return None
        return body[:end + 1], body[end + 1:]
    if body[0] in "{|>&*!":
        return None
    return _split_value(body)


def _replace_node(lines: List[str], idx: int, key: str, value: Any) -> Optional[List[str]]:
    """把 `key:` 的值換成 value，回傳新的行；做不到回 None。

    純量換純量只改那一行。值是清單（巡邏點、buff）時只換掉那個區塊——
    以前清單一律整份重寫，錄一次路線就把檔案其他地方的實測註解全部洗掉。
    """
    c = _content(lines[idx])
    eol = lines[idx][len(c):] or "\n"
    indent = c[:_indent_of(c)]
    rest = _key_rest(c, key)
    body = rest.lstrip(" \t")
    if not isinstance(value, (list, tuple)):
        if body and not body.startswith("#"):
            line = _replace_value(lines[idx], key, _scalar_text(value) or "")
            return None if line is None or _scalar_text(value) is None \
                else lines[:idx] + [line] + lines[idx + 1:]
        new = _scalar_text(value)                   # 區塊（清單）換回純量，例如 auto
        if new is None:
            return None
        tail = (" " + body) if body else ""
        end = _block_end(lines, idx)
        return lines[:idx] + [f"{indent}{key}: {new}{tail}{eol}"] + lines[end:]
    items = [_item_text(v) for v in value]
    if any(t is None for t in items):
        return None
    if body and not body.startswith("#"):
        split = _same_line_value(body)
        if split is None:
            return None
        comment = split[1].strip()
        end = idx + 1
    else:
        comment = body.strip()
        end = _block_end(lines, idx)
    tail = ("  " + comment) if comment else ""
    if not items:
        return lines[:idx] + [f"{indent}{key}: []{tail}{eol}"] + lines[end:]
    block = [f"{indent}  - {t}{eol}" for t in items]
    return lines[:idx] + [f"{indent}{key}:{tail}{eol}"] + block + lines[end:]


def patch_scalars(text: str, patch: dict) -> Optional[str]:
    """只改 patch 裡那幾個純量的值，其餘（尤其是註解）一個字都不動。

    repo 裡的 profile 用註解記錄實測數據（例如 1366x768 那份的繩點調整過程），
    merge_into 走 safe_dump 會把它們全部洗掉。清單值（巡邏點、buff）只換掉
    那一個區塊，區塊裡的註解會跟著舊清單一起走。做不到就回 None 讓呼叫端改用
    merge_into：flow 風格的區段、檔案裡沒有的 key 都算。改完一定重新 safe_load，必須跟 _deep_merge(原本, patch)
    完全相等才算數——任何沒想到的 YAML 寫法都只會退回舊路，不會寫壞檔案。
    """
    try:
        original = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError:
        return None
    if not isinstance(original, dict):
        return None
    lines = text.splitlines(keepends=True)
    for path, value in _leaves(patch):
        if _lookup(original, path) == value:
            continue                                # 值沒變（例如每次都帶的 name）就不碰
        idx = _find_key_line(lines, path)
        if idx is None:
            return None
        lines = _replace_node(lines, idx, path[-1], value)
        if lines is None:
            return None
    result = "".join(lines)
    try:
        ok = yaml.safe_load(result) == _deep_merge(original, patch)
    except yaml.YAMLError:
        return None
    return result if ok else None


def _backup(path: str, backup_once: set) -> Optional[str]:
    """註解要掉之前留一份 '<檔名>.bak'。已經有就不蓋：那份才是還有註解的原檔，
    下一次開控制台再存一次的話，蓋過去的會是已經沒註解的版本。

    回傳 .bak 路徑給呼叫端寫日誌；同一個 session 只回報一次。
    """
    key = os.path.normcase(os.path.abspath(path))
    if key in backup_once:
        return None
    backup_once.add(key)
    bak = path + ".bak"
    if not os.path.exists(bak):
        shutil.copyfile(path, bak)
    return bak


def _comment_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if "#" in line)


def write_patch(path: str, patch: dict, backup_once: set) -> Optional[str]:
    """把 patch 寫進 path：能就地改就保留註解，否則退回 merge_into。

    只要這次寫入會少掉註解（清單區塊換掉、或整份重寫），先留一份
    '<檔名>.bak'（見 _backup）。回傳 .bak 路徑讓呼叫端寫進日誌，沒有就回 None。
    patch 是空的就完全不碰檔案（不會因此建出空的 local.yaml）。
    """
    if not patch:
        return None
    made = None
    if os.path.exists(path):
        # newline='' 保留原本的換行字元：只改一行就不該把整個檔案換成 CRLF
        with open(path, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        new = patch_scalars(text, patch)
        if new is not None:
            if new != text:
                if _comment_lines(new) < _comment_lines(text):
                    made = _backup(path, backup_once)
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(new)
            return made
        if _comment_lines(text):
            made = _backup(path, backup_once)
    merge_into(path, patch)
    return made
