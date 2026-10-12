"""執行卡、儀表、健康數據列與日誌的 view-model。不碰 tkinter。

控制台右邊只有一個狀態面（執行卡）：狀態、原因、一個下一步按鈕。什麼情況
顯示什麼字、按鈕叫什麼、能不能按，全部在這裡從 runner.Status 算出來，
app.py 只把 View 的欄位貼到元件上。這樣「凌晨三點停機、早上七點回來看到
什麼」這種情境不用開視窗、不用開遊戲就能測。

用詞照 CONTEXT.md：暫停（可繼續）、停機（Panic，不可逆）、已停止（使用者
停的）、讓過（Wait，這個 tick 不動作）各有各的意思，不要混用。
"""
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import settings

# ---------------------------------------------------------------------------
# 用詞
# ---------------------------------------------------------------------------

# fsm 每一種 Action 的中文。Status.action 是類別名稱；'-' 是還沒做過決策
ACTION_LABELS: Dict[str, str] = {
    "Wait": "讓過",
    "Attack": "攻擊",
    "Chase": "追擊",
    "Move": "巡邏移動",
    "Climb": "垂直移動",
    "Escape": "脫困",
    "Probe": "探邊",
    "Loot": "撿物",
    "RunKeys": "巡邏點按鍵",
    "CastBuff": "補 buff",
    "DrinkPotion": "喝藥",
    "Panic": "停機",
    "-": "—",
}

# Tcl 8.6 只吃 BMP，而且 JhengHei 只涵蓋這些符號。✅❌⚠️ 會變成方框
ALLOWED_SYMBOLS = frozenset("●○▶‖■√！×▲・›•")

# doctor / selfcheck 的 status -> (符號, tone)
STATUS_GLYPH = {"ok": "√", "warn": "！", "fail": "×"}
STATUS_TONE = {"ok": "ok", "warn": "warn", "fail": "bad"}

# 工具的標籤（也是 run_tool 的 label）-> 重試時要分派的動作代號
TOOL_ACTION = {"校正": "calibrate", "截角色名牌": "nametag", "辨識自檢": "vision"}
_TOOL_DETAIL = {
    "校正": "框選視窗是另一個視窗，找不到就看工作列。順序：小地圖（只框地圖畫布，"
            "不含標題列）→ playfield。完成後會回到這裡",
    "截角色名牌": "框選視窗是另一個視窗，找不到就看工作列。只框角色腳下的名牌，按 Enter 確認",
    "辨識自檢": "正在拍一張遊戲畫面並標註辨識結果，好了會跳出結果視窗",
}
_TOOL_DETAIL_DEFAULT = "框選視窗是另一個視窗，找不到就看工作列。完成後會回到這裡"

# 對話框文案集中在這裡，測試才能檢查用詞與符號。(標題, 內文)
DIALOGS: Dict[str, Tuple[str, str]] = {
    "stop": ("停止這一場？",
             "停止會放開所有按鍵、寫出收工報告，這一場就結束了。\n\n"
             "只是要離開一下的話，按「‖ 暫停」就好。"),
    "close_busy": ("bot 還在跑",
                   "關掉控制台會停止這一場（放開按鍵、寫收工報告）。確定要關閉嗎？"),
    "template": ("範本不能直接修改",
                 "「{name}」是範本，存進去會改到大家共用的範例檔。"
                 "要用現在的設定另存成你自己的地圖嗎？"),
    "save_as": ("另存為我的地圖", "地圖名稱（例如：弓箭手訓練場一）"),
    "unsaved": ("還沒儲存", "「{name}」有 {n} 項修改還沒存（{fields}）。{verb}前要先儲存嗎？"),
    "revert": ("還原", "放棄 {n} 項修改，改回檔案裡的值？"),
    "start_gate": ("還沒準備好", "還沒完成：\n{items}\n仍要開始嗎？"),
    "not_ready": ("還不能開始", "{items}"),
    "calibrate": ("校正",
                  "接下來會跳出框選視窗：拖曳框住後按 Enter，按 c 跳過。"
                  "順序：小地圖 → playfield（HP／MP／EXP 多半會自動找到）。"
                  "小地圖只框地圖畫布本體，不要框到標題列——標題列上的黃色圖示"
                  "會被當成角色。"
                  "完成後自動寫回 config/local.yaml"),
    "nametag": ("截角色名牌",
                "等一下會跳出一張遊戲截圖。拖曳框住角色腳下那塊深色底的名字（名牌），"
                "按 Enter 確認。只框名牌，不要框到角色身體或別人的名字。按 c 取消。"),
    "vision": ("辨識自檢結果",
               "圖例：白框＝region　綠圈＝角色　紅圈＝其他玩家　黃框＝怪物　"
               "灰框＝跟隨物　紫＝名牌定位　青框＝攻擊範圍　淡藍框＝追擊範圍　"
               "橘線＝後退距離　深灰框（self）＝角色排除範圍　淺灰框（search）＝找怪範圍"),
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

_EMOJI = {"✅": "√", "❌": "×", "⚠": "！"}


def tk_safe(text: str) -> str:
    """Tk 畫得出來的字串：去掉 U+FE0F、常見 emoji 換成白名單符號、BMP 以外換成 ?。

    doctor 的 MARK 帶 U+FE0F，工具的 stdout 又整段收進日誌——Tcl 8.6 碰到
    BMP 以外的字元會畫成方框甚至丟例外。
    """
    out = []
    for ch in str(text):
        if ch == "️":
            continue
        ch = _EMOJI.get(ch, ch)
        out.append(ch if ord(ch) <= 0xFFFF else "?")
    return "".join(out)


def duration(seconds: float) -> str:
    """42 -> '0:42'、5025 -> '1:23:45'。"""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def headline(s: str, limit: int = 60) -> str:
    """第一句話：第一行裡 '——' 或 '。' 之前那段，最多 limit 字加 '…'。

    runner 與 doctor 的訊息都寫成「症狀——原因與修法」，卡片上只放得下症狀。
    """
    lines = [ln for ln in (s or "").strip().splitlines() if ln.strip()]
    line = lines[0] if lines else ""
    for sep in ("——", "。"):
        line = line.split(sep)[0]
    line = line.strip()
    return line if len(line) <= limit else line[:limit] + "…"


# CLI 的修法翻成控制台裡的位置。順序有關係：名牌那條要先吃掉 grab_template，
# 再把 doctor 自己附的「（控制台：…）」拿掉，才不會講兩次
_GUI_PATHS = [
    (re.compile(r"(?:python\s+)?tools/grab_template\.py[^。；\n（]*?player_nametag"),
     "準備 › 3 截角色名牌"),
    (re.compile(r"（控制台：[^）]*）"), ""),
    (re.compile(r"(?:python\s+)?tools/calibrate\.py(?:\s+--write)?"), "準備 › 2 校正"),
    (re.compile(r"(?:python\s+)?tools/debug_view\.py\s+--snapshot\s+\S+"), "準備 › 4 辨識自檢"),
    (re.compile(r"gui\.py 的錄製功能"), "巡邏與 buff › 錄製路線"),
]


def gui_text(s: str) -> str:
    """把「python tools/calibrate.py --write」這類 CLI 修法換成控制台的路徑。

    ROI 也換成 region（CONTEXT.md 的用詞；doctor 為了 CLI 輸出不變還留著舊稱）。
    """
    out = s or ""
    for pattern, repl in _GUI_PATHS:
        out = pattern.sub(repl, out)
    return out.replace("ROI", "region")


# (關鍵字們, 動作代號, 按鈕字)。由上往下第一個符合的勝出
_PROBLEM_ACTIONS: List[Tuple[Tuple[str, ...], str, str]] = [
    (("系統管理員",), "elevate", "以系統管理員身分重開"),
    (("player_nametag", "名牌"), "nametag", "截名牌"),
    (("找不到遊戲視窗", "window.title"), "window", "到「準備 › 1 遊戲視窗」"),
    (("calibrate.py", "region", "ROI", "regions."), "calibrate", "去校正 →"),
    (("logs/anomalies",), "anomalies", "開啟異常畫面"),
]


def problem_for(text: str) -> Optional[Tuple[str, str]]:
    """從 runner / doctor 的訊息挑出「下一步」按鈕：(動作代號, 按鈕字) 或 None。

    doctor.Check 不多開欄位：修法文字本來就點名了要做什麼，用關鍵字對照就夠，
    CLI 的行為也不會因此改變。
    """
    text = text or ""
    for words, action, label in _PROBLEM_ACTIONS:
        if any(w in text for w in words):
            return action, label
    return None


def action_text(action: str, reason: str = "") -> str:
    """'Wait', 'x' -> '讓過（x）'；'-' -> '—'。"""
    label = ACTION_LABELS.get(action, action)
    return f"{label}（{reason}）" if reason else label


def _signed_pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


# ---------------------------------------------------------------------------
# 儀表與健康數據列
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Limits:
    """儀表刻度與文案要用的門檻，load() 時從 cfg / profile 算一次。"""
    hp_potion: float = 0.0      # 0 = 沒綁 HP 藥水
    mp_potion: float = 0.0
    critical: float = 0.0       # 停機線
    pause_key: str = "F9"
    stop_key: str = "F12"
    max_runtime: float = 0.0    # 秒；0 = 不限


def limits_from(cfg, profile) -> Limits:
    if cfg is None:
        return Limits()

    def pot(kind: str) -> float:
        p = profile.potions.get(kind) if profile is not None else None
        return p.below_ratio if p is not None and p.key else 0.0

    return Limits(hp_potion=pot("hp"), mp_potion=pot("mp"),
                  critical=cfg.safety.critical_hp_ratio,
                  pause_key=cfg.safety.pause_key.upper(),
                  stop_key=cfg.safety.stop_key.upper(),
                  max_runtime=cfg.safety.max_runtime_minutes * 60)


@dataclass(frozen=True)
class Meter:
    """一條儀表：比例（None = 讀不到）、右邊的文字、文字的 tone、門檻刻度。

    marks 是 ((比例, 'potion' | 'critical'), ...)，顏色見 look.MARKS。
    """
    ratio: Optional[float]
    text: str
    tone: str
    marks: Tuple[Tuple[float, str], ...] = ()


def meter(value: Optional[float], kind: str, lim: Limits) -> Meter:
    """kind: 'hp' | 'mp' | 'exp'。HP 到停機線 bad、到喝藥線 warn；EXP 一律中性。"""
    marks: Tuple[Tuple[float, str], ...] = ()
    if kind == "hp":
        marks = tuple(m for m in ((lim.hp_potion, "potion"), (lim.critical, "critical"))
                      if m[0] > 0)
    elif kind == "mp" and lim.mp_potion > 0:
        marks = ((lim.mp_potion, "potion"),)
    if value is None:
        return Meter(None, "讀不到", "muted", marks)
    v = min(max(float(value), 0.0), 1.0)
    if kind == "exp":
        return Meter(v, f"{v * 100:.1f}%", "plain", marks)
    if kind == "hp":
        if lim.critical > 0 and v <= lim.critical:
            tone = "bad"
        elif lim.hp_potion > 0 and v <= lim.hp_potion:
            tone = "warn"
        else:
            tone = "ok"
    else:
        tone = "warn" if lim.mp_potion > 0 and v <= lim.mp_potion else "plain"
    return Meter(v, f"{v:.0%}", tone, marks)


def scene_line(st) -> Tuple[str, str]:
    """「角色 (95, 11)・怪物 3・其他玩家 0・跟隨物 1」。有其他玩家或找不到角色是 warn。"""
    if st.ticks == 0 and st.player is None:
        return "角色 —・怪物 —・其他玩家 —・跟隨物 —", "muted"
    pos = f"角色 ({st.player[0]}, {st.player[1]})" if st.player else "找不到角色黃點"
    text = f"{pos}・怪物 {st.mobs}・其他玩家 {st.others}・跟隨物 {st.followers}"
    return text, "warn" if (st.player is None or st.others > 0) else "plain"


def exp_line(st) -> Tuple[str, str]:
    """「經驗 +4.2%・每小時約 +3.1%」；還沒讀到 EXP 是「經驗 —」。"""
    if st.exp_gained is None:
        return "經驗 —", "muted"
    text = f"經驗 {_signed_pct(st.exp_gained)}"
    if st.exp_per_hour is not None:
        text += f"・每小時約 {_signed_pct(st.exp_per_hour)}"
    return text, "plain"


def perf_line(st, target: Optional[float] = None) -> Tuple[str, str]:
    """「tick 5791・7.9 / 8 FPS・遊戲 812 MB」。FPS 到目標 90% ok、60% warn，再低 bad。"""
    target = st.target_fps if target is None else target
    if st.fps > 0:
        ratio = st.fps / target if target > 0 else 1.0
        tone = "ok" if ratio >= 0.9 else "warn" if ratio >= 0.6 else "bad"
        fps = f"{st.fps:.1f}"
    else:
        tone, fps = "muted", "--"
    text = f"tick {st.ticks}・{fps}" + (f" / {target:g}" if target > 0 else "") + " FPS"
    if st.game_mem_mb:
        text += f"・遊戲 {st.game_mem_mb:.0f} MB"
    return text, tone


def end_summary(st) -> str:
    """「這一場 1:23:45・經驗 +4.2%」；沒跑完一場就是 ''。"""
    if not (st.started_at and st.ended_at):
        return ""
    exp = _signed_pct(st.exp_gained) if st.exp_gained is not None else "—"
    return f"這一場 {duration(st.ended_at - st.started_at)}・經驗 {exp}"


# ---------------------------------------------------------------------------
# 執行卡
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Context:
    """Status 以外、執行卡要知道的事。由 Controller 每次 poll 組一個。"""
    thread_alive: bool = False
    stopping: bool = False                  # 已經按了停止、等執行緒收尾
    recording: bool = False
    record_info: Optional[Tuple[float, int, int]] = None   # (秒, 看得到角色的幀, 總幀)
    tool_label: str = ""                    # 正在跑的工具；'' = 沒有
    tool_failed: Optional[Tuple[str, str]] = None           # (工具, stderr 最後一行)
    tool_cancelled: str = ""                # 工具正常結束但什麼都沒存（框選時按了 c）
    loaded: bool = True
    load_error: str = ""
    load_path: str = ""
    dry_choice: bool = False                # 模式選的是預演
    last_error: str = ""                    # 這次開始以來最後一筆 ERROR 的訊息
    crash: str = ""                         # runner 執行緒丟出來的例外
    start_error: str = ""                   # start() 連開都沒開成（找不到視窗）
    show_run: bool = True                   # False = 之後跑過工具，上一場的結局不再顯示
    vision_ready: bool = False              # 剛讀到新的辨識自檢報告（只亮一次）
    generation: int = 0


# 執行卡的 phase。app.py 不需要認得每一個，主要靠 View 的其他欄位；
# title_text 用它分組
PHASES = ("not_loaded", "idle", "tool", "recording", "starting", "running", "waiting",
          "stale", "paused_user", "paused_auto", "stopping", "ended", "not_started",
          "failed", "cancelled", "crashed")
RUNNING_PHASES = ("starting", "running", "waiting", "stale")
PAUSED_PHASES = ("paused_user", "paused_auto")
ENDED_PHASES = ("stopping", "ended", "not_started", "crashed")


@dataclass(frozen=True)
class View:
    """執行卡與即時狀態卡的全部內容，字串都是可以直接顯示的。

    tone 是 look.TONES 的 key；action / primary 的第一欄是動作代號。
    """
    phase: str
    state: str                              # 狀態字，例如「執行中」
    tone: str
    detail: str = ""                        # 最多兩行
    action: Optional[Tuple[str, str]] = None              # (動作代號, 按鈕字)
    primary: Tuple[str, str, bool] = ("start", "▶ 開始打怪", True)  # (指令, 字, 可按)
    can_stop: bool = False
    settings_locked: bool = False
    lock_text: str = ""                     # 左邊琥珀色那一條；'' = 不顯示
    dry_tag: bool = False                   # 顯示紫色 [預演]
    run_time: str = ""                      # '1:23:45'（只在執行中）
    elapsed: str = ""                       # 「已跑 1:23:45・剩 36:15」
    meters: Tuple[Meter, ...] = ()          # (HP, MP, EXP)
    frozen: str = ""                        # 「（暫停前）」/「（停止時）」；'' = 數值是即時的
    lines: Tuple[Tuple[str, str], ...] = ()  # (scene, exp, perf) 各是 (文字, tone)
    summary: str = ""                       # 收工摘要「這一場 …」
    report: str = ""                        # 收工報告的絕對路徑；'' = 沒有
    open_vision: bool = False               # 這一次 poll 該打開辨識自檢結果視窗
    generation: int = 0

    @property
    def pill(self) -> str:
        return f"● {self.state}"


def _first_line(text: str, limit: int = 80) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    line = lines[0] if lines else ""
    return line if len(line) <= limit else line[:limit] + "…"


DRY_PROBE_NOTE = ("預演不送按鍵，角色不會動，所以自動探邊量不到範圍、停下來是正常的；"
                  "正式執行才會真的去量。想在預演裡看打怪決策，巡邏點先改成「指定巡邏點」")


def _ended(reason: str, dry: bool = False) -> Tuple[str, str, str, Optional[Tuple[str, str]]]:
    """收工原因 -> (狀態字, tone, 說明, 動作)。原因字串是 runner 寫的 stop_reason。"""
    if dry and reason.startswith("PANIC：") and "探邊" in reason:
        # 預演時探邊一定失敗（不按方向鍵），runner 那句「確認方向鍵有送進遊戲、
        # 系統管理員」在這裡完全是誤導——使用者正是在驗證設定的時候看到它
        return "已停止", "muted", DRY_PROBE_NOTE, None
    if reason.startswith("PANIC："):
        return "已停機", "bad", reason[len("PANIC："):], ("anomalies", "開啟異常畫面")
    if reason.startswith("遊戲行程結束"):
        return "已停機", "bad", "遊戲行程結束（當掉、被關掉或被踢下線）", None
    if reason.startswith(("達到最長運行時間", "跑滿 max_ticks")):
        return "已收工", "ok", reason, None
    return "已停止", "muted", reason or "這一場已結束", None


class Monitor:
    """跨 poll 的記憶：讓過從什麼時候開始、tick 最後一次變動是什麼時候。

    讓過要連續 wait_after 秒才顯示「讓過中」：抵達巡邏點那種 0.2 秒的
    Wait 每幾秒就有一次，直接顯示的話卡片會一直閃。
    """

    def __init__(self, wait_after: float = 3.0, stale_after: float = 5.0):
        self.wait_after = wait_after
        self.stale_after = stale_after
        self.wait_since: Optional[float] = None
        self.tick_changed_at: float = 0.0
        self._ticks = -1
        self._run = None

    def _track(self, st, active: bool, now: float) -> None:
        if not active or st.started_at != self._run:
            # 暫停、啟動中、換了一場：從現在重新起算，不然暫停一小時回來
            # 第一個 poll 就會被判成「畫面沒更新」
            self._run = st.started_at
            self._ticks = st.ticks
            self.tick_changed_at = now
        elif st.ticks != self._ticks:
            self._ticks = st.ticks
            self.tick_changed_at = now
        if active and st.action == "Wait":
            if self.wait_since is None:
                self.wait_since = now
        else:
            self.wait_since = None

    def view(self, st, ctx: Context, lim: Limits, now: Optional[float] = None) -> View:
        now = time.monotonic() if now is None else now
        alive = ctx.thread_alive
        active = (alive and not ctx.stopping and st.preflight == "ok" and st.running
                  and not st.ended_at and not st.paused)
        self._track(st, active, now)

        start_text = "▶ 開始預演" if ctx.dry_choice else "▶ 開始打怪"
        start = ("start", start_text, ctx.loaded)
        locked_start = ("start", start_text, False)
        action: Optional[Tuple[str, str]] = None
        frozen = ""

        if not ctx.loaded and not alive:
            phase, state, tone = "not_loaded", "設定讀不到", "bad"
            name = re.split(r"[\\/]", ctx.load_path)[-1] if ctx.load_path else "設定檔"
            detail = f"{name}：{_first_line(ctx.load_error)}。修好之前不能儲存"
            action = ("notepad", "用記事本開啟") if ctx.load_path else None
            primary = locked_start
        elif ctx.tool_label:
            phase, state, tone = "tool", f"{ctx.tool_label}中…", "tool"
            detail = _TOOL_DETAIL.get(ctx.tool_label, _TOOL_DETAIL_DEFAULT)
            primary = locked_start
        elif ctx.recording:
            phase, state, tone = "recording", "錄製中", "info"
            secs, tracked, n = ctx.record_info or (0.0, 0, 0)
            detail = ("在遊戲裡照平常走一趟，走完回來按「■ 停止錄製」"
                      f"（{secs:.0f} 秒、{tracked}/{n} 幀看得到角色）")
            primary = locked_start
        elif alive:
            phase, state, tone, detail, action, primary = self._alive(st, ctx, lim, now)
        else:
            phase, state, tone, detail, action = self._idle(st, ctx, lim)
            primary = start

        if phase in PAUSED_PHASES:
            frozen = "（暫停前）"
        elif phase in ENDED_PHASES + ("failed", "cancelled", "idle") and st.ticks > 0:
            frozen = "（停止時）"
        perceiving = phase in ("running", "waiting", "stale")

        locked = alive or ctx.recording or bool(ctx.tool_label)
        if ctx.tool_label:
            lock_text = f"！{ctx.tool_label}中：等它的視窗關掉再改設定"
        elif ctx.recording:
            lock_text = "！錄製中：停止錄製之後才能改設定"
        elif alive:
            lock_text = "！執行中：設定在按下開始時就讀進去了，要改請先 ■ 停止"
        else:
            lock_text = ""

        run_time = elapsed = ""
        if alive and st.started_at and not st.ended_at:
            ran = now - st.started_at
            run_time = duration(ran)
            elapsed = f"已跑 {run_time}"
            if lim.max_runtime > 0:
                elapsed += f"・剩 {duration(max(lim.max_runtime - ran, 0))}"

        blank = st.ticks == 0        # 還沒感知過任何一幀：顯示「—」而不是嚇人的「讀不到」
        meters = tuple(
            Meter(None, "—", "muted", m.marks) if blank and m.ratio is None
            else (m if perceiving else Meter(m.ratio, m.text, "muted", m.marks))
            for m in (meter(st.hp, "hp", lim), meter(st.mp, "mp", lim),
                      meter(st.exp, "exp", lim)))
        lines = tuple(line if perceiving else (line[0], "muted")
                      for line in (scene_line(st), exp_line(st), perf_line(st)))

        show_end = phase in ("ended", "not_started", "crashed") and st.preflight == "ok"
        return View(
            phase=phase, state=state, tone=tone, detail=detail, action=action,
            primary=primary, can_stop=alive, settings_locked=locked, lock_text=lock_text,
            dry_tag=st.dry_run if alive else ctx.dry_choice,
            run_time=run_time, elapsed=elapsed, meters=meters, frozen=frozen, lines=lines,
            summary=end_summary(st) if show_end else "",
            report=st.report_path if phase in ("ended", "crashed") else "",
            open_vision=ctx.vision_ready, generation=ctx.generation)

    def _alive(self, st, ctx: Context, lim: Limits, now: float):
        pause_btn = ("pause", "‖ 暫停", True)
        if ctx.stopping or (st.preflight == "ok" and st.ended_at):
            return ("stopping", "正在停止…", "muted", "放開按鍵、寫收工報告…", None,
                    ("start", "正在停止…", False))
        if st.preflight in ("", "checking") or (st.preflight == "ok" and not st.running):
            return ("starting", "啟動中", "info", "開場自檢：確認血條、小地圖讀得到…", None,
                    ("start", "啟動中…", False))
        if st.preflight == "fail":
            phase, state, tone, detail, action = self._not_started(ctx)
            return phase, state, tone, detail, action, ("start", "啟動中…", False)
        if st.paused:
            reason = st.pause_reason
            resume = ("resume", "▶ 繼續", True)
            if st.paused_by_user or not reason:
                return ("paused_user", "已暫停", "warn",
                        f"{reason or '已暫停'}。按 ▶ 繼續 或 {lim.pause_key}", None, resume)
            return ("paused_auto", "自動暫停", "bad",
                    f"{reason}。看一下遊戲畫面，沒問題就按 ▶ 繼續 或 {lim.pause_key}",
                    ("anomalies", "開啟異常畫面"), resume)
        quiet = now - self.tick_changed_at
        if quiet >= self.stale_after:
            return ("stale", "畫面沒更新", "warn",
                    f"已 {int(quiet)} 秒沒有新的 tick——黑屏、換圖中，或擷取卡住",
                    None, pause_btn)
        if self.wait_since is not None and now - self.wait_since >= self.wait_after:
            return ("waiting", "讓過中", "warn",
                    f"{st.reason or '讓過'}（已 {int(now - self.wait_since)} 秒）",
                    None, pause_btn)
        return ("running", "執行中", "ok", f"決策：{action_text(st.action, st.reason)}",
                None, pause_btn)

    @staticmethod
    def _not_started(ctx: Context):
        if ctx.last_error:
            detail = f"開場自檢沒過：{gui_text(headline(ctx.last_error))}"
        else:
            detail = "開場自檢沒過，詳細內容在日誌"
        return "not_started", "沒開始", "bad", detail, problem_for(ctx.last_error)

    def _idle(self, st, ctx: Context, lim: Limits):
        """執行緒沒在跑：上一場（或上一個工具）的結局要一直留著，直到下一次開始。"""
        if ctx.start_error:
            return ("not_started", "沒開始", "bad", ctx.start_error,
                    problem_for(ctx.start_error))
        if ctx.tool_failed:
            label, line = ctx.tool_failed
            act = TOOL_ACTION.get(label)
            return ("failed", "沒有完成", "bad", f"{label}沒有完成：{line or '原因看日誌'}",
                    (act, "再試一次") if act else None)
        if ctx.tool_cancelled:
            label = ctx.tool_cancelled
            act = TOOL_ACTION.get(label)
            if label == "截角色名牌":
                return ("cancelled", "沒有存到", "warn",
                        "沒有存到名牌（框選視窗直接按了 c 或被關掉）", ("nametag", "再截一次"))
            return ("cancelled", "沒有存到", "warn",
                    f"{label}沒有存到任何東西（視窗直接按了 c 或被關掉）",
                    (act, "再試一次") if act else None)
        if ctx.show_run:
            if ctx.crash:
                return ("crashed", "程式出錯", "bad",
                        f"{headline(ctx.crash)}。詳細內容在日誌", None)
            if st.preflight == "fail":
                return self._not_started(ctx)
            if st.ended_at:
                state, tone, detail, action = _ended(st.stop_reason, st.dry_run)
                return "ended", state, tone, detail, action
        return ("idle", "待命", "muted",
                f"設定好就按 ▶ 開始。遊戲裡 {lim.pause_key} 暫停/繼續、{lim.stop_key} 停止",
                None)


def title_text(view: View, map_name: str, dirty: bool) -> str:
    """主視窗標題：縮到工作列、或被遊戲蓋住時，Alt-Tab 也看得到狀態。"""
    star = " *" if dirty else ""
    name = map_name or "—"
    if view.phase in RUNNING_PHASES:
        head = f"▶ {'預演・' if view.dry_tag else ''}{view.state}"
        if view.run_time:
            head += f" {view.run_time}"
        hp = view.meters[0].text if view.meters and view.meters[0].ratio is not None else "—"
        return f"{head}｜HP {hp}｜{name}｜mapleai{star}"
    if view.phase in PAUSED_PHASES:
        return f"‖ {view.state}｜{name}｜mapleai{star}"
    if view.phase in ENDED_PHASES:
        return f"■ {view.state}｜{name}｜mapleai{star}"
    if view.phase in ("tool", "recording"):
        return f"● {view.state}｜{name}｜mapleai{star}"
    return f"mapleai 控制台 — {name}{star}"


# ---------------------------------------------------------------------------
# 日誌
# ---------------------------------------------------------------------------

class LogLine(str):
    """日誌的一行。本身就是 str（舊程式與測試把它當字串用），另外帶分級。

    level: 'info' | 'warn' | 'error'；kind: 'normal' | 'tool' | 'tick'。
    """
    level: str
    kind: str

    def __new__(cls, text: str, level: str = "info", kind: str = "normal"):
        obj = super().__new__(cls, text)
        obj.level = level
        obj.kind = kind
        return obj


def classify(record: Any) -> Tuple[str, str]:
    """LogRecord -> (level, kind)。kind 來自 extra：runner 的 tick、工具輸出的 tool。"""
    no = getattr(record, "levelno", 20)
    level = "error" if no >= 40 else "warn" if no >= 30 else "info"
    if getattr(record, "tick", False):
        kind = "tick"
    elif getattr(record, "tool", False):
        kind = "tool"
    else:
        kind = "normal"
    return level, kind


def visible(line: LogLine, problems_only: bool) -> bool:
    """「只看問題」打勾時只留 WARN 以上。"""
    return not problems_only or getattr(line, "level", "info") in ("warn", "error")


# ---------------------------------------------------------------------------
# 表單問題與自檢判讀（給 view 直接顯示）
# ---------------------------------------------------------------------------

def field_label(key: str) -> str:
    """欄位名、'buff3'、'buffs' -> 給人看的名字。"""
    if key in settings.SPECS:
        return settings.SPECS[key].label
    if key == "buffs":
        return "循環 buff"
    m = re.fullmatch(r"buff(\d+)", key)
    return f"buff {m.group(1)}" if m else key


def _errors(problems: Dict[str, Any]) -> List[str]:
    order = [k for k in settings.FORM_ORDER if k in problems]
    order += [k for k in problems if k not in order]
    return [k for k in order if problems[k].level == "error"]


def problem_line(problems: Dict[str, Any]) -> str:
    """表單下面那一行：「× 2 個欄位要修正才能儲存：水平距離、buff 3」；沒錯誤是 ''。"""
    errs = _errors(problems)
    if not errs:
        return ""
    return f"× {len(errs)} 個欄位要修正才能儲存：" + "、".join(field_label(k) for k in errs)


def problem_list(problems: Dict[str, Any], limit: int = 5) -> str:
    """「還不能開始」對話框的內文：每個錯誤一行，最多 limit 行。"""
    errs = _errors(problems)
    lines = [f"・{problems[k].text}" for k in errs[:limit]]
    if len(errs) > limit:
        lines.append(f"…還有 {len(errs) - limit} 項")
    return "\n".join(lines)


_VERDICT_BUTTONS = {"calibrate": "重新校正", "nametag": "截名牌", "tab:vision": "到辨識分頁"}


def verdict_rows(verdicts) -> List[Tuple[str, str, str, Optional[Tuple[str, str]]]]:
    """selfcheck.verdicts() -> [(符號, tone, 文字, (動作代號, 按鈕字) 或 None)]。"""
    rows = []
    for status, text, act in verdicts:
        button = None
        if act:
            label = _VERDICT_BUTTONS.get(act, "處理")
            if act == "nametag" and "比不中" in text:
                label = "重截名牌"
            button = (act, label)
        rows.append((STATUS_GLYPH.get(status, "・"), STATUS_TONE.get(status, "plain"),
                     tk_safe(gui_text(text)), button))
    return rows


def symbols_ok(text: str) -> bool:
    """只用 BMP、沒有 U+FE0F、符號類（So）只出現白名單裡的。測試與 view 都可以用。"""
    for ch in text:
        if ord(ch) > 0xFFFF or ch == "️":
            return False
        if unicodedata.category(ch) == "So" and ch not in ALLOWED_SYMBOLS:
            return False
    return True
