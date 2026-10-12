"""準備分頁：第一次使用的 7 步清單、開跑前把關、控制台自己的狀態檔。不碰 tkinter。

清單的每一步「做完了沒」都由 SetupFacts 推出來，SetupFacts 的 I/O（找視窗、
看檔案在不在、讀 gui_state.json）全在 Controller.facts()。這裡只剩判斷，
所以「校正改過了，自檢要重做」這種規則可以直接測。

gui_state.json 放的是控制台自己記的事（上次開的地圖、視窗位置、「辨識
自檢看過了」），跟 Config / Profile 都不同：local.yaml 會被校正工具用
safe_dump 整份重寫，塞在那裡遲早被洗掉。
"""
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import settings
from .live import STATUS_GLYPH, STATUS_TONE, gui_text, problem_for, tk_safe

STATE_NAME = "gui_state.json"
DEFAULT_CONFIG = os.path.join("config", "default.yaml")
DEFAULT_PROFILE = "example.yaml"

# 準備清單「預演一次」要跑滿這麼多 tick 才算數：開場自檢通過不代表決策
# 正常，跑個幾秒（8 FPS 約 5 秒）才看得出會不會一直讓過
DRY_OK_TICKS = 40


def _defaults() -> Dict[str, Any]:
    return {
        "last_profile": "",
        "geometry": "",          # 'WxH+X+Y'；還原前要先過 look.fits()
        "last_tab": "",
        "vision_ok": {},         # {"fp": regions 指紋, "at": time.time()}
        "dry_ok": {},            # {profile 檔名: regions 指紋}
    }


def state_path(config_path: str) -> str:
    """狀態檔放在全域設定旁邊（config/gui_state.json，已被 config/*.json 忽略）。"""
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), STATE_NAME)


def load_state(path: str) -> Dict[str, Any]:
    """讀狀態檔；不存在、壞掉、型別不對的鍵一律用預設值，絕不丟例外。"""
    out = _defaults()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return out
    if not isinstance(data, dict):
        return out
    for key, default in out.items():
        if key in data and isinstance(data[key], type(default)):
            out[key] = data[key]
    return out


def save_state(path: str, data: Dict[str, Any]) -> bool:
    """原子寫入（同資料夾的暫存檔 + os.replace）：寫到一半當掉也不會留下半個 JSON。"""
    folder = os.path.dirname(os.path.abspath(path))
    tmp = None
    try:
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".gui_state.", suffix=".tmp", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except (OSError, TypeError, ValueError):
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False


def pick_profile(cli: Optional[str], state: Dict[str, Any], folder: str,
                 exists=os.path.isfile) -> str:
    """要開哪張地圖：命令列 > 上次開的（檔案還在的話）> 範例。

    以前一律開 example.yaml，按一次開始就把範例檔改成自己的設定
    （README 專門警告過的陷阱）。
    """
    if cli:
        return cli
    last = state.get("last_profile") or ""
    if last and exists(last):
        return last
    return os.path.join(folder, DEFAULT_PROFILE)


def resolve_launch(config_arg: Optional[str], profile_arg: Optional[str],
                   base_dir: str) -> Tuple[str, str]:
    """gui.py 的路徑解析，在 chdir 之前做。

    命令列給的路徑相對於啟動時的目錄；沒給的預設值相對於程式所在的資料夾。
    捷徑的「開始位置」指到別處時，相對路徑的 tools/… 全部找不到——所以
    gui.py 解析完會 chdir 到程式資料夾，而這之前的路徑都得先變成絕對路徑。
    """
    config = os.path.abspath(config_arg) if config_arg else os.path.join(base_dir, DEFAULT_CONFIG)
    state = load_state(state_path(config))
    folder = os.path.join(base_dir, "config", "profiles")
    profile = pick_profile(os.path.abspath(profile_arg) if profile_arg else "", state, folder)
    return config, os.path.abspath(profile)


def regions_fingerprint(regions: Dict[str, Any], calibrated_for) -> str:
    """校正結果的短指紋。自檢、預演都記這個：重新校正後指紋變了就要重做。"""
    data = {
        "regions": {k: (list(v) if isinstance(v, (list, tuple)) else v)
                    for k, v in (regions or {}).items()},
        "calibrated_for": list(calibrated_for) if calibrated_for else None,
    }
    blob = json.dumps(data, sort_keys=True, ensure_ascii=True).encode("ascii")
    return hashlib.sha1(blob).hexdigest()[:12]


def tool_outcome(before_mtime: Optional[float], after_mtime: Optional[float],
                 returncode: int) -> str:
    """截圖類工具的結果：'saved' | 'cancelled' | 'failed'。

    grab_template 在框選視窗按 c 也是正常結束（return code 0），只看 return
    code 會把「什麼都沒存」報成成功。所以看輸出檔的 mtime 有沒有變。
    """
    if returncode != 0:
        return "failed"
    if after_mtime is not None and after_mtime != before_mtime:
        return "saved"
    return "cancelled"


# ---------------------------------------------------------------------------
# 準備清單
# ---------------------------------------------------------------------------

GROUP_TITLES = {
    "machine": "這台電腦（換電腦或改視窗大小才要重做）",
    "map": "這張地圖（換地圖或換角色就換一份）",
}

# Step.state -> (圖示, tone)。只有第一個沒做完的步驟用 Primary 按鈕（Step.primary）
STEP_ICON = {
    "done": ("√", "ok"),
    "next": ("▶", "info"),
    "todo": ("○", "muted"),
    "warn": ("！", "warn"),
    "fail": ("×", "bad"),
    "busy": ("●", "tool"),
}

# 工具標籤 -> 哪一步在跑
_TOOL_STEP = {"校正": "calibrate", "截角色名牌": "nametag", "辨識自檢": "vision"}

# 實戰開始前沒做完會被點名的步驟，與沒做的後果
_BLOCKING = {
    "calibrate": "校正——region 沒框好，HP、小地圖都會讀錯",
    "nametag": "截角色名牌——沒有它可能把自己當成怪站著打",
    "vision": "辨識自檢——血條讀錯會莫名停機",
}

# 準備清單已經在講的檢查，環境檢查摘要就不再列一次
HIDDEN_CHECKS = frozenset({"遊戲視窗", "視窗大小 vs 校正值", "必要 ROI",
                           "window.calibrated_for", "平台", "巡邏路線", "角色名牌"})


@dataclass(frozen=True)
class SetupFacts:
    """判斷清單需要的事實。全部由 Controller.facts() 收集（含 I/O）。"""
    loaded: bool = True
    load_error: str = ""
    windows: bool = True                       # 這台是 Windows
    window_title: str = ""                     # 設定裡的 window_title
    window_found: str = ""                     # 比中的視窗完整標題；'' = 沒找到
    window_size: Optional[Tuple[int, int]] = None
    title_matches: Tuple[str, ...] = ()        # 標題含 window_title 的其他視窗
    calibrated_for: Optional[Tuple[int, int]] = None
    nametag: bool = False                      # 截過角色名牌
    regions_fp: str = ""                       # 現在的校正指紋
    vision_fp: str = ""                        # 辨識自檢確認過時的指紋
    profile_name: str = ""
    profile_file: str = ""
    template: bool = False
    waypoints: str = "auto"                    # 存檔裡的巡邏點（UI 寫法）
    dry_ok: bool = False                       # 這張地圖在現在的校正下預演過
    tool: str = ""                             # 正在跑的工具標籤


@dataclass(frozen=True)
class Step:
    """清單的一列。action 是按鈕的動作代號（app.py 用 {代號: callback} 分派）。

    id: window | calibrate | nametag | vision | profile | route | dry
    state: done | next | todo | warn | fail | busy
    """
    id: str
    n: int
    group: str            # 'machine' | 'map'，標題見 GROUP_TITLES
    title: str
    state: str
    detail: str
    button: str = ""      # '' = 沒有按鈕（第 1 步的控制項是視窗選單）
    action: Optional[str] = None
    primary: bool = False  # 第一個沒做完的步驟：按鈕用 Primary 樣式


def conflict_text(needle: str, matches) -> str:
    """多個視窗都比得中時的提醒；只有一個（或沒有）就是 ''。"""
    matches = list(matches)
    if len(matches) < 2:
        return ""
    return (f"有 {len(matches)} 個視窗標題都含「{needle}」，會抓第一個「{matches[0]}」。"
            "選完整一點的標題比較保險")


def _window_step(f: SetupFacts) -> Tuple[str, str]:
    if not f.windows:
        return "warn", "這台不是 Windows：抓不到遊戲視窗，只能用截圖離線測試"
    if not f.window_found:
        return "todo", "還沒找到遊戲視窗。先開遊戲（視窗模式），再從清單選它"
    conflict = conflict_text(f.window_title, f.title_matches)
    if conflict:
        return "warn", conflict
    w, h = f.window_size or (0, 0)
    return "done", f"找到「{f.window_found}」，client 區 {w}x{h}"


def _calibrate_step(f: SetupFacts) -> Tuple[str, str, str]:
    if not f.calibrated_for:
        return "todo", "還沒在這台電腦校正", "開始校正"
    cw, ch = f.calibrated_for
    if f.window_size and tuple(f.window_size) != tuple(f.calibrated_for):
        w, h = f.window_size
        return ("warn", f"遊戲視窗現在是 {w}x{h}，校正時是 {cw}x{ch}。開始時會試著把"
                        "視窗調回去；故意換大小就重新校正", "重新校正")
    return "done", f"已校正（{cw}x{ch}）", "重新校正"


def _vision_step(f: SetupFacts) -> Tuple[str, str, str]:
    if f.vision_fp and f.vision_fp == f.regions_fp:
        return "done", "確認過", "再看一次"
    if f.vision_fp:
        return "todo", "校正改過了，要再自檢一次", "自檢"
    return "todo", "拍一張遊戲畫面，確認血條、小地圖角色點、名牌、怪物框都對", "自檢"


def _route_step(f: SetupFacts) -> Tuple[str, str]:
    p = settings.describe_waypoints(f.waypoints)
    state = {"ok": "done", "info": "done", "warn": "warn"}.get(p.level, "fail")
    return state, p.text


def evaluate(f: SetupFacts) -> List[Step]:
    """事實 -> 7 個步驟。步驟從不硬鎖：先後關係寫在說明裡，按鈕永遠能按。"""
    steps: List[Step] = []

    state, detail = _window_step(f)
    steps.append(Step("window", 1, "machine", "遊戲視窗", state, detail, "", "window"))

    state, detail, button = _calibrate_step(f)
    steps.append(Step("calibrate", 2, "machine", "校正", state, detail, button, "calibrate"))

    if f.nametag:
        steps.append(Step("nametag", 3, "machine", "截角色名牌", "done",
                          "已截。換角色要重截", "重截", "nametag"))
    else:
        steps.append(Step("nametag", 3, "machine", "截角色名牌", "todo",
                          "還沒截。沒有名牌，程式不知道角色在哪，會把自己當成怪站著打",
                          "截名牌", "nametag"))

    state, detail, button = _vision_step(f)
    steps.append(Step("vision", 4, "machine", "辨識自檢", state, detail, button, "vision"))

    if not f.loaded:
        line = (f.load_error or "").strip().splitlines()
        steps.append(Step("profile", 5, "map", "我的地圖設定", "fail",
                          f"設定檔讀不起來：{line[0] if line else '原因看日誌'}",
                          "用記事本開啟", "notepad"))
    elif f.template:
        steps.append(Step("profile", 5, "map", "我的地圖設定", "warn",
                          f"現在開的是範本「{f.profile_name}」。範本不會被修改；"
                          "另存一份，之後的設定才存得進去", "另存…", "save_as"))
    else:
        steps.append(Step("profile", 5, "map", "我的地圖設定", "done",
                          f"{f.profile_name}（{f.profile_file}）"))

    state, detail = _route_step(f) if f.loaded else ("todo", "設定檔讀不起來")
    steps.append(Step("route", 6, "map", "巡邏路線", state, detail, "到巡邏分頁",
                      "tab:patrol"))

    if f.dry_ok:
        steps.append(Step("dry", 7, "map", "預演一次", "done",
                          "預演過：開場自檢通過，決策也跑了一段", "再預演", "dry_run"))
    else:
        steps.append(Step("dry", 7, "map", "預演一次", "todo",
                          "不送出按鍵跑一遍：確認開場自檢會過、決策看起來合理",
                          "開始預演", "dry_run"))

    busy = _TOOL_STEP.get(f.tool)
    out: List[Step] = []
    first = True
    for s in steps:
        if s.id == busy:
            s = Step(s.id, s.n, s.group, s.title, "busy", f"{f.tool}中…（另一個視窗）",
                     s.button, s.action)
        if first and s.state not in ("done", "busy"):
            first = False
            s = Step(s.id, s.n, s.group, s.title, "next" if s.state == "todo" else s.state,
                     s.detail, s.button, s.action, primary=True)
        out.append(s)
    return out


def progress(steps: List[Step]) -> Tuple[int, int]:
    return sum(1 for s in steps if s.state == "done"), len(steps)


def tab_title(steps: List[Step]) -> str:
    """'準備 5/7'，全部做完是 '準備 √'。"""
    done, total = progress(steps)
    return "準備 √" if done == total else f"準備 {done}/{total}"


def default_mode(steps: List[Step]) -> str:
    """'dry' | 'live'：這張地圖預演過才預設實戰。"""
    return "live" if any(s.id == "dry" and s.state == "done" for s in steps) else "dry"


def _checks(readiness):
    for _title, checks in readiness or []:
        for c in checks:
            if c.name not in HIDDEN_CHECKS:
                yield c


def start_blockers(steps: List[Step], readiness, live: bool) -> List[str]:
    """實戰開始前要點名的事：沒做完的第 2/3/4 步與環境檢查的 FAIL。預演不把關。

    回傳的每一項不含前面的「・」，對話框自己排版。
    """
    if not live:
        return []
    out = [f"{s.n} {_BLOCKING[s.id]}" for s in steps
           if s.id in _BLOCKING and s.state != "done"]
    for c in _checks(readiness):
        if c.status == "fail":
            out.append(tk_safe(f"環境檢查：{c.name}——{c.detail}"))
    return out


# ---------------------------------------------------------------------------
# 環境檢查（doctor.run_all 的結果）摘要
# ---------------------------------------------------------------------------

def readiness_summary(readiness) -> Tuple[str, str]:
    """(「環境檢查：3 項通過、1 項提醒、0 項必須修」, tone)；還沒跑完是「環境檢查中…」。"""
    if readiness is None:
        return "環境檢查中…", "muted"
    counts = {"ok": 0, "warn": 0, "fail": 0}
    for c in _checks(readiness):
        counts[c.status] = counts.get(c.status, 0) + 1
    tone = "bad" if counts["fail"] else "warn" if counts["warn"] else "ok"
    return (f"環境檢查：{counts['ok']} 項通過、{counts['warn']} 項提醒、"
            f"{counts['fail']} 項必須修"), tone


Row = Tuple[str, str, str, Optional[Tuple[str, str]]]


def _row(c) -> Row:
    text = f"{c.name}：{c.detail}" if c.detail else c.name
    if c.fix and c.status != "ok":
        text += f"——{c.fix}"
    return (STATUS_GLYPH.get(c.status, "・"), STATUS_TONE.get(c.status, "plain"),
            tk_safe(gui_text(text)), problem_for(f"{c.name} {c.detail} {c.fix}"))


def readiness_rows(readiness, limit: int = 2) -> List[Row]:
    """準備分頁底下最多 limit 列：先 FAIL，再不是選配套件的 WARN。

    每列 (符號, tone, 文字, (動作代號, 按鈕字) 或 None)。永遠不呼叫
    Check.line()：doctor.MARK 帶 U+FE0F，Tk 畫成方框。
    """
    checks = list(_checks(readiness))
    picked = [c for c in checks if c.status == "fail"]
    picked += [c for c in checks if c.status == "warn" and not c.name.startswith("選配")]
    return [_row(c) for c in picked[:limit]]


def readiness_lines(readiness) -> List[Tuple[str, str]]:
    """[詳細] 視窗的全部內容：[(tone, 文字)]，段落標題的 tone 是 'plain'。"""
    out: List[Tuple[str, str]] = []
    for title, checks in readiness or []:
        out.append(("plain", tk_safe(f"【{title}】")))
        for c in checks:
            glyph, tone, text, _act = _row(c)
            out.append((tone, f"{glyph} {text}"))
    return out
