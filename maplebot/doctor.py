"""環境自檢：開跑之前先把「一定會失敗」的狀況講清楚。

`runner._preflight` 檢查的是**辨識對不對**（ROI 有沒有框歪、讀不讀得到 HP），
但它得先能跑起來才會執行。使用者實際卡住的地方大多在更前面：套件沒裝、
設定檔打錯、模型路徑寫錯、遊戲沒開、視窗大小跟校正當下不一樣。
那些都不必開遊戲、不必按任何鍵就能查出來。

每個檢查回傳 `Check`，包含**該怎麼修**——「❌ 找不到模型」沒有用，
「❌ 找不到模型 → 執行 python tools/train_yolo.py，或把 vision.mob_detector
改回 outline」才有用。

所有檢查都是純函式（吃參數、回 Check），CLI 在 tools/doctor.py，
所以整套邏輯測得到、不需要真的壞掉的環境。
"""
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

OK, WARN, FAIL = "ok", "warn", "fail"
MARK = {OK: "✅", WARN: "⚠️ ", FAIL: "❌"}

MIN_PYTHON = (3, 9)

# (import 名稱, 顯示名稱, 用途)
REQUIRED_MODULES = [
    ("numpy", "numpy", "數值運算"),
    ("cv2", "opencv-python", "影像處理"),
    ("yaml", "PyYAML", "設定檔"),
]
OPTIONAL_MODULES = [
    ("mss", "mss", "螢幕擷取（Windows 即時執行必需；離線用 --source 則不需要）"),
    ("psutil", "psutil", "系統/遊戲行程監看與遊戲當掉偵測"),
    ("matplotlib", "matplotlib", "收工報告的曲線圖"),
    ("ultralytics", "ultralytics", "YOLO 偵測與訓練（mob_detector: yolo）"),
    ("requests", "requests", "遠端推理與 VLM 督導層"),
]


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    fix: str = ""

    def line(self) -> str:
        text = f"{MARK[self.status]} {self.name}"
        if self.detail:
            text += f": {self.detail}"
        if self.fix and self.status != OK:
            text += f"\n     → {self.fix}"
        return text


@dataclass
class Report:
    checks: List[Check] = field(default_factory=list)

    def add(self, check: Check) -> Check:
        self.checks.append(check)
        return check

    def count(self, status: str) -> int:
        return sum(1 for c in self.checks if c.status == status)

    @property
    def failed(self) -> bool:
        return self.count(FAIL) > 0

    def summary(self) -> str:
        return (f"{self.count(OK)} 項通過、{self.count(WARN)} 項提醒、"
                f"{self.count(FAIL)} 項必須修")


# ---- 個別檢查（全部是純函式）----

def check_python(version: Tuple[int, ...] = None) -> Check:
    version = tuple(sys.version_info[:2]) if version is None else tuple(version[:2])
    shown = ".".join(str(v) for v in version)
    if version >= MIN_PYTHON:
        return Check("Python 版本", OK, shown)
    return Check("Python 版本", FAIL, f"{shown}（需要 {'.'.join(map(str, MIN_PYTHON))}+）",
                 "裝新版 Python，或用 uv venv 建立新的虛擬環境")


def check_modules(importer: Callable[[str], bool], required=None, optional=None) -> List[Check]:
    """importer 傳進來是為了測試——真的執行時就是「試著 import 看看」。"""
    out = []
    for mod, pkg, why in (REQUIRED_MODULES if required is None else required):
        if importer(mod):
            out.append(Check(f"套件 {pkg}", OK, why))
        else:
            out.append(Check(f"套件 {pkg}", FAIL, f"沒裝（{why}）",
                             f"pip install {pkg}　或　pip install -r requirements.txt"))
    for mod, pkg, why in (OPTIONAL_MODULES if optional is None else optional):
        if importer(mod):
            out.append(Check(f"選配 {pkg}", OK, why))
        else:
            out.append(Check(f"選配 {pkg}", WARN, f"沒裝，因此無法使用：{why}",
                             f"要用的話 pip install {pkg}"))
    return out


def check_regions(cfg) -> List[Check]:
    """ROI 有沒有缺、有沒有互相矛盾。

    最重要的是「playfield 有沒有把血條框進去」：框進去的話怪物偵測會把
    UI 當成怪，使用者只會看到「站在空地一直揮」。
    """
    out = []
    needed = ["minimap", "hp_bar", "mp_bar", "playfield"]
    missing = [n for n in needed
               if n not in cfg.regions and not (n == "minimap" and cfg.minimap_auto)]
    if missing:
        out.append(Check("必要 ROI", FAIL, "缺少 " + "、".join(missing),
                         "python tools/calibrate.py --write 重新框選"))
    else:
        out.append(Check("必要 ROI", OK, "、".join(needed)))

    if cfg.calibrated_for:
        cw, ch = cfg.calibrated_for
        outside = [f"{n} {r}" for n, r in cfg.regions.items()
                   if r[0] < 0 or r[1] < 0 or r[0] + r[2] > cw or r[1] + r[3] > ch]
        if outside:
            out.append(Check("ROI 是否在畫面內", FAIL,
                             f"超出校正尺寸 {cw}x{ch} 的有 " + "、".join(outside),
                             "python tools/calibrate.py --write 重新框選"))
        else:
            out.append(Check("ROI 是否在畫面內", OK, f"全部落在 {cw}x{ch} 內"))
    else:
        out.append(Check("window.calibrated_for", WARN, "沒記錄校正當下的視窗大小",
                         "重跑 tools/calibrate.py --write 會自動寫入；"
                         "有了它，視窗大小改變時開場就會擋下來而不是讀出一堆錯值"))

    pf = cfg.regions.get("playfield")
    if pf:
        # 小地圖**本來就**畫在遊戲畫面上，重疊是正常的，不能拿來當警告
        # （不然每個人的預設設定都會跳一個假警報）。真正該擋的是血條那排：
        # 它們在畫面下緣的 UI 區，被框進 playfield 代表 ROI 框太大了
        overlaps = [n for n in ("hp_bar", "mp_bar", "exp_bar")
                    if n in cfg.regions and _overlap(pf, cfg.regions[n])]
        if overlaps:
            out.append(Check("playfield 是否乾淨", WARN,
                             "與 " + "、".join(overlaps) + " 重疊",
                             "血條那排被框進 playfield 的話，怪物偵測會把 UI 當成怪"
                             "（症狀是站在空地一直揮）。重新框選時只框遊戲畫面本體"))
        else:
            out.append(Check("playfield 是否乾淨", OK, "沒有框到 HP/MP/EXP 條"))
    return out


def _overlap(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


def check_detector(cfg, exists: Callable[[str], bool] = os.path.exists,
                   listdir=os.listdir) -> Check:
    """怪物偵測方式選好了，但它需要的東西備齊了嗎。"""
    mode = cfg.vision.mob_detector
    if mode == "outline":
        return Check("怪物偵測", OK, "outline（零設定，不需要模板或模型）")
    if mode == "template":
        d = "data/templates/mobs"
        try:
            names = [f for f in listdir(d) if f.lower().endswith((".png", ".jpg"))]
        except OSError:
            names = []
        if names:
            return Check("怪物偵測", OK, f"template，{len(names)} 個模板")
        return Check("怪物偵測", FAIL, f"template 模式但 {d} 沒有模板",
                     "python tools/grab_template.py --name <怪物名> 先截幾張，"
                     "或把 vision.mob_detector 改回 outline")
    if mode == "yolo":
        path = cfg.vision.yolo_model
        if not path:
            return Check("怪物偵測", FAIL, "yolo 模式但沒設 vision.yolo_model",
                         "填訓練好的 .pt/.onnx 路徑，或改回 outline")
        if not exists(path) and not _is_pretrained(path):
            return Check("怪物偵測", FAIL, f"找不到模型檔 {path}",
                         "確認路徑；還沒訓練的話見 docs/YOLO_TRAINING.md，"
                         "或先把 vision.mob_detector 改回 outline")
        return Check("怪物偵測", OK, f"yolo（{path}）")
    if mode == "remote":
        if not cfg.vision.remote_endpoint:
            return Check("怪物偵測", FAIL, "remote 模式但沒設 vision.remote_endpoint",
                         "填推理伺服器位址，例如 http://192.168.1.50:8100/detect")
        return Check("怪物偵測", OK, f"remote（{cfg.vision.remote_endpoint}）"
                     + "，連線狀況用 python tools/check_remote.py 測")
    return Check("怪物偵測", FAIL, f"不認得的模式 {mode!r}",
                 "vision.mob_detector 只能是 outline / template / yolo / remote")


def _is_pretrained(path: str) -> bool:
    from .vision.yolo_mobs import is_pretrained_name
    return is_pretrained_name(path)


def check_profile(profile, critical_hp_ratio: Optional[float] = None) -> List[Check]:
    out = []
    skills = profile.active_skills()
    if any(s.key for s in skills):
        keys = "、".join(s.key for s in skills if s.key)
        out.append(Check("攻擊鍵", OK, f"{len(skills)} 個技能（{keys}）"))
    else:
        out.append(Check("攻擊鍵", FAIL, "沒有任何技能設定 key",
                         "在 profile 的 attack.key 或 skills[].key 填遊戲裡的攻擊鍵"))

    if profile.patrol.auto:
        out.append(Check("巡邏路線", OK, "waypoints: auto（開場自動量測）"))
    elif profile.patrol.waypoints:
        out.append(Check("巡邏路線", OK, f"{len(profile.patrol.waypoints)} 個巡邏點"))
    else:
        out.append(Check("巡邏路線", FAIL, "沒有巡邏點，角色打完附近的怪就不會動了",
                         "profile 設 patrol.waypoints: auto，"
                         "或用 gui.py 的錄製功能走一趟"))

    hp = profile.potions.get("hp")
    if hp and hp.key and hp.below_ratio > 0:
        out.append(Check("補血藥", OK, f"{hp.key} 鍵，低於 {hp.below_ratio:.0%} 時喝"))
    else:
        out.append(Check("補血藥", WARN, "沒設定——掉血只能靠 critical_hp_ratio 停機",
                         "profile 的 potions.hp 填藥水鍵與門檻"))

    # 這組值互相矛盾時的症狀是「一掉血就停機」，而使用者會以為是辨識壞了：
    # 喝藥門檻設得比停機線低的話，永遠是先觸發停機、輪不到喝藥
    if (critical_hp_ratio is not None and hp and hp.key
            and 0 < hp.below_ratio <= critical_hp_ratio):
        out.append(Check("藥水門檻 vs 危險線", FAIL,
                         f"喝藥門檻 {hp.below_ratio:.0%} 不高於停機線 "
                         f"{critical_hp_ratio:.0%}",
                         "把 potions.hp.below_ratio 調高到 safety.critical_hp_ratio "
                         "之上（例如停機線 25% 就設 50%），否則永遠先停機、不會喝藥"))
    return out


def check_writable(path: str = "logs") -> Check:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".doctor_write_test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return Check("可寫入 logs/", OK, os.path.abspath(path))
    except OSError as e:
        return Check("可寫入 logs/", FAIL, str(e),
                     "換個有寫入權限的目錄執行，或用系統管理員開啟終端機")


def check_platform() -> Check:
    if sys.platform == "win32":
        return Check("平台", OK, "Windows（即時擷取與按鍵可用）")
    return Check("平台", WARN, f"{sys.platform}——即時擷取與 SendInput 只支援 Windows",
                 "這台只能做離線開發：python main.py --source <截圖> --dry-run、pytest")


def check_window(cfg, finder: Optional[Callable[[str], object]] = None) -> List[Check]:
    """遊戲視窗找不找得到、大小跟校正當下一不一樣。非 Windows 直接跳過。"""
    if sys.platform != "win32":
        return []
    if finder is None:
        from .window import find_game_window
        finder = find_game_window
    win = finder(cfg.window_title)
    if win is None:
        return [Check("遊戲視窗", FAIL, f"找不到標題含「{cfg.window_title}」的視窗",
                      "先開遊戲並用視窗模式；標題不同的話改 config 的 window.title"
                      "（子字串比對即可）")]
    out = [Check("遊戲視窗", OK, f"{cfg.window_title}（client 區 {win.size[0]}x{win.size[1]}）")]
    if cfg.calibrated_for and tuple(cfg.calibrated_for) != tuple(win.size):
        cw, ch = cfg.calibrated_for
        out.append(Check("視窗大小 vs 校正值", FAIL,
                         f"現在 {win.size[0]}x{win.size[1]}，校正當下是 {cw}x{ch}",
                         "所有 ROI 都會錯位（最常見的症狀是 HP 讀成 0% 然後莫名停機）。"
                         "把視窗調回原本大小，或重跑 tools/calibrate.py --write"))
    return out


def check_game_process(cfg) -> Check:
    from .sysmon import HAVE_PSUTIL
    if not HAVE_PSUTIL:
        return Check("遊戲行程", WARN, "沒裝 psutil，無法偵測遊戲當掉",
                     "pip install psutil")
    if not cfg.monitor.enabled:
        return Check("遊戲行程", WARN, "monitor.enabled 是 false，遊戲當掉不會被偵測到",
                     "config 設 monitor.enabled: true")
    return Check("遊戲行程監看", OK,
                 f"每 {cfg.monitor.interval:g} 秒檢查一次"
                 + ("，遊戲結束時自動停機" if cfg.monitor.stop_when_game_exits else ""))
