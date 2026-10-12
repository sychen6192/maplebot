"""環境自檢：開跑之前先把「一定會失敗」的狀況講清楚。

`runner._preflight` 檢查的是**辨識對不對**（ROI 有沒有框歪、讀不讀得到 HP），
但它得先能跑起來才會執行。使用者實際卡住的地方大多在更前面：套件沒裝、
設定檔打錯、模型路徑寫錯、遊戲沒開、視窗大小跟校正當下不一樣。
那些都不必開遊戲、不必按任何鍵就能查出來。

每個檢查回傳 `Check`，包含**該怎麼修**——「❌ 找不到模型」沒有用，
「❌ 找不到模型 → 執行 python tools/train_yolo.py，或把 vision.mob_detector
改回 outline」才有用。

所有檢查都是純函式（吃參數、回 Check），所以整套邏輯測得到、不需要真的
壞掉的環境。哪些檢查、照什麼順序跑由 run_all() 決定：CLI（tools/doctor.py）
只負責印，GUI 的準備分頁拿同一份來彙總，兩邊才不會講出不一樣的結論。
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
    from ..vision.yolo_mobs import is_pretrained_name
    return is_pretrained_name(path)


# 跟 vision/nametag.FEATURE_NAMES 是同一組檔名（測試會對）。不直接 import
# 是因為那會連帶載入 cv2，而 doctor 要在 cv2 沒裝的機器上也跑得完，
# 才講得出「cv2 沒裝」。
NAMETAG_FILES = ("player_feature.png", "player_nametag.png")


def check_nametag(ui_dir: str, exists: Callable[[str], bool] = os.path.exists) -> Check:
    """使用者截過角色名牌沒有。

    沒截 bot 照樣跑得起來，所以只是 WARN；但症狀很難自己看出來：角色定位
    退回組隊紅條（要自己跟自己組隊才有）或畫面正中央，而鏡頭有跟隨延遲、
    走到地圖邊緣還會停住，實測角色可以偏離中心 200px 以上——挖掉「自己」
    挖錯地方，角色就被當成一隻怪站著打一整晚。
    """
    for name in NAMETAG_FILES:
        path = os.path.join(ui_dir, name)
        if exists(path):
            return Check("角色名牌", OK, path)
    return Check("角色名牌", WARN, "還沒截角色名牌",
                 f"python tools/grab_template.py --dir {ui_dir} --name player_nametag"
                 "（控制台：準備 › 3 截角色名牌）。沒有名牌時角色定位只能靠組隊紅條"
                 "或畫面正中央，鏡頭一偏就會把角色自己當成怪站著打")


REACH = "射程設定"


def check_reach(cfg, profile) -> List[Check]:
    """射程、追擊、後退、角色排除範圍、找怪範圍彼此有沒有打架。

    這幾個數字分散在 Profile（range_px／chase_px／keep_away_px）與 Config
    （outline_player_box／mob_search_box），單看每一個都合理，湊在一起才會
    出事，而且症狀都是「怎麼不打／不追／不退」，看不出是設定互相抵銷。
    全部換成 playfield 的實際像素再比：outline_player_box 與 Profile 的距離
    以 790 寬為基準縮放（ADR 0003），mob_search_box 則是實際像素、不縮放。

    比的是「單隻怪就會放」的技能（min_mobs <= 1）：fsm 平常靠它打，要好幾隻
    才放的大絕不會擋追擊，也不是後退距離要配合的那一招。
    """
    import math
    from ..config import REFERENCE_WIDTH
    pf_w = cfg.regions.get("playfield", (0, 0, REFERENCE_WIDTH, 0))[2] or REFERENCE_WIDTH
    base = pf_w / REFERENCE_WIDTH                       # 790 基準 -> 實際像素
    s = base if profile.attack_auto_scale else 1.0      # Profile 距離的換算
    skills = profile.active_skills()
    basic = [sk for sk in skills if max(sk.min_mobs, 1) <= 1] or skills[:1]
    ref = basic[0]
    single_reach = max(sk.range_px for sk in basic)
    longest = max(sk.range_px for sk in skills)
    # 跟 Perceiver 丟掉角色自己的規則一樣：框寬換成實際像素取整數再除 2，
    # 中心離角色 <= 這個值的偵測一律丟掉
    half = int(cfg.vision.outline_player_box[0] * base) // 2
    name = f"「{ref.key}」" if ref.key else "主攻"
    out = []

    keep = profile.keep_away_px
    if keep > 0 and keep * s <= half + 1:
        least = math.floor((half + 1) / s) + 1
        out.append(Check(REACH, WARN,
                         f"後退距離 keep_away_px={keep}（實際 {keep * s:.0f}px）沒超過"
                         f"角色排除範圍的半寬 {half}px",
                         "貼著角色的怪在決策前就被當成角色自己濾掉了，永遠不會觸發後退。"
                         f"keep_away_px 至少設 {least}，或把 vision.outline_player_box 的寬縮小"))
    if keep > 0 and keep >= ref.range_px:
        out.append(Check(REACH, WARN,
                         f"後退距離 keep_away_px={keep} 不小於{name}的射程 range_px={ref.range_px}",
                         "怪一進射程就先後退，退開了又打不到，等於永遠不打。"
                         "keep_away_px 要明顯小於 range_px（遠程範本是 60 對 110）"))
    if ref.range_px * s <= half:
        out.append(Check(REACH, WARN,
                         f"{name}射程 range_px={ref.range_px}（實際 {ref.range_px * s:.0f}px）"
                         f"沒超過角色排除範圍的半寬 {half}px",
                         "中心離角色這麼近的怪都會被當成角色自己濾掉，能打的只剩"
                         "比排除範圍再遠一點、框邊剛好碰到射程的怪。調大 range_px，"
                         "或縮小 vision.outline_player_box 的寬"))
    if profile.chase_px > 0 and profile.chase_px <= single_reach:
        out.append(Check(REACH, WARN,
                         f"追擊距離 chase_px={profile.chase_px} 不大於單隻怪就會放的射程 "
                         f"range_px={single_reach}",
                         "射程外的怪也在追擊範圍外，追擊永遠不會發生。chase_px 要比它大"
                         "（實機 1366 寬的近戰 55 配 120 跑得順）。要好幾隻才放的大絕不算，"
                         "它不會擋追擊"))
    box = cfg.vision.mob_search_box
    if box:
        need = max(longest, profile.chase_px) * s
        if box[0] / 2 < need:
            out.append(Check(REACH, WARN,
                             f"找怪範圍 mob_search_box 寬 {box[0]}px，一半 {box[0] // 2}px 小於"
                             f"{'追擊距離' if profile.chase_px > longest else '射程'}"
                             f"換算後的 {need:.0f}px",
                             "框外的怪根本看不到，射程與追擊都被截斷。mob_search_box 是"
                             f"實際像素、不跟著視窗縮放，寬至少要 {int(need * 2 + 1)}"))
    if not out:
        detail = f"射程 {ref.range_px}" + (f"（最長 {longest}）" if longest != ref.range_px else "")
        if profile.chase_px:
            detail += f"・追擊 {profile.chase_px}"
        if keep:
            detail += f"・後退 {keep}"
        out.append(Check(REACH, OK, detail + "，彼此不衝突"))
    return out


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
                         "之上（例如停機線 25% 就設 50%），否則每次都要先掉進危險線才開始喝藥，"
                         "只剩 critical_hp_seconds 那幾秒能搶救"))
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
        from ..window import find_game_window
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

    # 按鍵權限：遊戲提權了、bot 沒有 -> SendInput 被 UIPI **默默**丟掉。
    # 這是所有掛機失敗裡最難自己看出來的：log 顯示按了、failures 也是 0、
    # 遊戲就是收不到。角色站在原地被圍毆、藥也喝不下去。
    hwnd = getattr(win, "hwnd", None)
    if hwnd:
        from ..window import current_process_elevated, window_process_elevated
        game_elev = window_process_elevated(hwnd)
        if game_elev is True and current_process_elevated() is False:
            out.append(Check(
                "按鍵權限", FAIL,
                "遊戲以系統管理員執行，這裡卻是一般權限",
                "Windows 的 UIPI 會默默丟掉一般權限行程送給提權視窗的按鍵"
                "（SendInput 回報成功、遊戲收不到、角色一步都不會動）。"
                "請用「以系統管理員身分執行」開啟終端機或 GUI 再跑"))
        elif game_elev is True:
            out.append(Check("按鍵權限", OK, "遊戲與 bot 都是系統管理員，可以送鍵"))
        elif game_elev is False:
            out.append(Check("按鍵權限", OK, "遊戲未提權，一般權限就能送鍵"))
    return out


def check_game_process(cfg) -> Check:
    from ..telemetry.sysmon import HAVE_PSUTIL
    if not HAVE_PSUTIL:
        return Check("遊戲行程", WARN, "沒裝 psutil，無法偵測遊戲當掉",
                     "pip install psutil")
    if not cfg.monitor.enabled:
        return Check("遊戲行程", WARN, "monitor.enabled 是 false，遊戲當掉不會被偵測到",
                     "config 設 monitor.enabled: true")
    return Check("遊戲行程監看", OK,
                 f"每 {cfg.monitor.interval:g} 秒檢查一次"
                 + ("，遊戲結束時自動停機" if cfg.monitor.stop_when_game_exits else ""))


# 血條 ROI 框歪到什麼程度會讓讀值出錯。
# 這個數字是照**症狀**量出來的，不是憑感覺訂的（fixture，hp_bar 寬 105）：
#
#   水平平移   讀值    誤差        垂直平移   讀值    誤差
#      0      1.000   +0.000          2      1.000   +0.000
#      5      0.952   +0.048          4      1.000   +0.000
#     10      0.905   +0.095          6      1.000   +0.000
#     20      0.810   +0.190          8      1.000   +0.000
#
# 關鍵發現：**只有水平方向會影響讀值**。bar_ratio 算的是「最右邊有填色的欄
# 位置 / ROI 寬度」，所以 x 與寬度歪掉就直接變成比例誤差（誤差 ≈ 位移/寬度），
# 而垂直歪掉只要還切到色條就完全不影響。用 IoU 判斷會同時被垂直方向拖累、
# 又對水平方向不夠敏感——20px 的水平位移（讀值已經差 19%）IoU 還有 0.51。
#
# 0.10 是這樣訂的：喝藥門檻常設在 0.5/0.3，10% 的讀值誤差足以讓它提早或
# 延後觸發；再小就開始被「自動偵測的框本來就比 ROI 大一兩 px」誤判。
_BAR_MAX_RATIO_ERROR = 0.10


def check_status_bars(cfg, frame) -> List[Check]:
    """設定的 HP/MP/EXP ROI 跟畫面上自動找到的血條對得上嗎？

    **為什麼要這一項**：`vision/statusbar.py` 的自動偵測早就寫好了，但一直
    只有 `tools/calibrate.py` 在用——執行期完全沒有人拿它來對答案。於是
    「ROI 平移之後讀到一個合理但錯的值」這種情況沒有任何東西擋得住：
    框歪到外框上就永遠讀 100%（於是永遠不喝藥），框到底色就永遠讀 0%
    （於是灌兩瓶藥再判定瀕死停機）。兩種都不會讓 bot 看起來壞掉，
    使用者只會覺得「莫名其妙」。現有的開場自檢只擋得到後面那一種。

    frame 是一張完整的遊戲畫面（client 區）。自動偵測認不出來時只警告不擋——
    那可能只是這個客戶端的狀態列長得不一樣，不代表使用者的設定是錯的。
    """
    from ..vision.statusbar import find_status_bars

    found = find_status_bars(frame)
    if not found:
        return [Check("血條 ROI 交叉驗證", WARN, "畫面上自動找不到狀態列",
                      "不一定是你的設定錯——也可能是這個客戶端的狀態列長得不一樣。"
                      "用 python tools/debug_view.py --snapshot check.png 看一眼框在哪")]

    out = []
    for name in ("hp_bar", "mp_bar", "exp_bar"):
        want, got = cfg.regions.get(name), found.get(name)
        if want is None or got is None:
            continue
        wx, wy, ww, wh = want
        gx, gy, gw, gh = got
        # 讀值誤差 ≈ 左緣位移/寬度 + 寬度誤差/寬度（見上方量測）
        err = (abs(wx - gx) + abs(ww - gw)) / float(max(gw, 1))
        # 垂直方向不影響比例，但完全錯開就會讀成 0%——那是另一種症狀
        missed = wy + wh <= gy or wy >= gy + gh

        if missed:
            out.append(Check(
                f"{name} 位置", FAIL,
                f"設定是 {tuple(want)}，但畫面上的血條在 {tuple(got)}：垂直完全錯開",
                "ROI 整個沒框到色條，讀值會是 0%——灌兩瓶藥再判定瀕死停機。"
                "執行 python tools/calibrate.py --write 重新框選"))
        elif err > _BAR_MAX_RATIO_ERROR:
            out.append(Check(
                f"{name} 位置", FAIL,
                f"設定是 {tuple(want)}，但畫面上的血條在 {tuple(got)}"
                f"：水平差了 {abs(wx - gx)}px、寬度差 {abs(ww - gw)}px，"
                f"讀值大約會偏 {err:.0%}",
                "bar_ratio 算的是「最右邊有色的欄 / ROI 寬度」，所以水平框歪就是"
                "直接的比例誤差。喝藥門檻常設在 50%/30%，這種偏差足以讓它提早或"
                "延後觸發。執行 python tools/calibrate.py --write 重新框選"))
        else:
            out.append(Check(f"{name} 位置", OK,
                             f"與自動偵測相符（讀值偏差約 {err:.0%}）"))
    if not out:
        out.append(Check("血條 ROI 交叉驗證", WARN, "設定裡沒有可比對的血條 ROI",
                         "python tools/calibrate.py --write"))
    return out


# ---- 整套組合 ----

# 讀不起來時 run_all 就停在這一項；config_failed() 靠這個名字認出來
CONFIG_CHECK = "全域設定"


def run_all(cfg_path: str, profile_path: str, importer: Callable[[str], bool],
            window_finder: Optional[Callable[[str], object]] = None,
            source: str = "") -> List[Tuple[str, List[Check]]]:
    """整套自檢，依段落回傳 [(段落標題, [Check, ...]), ...]。

    CLI（tools/doctor.py）照順序印，GUI 拿來彙總——兩邊用同一份組合，
    才不會 CLI 說有問題、控制台卻說一切就緒。importer 由呼叫端決定：
    CLI 真的 import（才抓得到「裝了但載入就炸」），GUI 用 find_spec
    （真的 import ultralytics 會連帶載入 torch，要好幾秒）。

    全域設定讀不起來就停在「設定檔」那段：後面每一項都要用到 cfg，硬跑
    只會多出一串跟真正問題無關的錯。用 config_failed() 判斷是不是停在這裡。
    「遊戲視窗」那段只在有結果時出現（非 Windows 整段跳過）；
    「辨識」那段只在給了 source 截圖時出現。
    """
    from ..config import ConfigError, load_config, load_profile

    env = [check_python(), check_platform()]
    env += check_modules(importer)
    env.append(check_writable())
    sections: List[Tuple[str, List[Check]]] = [("執行環境", env)]

    try:
        cfg = load_config(cfg_path)
    except ConfigError as e:
        sections.append(("設定檔", [Check(
            CONFIG_CHECK, FAIL, str(e),
            f"修正 {cfg_path}（YAML 縮排/欄位型別），或跟版本控管裡的預設檔比對")]))
        return sections
    conf = [Check(CONFIG_CHECK, OK, " + ".join(cfg.sources))]
    conf += check_regions(cfg)
    conf.append(check_detector(cfg))
    conf.append(check_nametag(cfg.vision.ui_templates_dir))
    conf.append(check_game_process(cfg))
    sections.append(("設定檔", conf))

    try:
        profile = load_profile(profile_path)
    except ConfigError as e:
        prof = [Check("Profile", FAIL, str(e), f"修正 {profile_path}")]
    else:
        prof = [Check("Profile", OK, profile.name)]
        prof += check_profile(profile, cfg.safety.critical_hp_ratio)
        prof += check_reach(cfg, profile)
    sections.append((f"Profile（{profile_path}）", prof))

    if source:
        import cv2
        # 先問檔案在不在：cv2.imread 讀不到會往 stderr 印一行 WARN，而這裡是
        # 全部算完才印，那行會跑到報告最上面、跟「截圖」那項對不起來
        shot = cv2.imread(source, cv2.IMREAD_COLOR) if os.path.exists(source) else None
        if shot is None:
            vis = [Check("截圖", FAIL, f"讀不到 {source}",
                         "確認路徑；用 tools/debug_view.py --snapshot 可以存一張")]
        else:
            vis = check_status_bars(cfg, shot)
        sections.append(("辨識（用 --source 的截圖驗）", vis))

    window_checks = check_window(cfg, window_finder)
    if window_checks:
        sections.append(("遊戲視窗", window_checks))
    return sections


def config_failed(sections: List[Tuple[str, List[Check]]]) -> bool:
    """run_all 是不是停在全域設定讀不起來那一步。"""
    return any(c.name == CONFIG_CHECK and c.status == FAIL
               for _, checks in sections for c in checks)
