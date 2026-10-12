"""GUI 的大腦：開關 bot、錄製路線、存讀設定、跑外部工具、收集 log。

完全不碰 tkinter——視窗那層只負責把欄位讀出來丟進這裡、再把狀態畫回去。
這樣整個「按下開始會發生什麼」都能離線測試，不用開視窗也不用開遊戲。

執行緒規則（Tk 是單執行緒的）：runner、錄製、工具、環境檢查與 log handler
都跑在背景執行緒，它們只能**整個物件一次賦值**（self.readiness = …）或
往 deque 裡 append。畫面每 200 ms 呼叫一次 poll()，那是唯一的橋。
"""
import copy
import importlib.util
import itertools
import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Tuple

import yaml

from .. import window
from ..capture import WindowCapture
from ..config import ConfigError, load_config, load_profile, resolve_local_path
from ..control.input_win import IS_WINDOWS, Keyboard, NullBackend
from ..diagnostics import doctor
from ..diagnostics.selfcheck import VisionReport, verdicts
from ..perception import Perceiver
from ..recording.recorder import KeyWatcher, Recorder
from ..recording.route import compress, coverage, describe, to_yaml_block
from ..runner import Runner, Status
from ..safety import ANOMALY_DIR
from ..vision.mobs import make_detector
from . import live, settings, setup
from .live import LogLine

HISTORY_LINES = 3000     # 「只看問題」從這裡重畫；開著對話框時 poll 停住也不會無限長
FACTS_TTL = 3.0          # 準備清單裡「遊戲視窗在不在」多久重查一次

GUI_PAUSE = "你在控制台按了暫停"
GUI_STOP = "你在控制台按了停止"
NOT_LOADED = "設定檔讀不起來，修好之前不會存檔（以免把壞掉的檔案蓋成預設值）"

# 多技能 profile 只讀 skills:，表單這幾格寫進 attack: 也不會生效
ATTACK_FIELDS = ("attack_key", "attack_type", "attack_seconds", "attack_repeat",
                 "attack_range", "attack_vrange")

# 控制台自己的視窗（標題含這些字）不能出現在遊戲視窗選單裡
_OWN_TITLES = ("mapleai", "maplebot")


class _BufferHandler(logging.Handler):
    """把 log 收進 deque 給 UI 顯示。

    只收 INFO 以上：log.setup() 把 logger 開在 DEBUG（檔案要全寫），沒設
    level 的話實戰中 executor 每個 tick 的「等待:」「巡邏：往…走」也會進來。
    dry-run 每 tick 一行（extra tick=True）預設也擋掉，檔案 log 照寫。
    """

    def __init__(self, owner: "Controller"):
        super().__init__(level=logging.INFO)
        self.owner = owner

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level, kind = live.classify(record)
            if kind == "tick" and not self.owner.keep_ticks:
                return
            msg = record.getMessage()
            stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
            line = LogLine(live.tk_safe(f"[{stamp}] {msg}"), level, kind)
            self.owner.lines.append(line)
            self.owner.history.append(line)
            if level == "error":
                self.owner.last_error = msg
        except Exception:      # log 壞掉不能拖垮 bot
            pass


def _useful_stderr(text: Optional[str], keep: int = 6) -> List[str]:
    """從 traceback 裡挑出人看得懂的部分。

    整串 traceback 貼進系統日誌只會把真正的錯誤訊息推出畫面——真正有用的是
    最後那幾行（例外類別與訊息，我們的錯誤訊息本來就寫成給人看的）。
    """
    lines = []
    for line in (text or "").splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith(("File \"", "Traceback (")) or line.startswith("    "):
            continue                       # 檔名行與程式碼行都不用看
        lines.append(stripped)
    return lines[-keep:]


def game_window_candidates(titles) -> List[str]:
    """遊戲視窗選單的內容：去掉空白標題與控制台自己，含「楓」或 maple 的排前面。"""
    seen = set()
    out = []
    for t in titles:
        t = (t or "").strip()
        low = t.lower()
        if not t or t in seen or any(own in low for own in _OWN_TITLES):
            continue
        seen.add(t)
        out.append(t)
    return sorted(out, key=lambda t: 0 if ("楓" in t or "maple" in t.lower()) else 1)


def title_conflicts(needle: str, titles) -> List[str]:
    """標題含 needle（不分大小寫）的所有視窗，順序照 titles。find_game_window 抓第一個。"""
    n = (needle or "").strip().lower()
    return [t for t in titles if n and n in (t or "").lower()]


def _mtime(path: Optional[str]) -> Optional[float]:
    try:
        return os.path.getmtime(path) if path else None
    except OSError:
        return None


def _default_open(path: str) -> None:
    if IS_WINDOWS:
        os.startfile(path)                 # type: ignore[attr-defined]
    else:
        import pathlib
        import webbrowser
        webbrowser.open(pathlib.Path(path).as_uri())


class Controller:
    def __init__(self, config_path: str = "config/default.yaml",
                 profile_path: str = "config/profiles/example.yaml",
                 logger: Optional[logging.Logger] = None,
                 capture_factory: Optional[Callable] = None,
                 state_path: Optional[str] = None,
                 opener: Optional[Callable[[str], None]] = None):
        self.config_path = config_path
        self.profile_path = profile_path
        # lines 是還沒畫上去的（drain_logs 取走），history 是最近 3000 行
        self.lines: Deque[LogLine] = deque(maxlen=HISTORY_LINES)
        self.history: Deque[LogLine] = deque(maxlen=HISTORY_LINES)
        self.keep_ticks = False            # 「含每個 tick」
        self.last_error = ""               # 這次開始以來最後一筆 ERROR 的訊息
        self.log = logger or logging.getLogger("maplebot")
        self.log.addHandler(_BufferHandler(self))
        self._capture_factory = capture_factory
        self._opener = opener

        self.cfg = None
        self.profile = None
        self.loaded_ok = False
        self.load_error = ""
        self.load_path = ""                # 讀不起來的那個檔
        self._baseline: Dict[str, str] = {}
        self._baseline_buffs: List[Tuple[str, str]] = []
        self._limits = live.Limits()
        self._backups: set = set()         # 這個 session 已經備份過的檔（write_patch）

        self.runner: Optional[Runner] = None
        self._thread: Optional[threading.Thread] = None
        self._rec_thread: Optional[threading.Thread] = None
        self._rec_stop = threading.Event()
        self.recorder: Optional[Recorder] = None
        self.last_route: str = ""
        self.error: str = ""
        self.error_code: str = ""          # NOT_LOADED | BUSY | INVALID | TEMPLATE_READONLY | IO
        self.dry_choice = True             # 執行卡的模式選擇（view 寫入）

        # 執行卡要留著的結局：直到下一次開始或跑工具才清掉
        self.crash = ""
        self.start_error = ""
        self.tool_label = ""
        self.tool_failed: Optional[Tuple[str, str]] = None
        self.tool_cancelled = ""
        self._show_run = False
        self._stopping = False
        self._tool_thread_obj: Optional[threading.Thread] = None

        self.monitor = live.Monitor()
        self._gen = itertools.count(1)
        self.generation = 0

        self.state_path = state_path or setup.state_path(config_path)
        self.state = setup.load_state(self.state_path)
        self._state_lock = threading.Lock()

        self.vision_result: Optional[VisionReport] = None
        self._vision_new = False
        self._vision_fp = ""
        self._dry_recorded = False
        self._facts_cache: Optional[Tuple[int, float, setup.SetupFacts]] = None
        self.readiness = None              # doctor.run_all 的結果；None = 還沒跑完
        self._readiness_thread: Optional[threading.Thread] = None

    def _bump(self) -> None:
        # next() 在 CPython 是原子的：背景執行緒與主執行緒同時遞增也不會掉號
        self.generation = next(self._gen)

    # ---- 設定 ----

    def load(self) -> bool:
        """讀全域設定與 profile。讀不起來就鎖住存檔（loaded_ok = False）。

        校正工具結束後會在工具執行緒裡呼叫：新的 cfg、profile、基準全部先算好
        再逐一賦值，畫面那邊任何時刻讀到的都是完整的物件。
        """
        cfg = None
        try:
            cfg = load_config(self.config_path)
            profile = load_profile(self.profile_path)
        except (ConfigError, OSError, ValueError, TypeError, AttributeError,
                yaml.YAMLError) as e:
            # 全域設定讀得起來就留著（視窗標題、熱鍵還用得到）；profile 一定
            # 清掉——以前會留著上一份舊的，存檔時就拿舊值蓋新檔
            self.cfg = cfg
            self.profile = None
            self._baseline, self._baseline_buffs = {}, []
            self._limits = live.limits_from(cfg, None)
            self.load_path = self.config_path if cfg is None else self.profile_path
            self.load_error = str(e)
            self.loaded_ok = False
            self.error = str(e)
            self.log.error("設定錯誤: %s", e)
            self._bump()
            return False
        values = settings.from_config(cfg, profile)
        baseline = {f: settings.normalize(f, v) for f, v in values.items()}
        buffs = settings.buff_rows(profile)
        self.cfg = cfg
        self.profile = profile
        self._baseline = baseline
        self._baseline_buffs = buffs
        self._limits = live.limits_from(cfg, profile)
        self.load_error = self.load_path = ""
        self.loaded_ok = True
        self.error = ""
        self.log.info("已載入設定: %s", " + ".join(cfg.sources + [self.profile_path]))
        self._remember_profile()
        self._bump()
        return True

    def can_edit(self) -> bool:
        """表單能不能改：設定讀得起來、而且沒有在跑 bot / 錄製 / 工具。"""
        return self.loaded_ok and not self.busy

    @property
    def is_template(self) -> bool:
        return settings.is_template(self.profile_path)

    @property
    def map_name(self) -> str:
        if self.profile is not None:
            return self.profile.name
        return os.path.splitext(os.path.basename(self.profile_path))[0]

    @property
    def buff_overflow(self) -> int:
        """UI 只有 8 組，profile 多出來的組數（存檔時原封不動保留）。"""
        return max(len(self.profile.buffs) - settings.BUFF_SLOTS, 0) if self.profile else 0

    def values(self) -> Dict[str, object]:
        if not self.loaded_ok:
            return settings.defaults()
        return settings.from_config(self.cfg, self.profile)

    def buff_rows(self) -> List[Tuple[str, str]]:
        if not self.loaded_ok:
            return [("", "")] * settings.BUFF_SLOTS
        return settings.buff_rows(self.profile)

    def locked(self) -> Dict[str, str]:
        """表單上唯讀的欄位 -> 原因。用 skills: 的 profile 攻擊欄位寫了也沒用。"""
        if not self.profile or not self.profile.skills:
            return {}
        skills = self.profile.skills
        note = (f"這份地圖設定用 skills: 多技能輪替（{len(skills)} 招："
                f"{'、'.join(s.key for s in skills)}），這裡只顯示第一招——"
                "要改請直接編輯 YAML")
        return {f: note for f in ATTACK_FIELDS}

    def _editable(self, values: Dict[str, object]) -> Dict[str, object]:
        locked = self.locked()
        return {k: v for k, v in values.items() if k not in locked}

    def changes(self, values: Dict[str, object], buff_rows) -> List[str]:
        """跟檔案裡不一樣的欄位（FIELDS 順序），buff 有改再加 'buffs'。"""
        if not self.loaded_ok:
            return []
        out = settings.changed_fields(self._baseline, self._editable(values))
        if buff_rows is not None and settings.buff_rows_changed(buff_rows,
                                                                self._baseline_buffs):
            out.append("buffs")
        return out

    def describe_changes(self, values: Dict[str, object], buff_rows, limit: int = 3) -> str:
        """「攻擊鍵、水平距離、巡邏點 等 5 項」；沒改是 ''。"""
        names = [live.field_label(f) for f in self.changes(values, buff_rows)]
        if len(names) <= limit:
            return "、".join(names)
        return "、".join(names[:limit]) + f" 等 {len(names)} 項"

    def baseline_value(self, field: str) -> str:
        """檔案裡的值（tooltip 的「已修改（檔案裡是 320）」）。'buff3' 是「8 每 120 秒」。"""
        if field.startswith("buff") and field[4:].isdigit():
            i = int(field[4:]) - 1
            if 0 <= i < len(self._baseline_buffs):
                key, secs = self._baseline_buffs[i]
                try:                        # profile 讀進來是 120.0，表單上是 120
                    x = float(secs)
                    secs = str(int(x)) if x.is_integer() else str(x)
                except ValueError:
                    pass
                return f"{key} 每 {secs} 秒" if key or secs else ""
            return ""
        return self._baseline.get(field, "")

    def _hotkeys(self) -> Optional[Dict[str, str]]:
        if self.cfg is None:
            return None
        return {self.cfg.safety.pause_key: "暫停", self.cfg.safety.stop_key: "停止"}

    def problems(self, values: Dict[str, object], buff_rows) -> Dict[str, settings.Problem]:
        """每個欄位的問題。改過的欄位超出範圍是錯誤；沒動過的只提醒（舊地圖照樣能跑）。"""
        if not self.loaded_ok:
            return {}
        return settings.validate(self._editable(values), buff_rows,
                                 changed=self.changes(values, buff_rows),
                                 hotkeys=self._hotkeys())

    def _fail(self, code: str, msg: str) -> bool:
        self.error, self.error_code = msg, code
        self.log.error("%s", msg)
        return False

    def _writable(self) -> bool:
        self.error, self.error_code = "", ""
        if not self.loaded_ok:
            return self._fail("NOT_LOADED", NOT_LOADED)
        if self.busy:
            what = self.tool_label or ("錄製" if self.recording else "bot")
            return self._fail("BUSY", f"{what}還在執行，停下來之後才能存檔")
        return True

    def _validated(self, values, buff_rows, changed: List[str]) -> bool:
        problems = settings.validate(self._editable(values), buff_rows, changed=changed,
                                     hotkeys=self._hotkeys())
        first = settings.first_error(problems)
        if first is None:
            return True
        return self._fail("INVALID", f"{live.problem_line(problems)[2:]}。{problems[first].text}")

    def _write(self, values, buff_rows, changed: List[str], profile: bool) -> Optional[List[str]]:
        """把改過的欄位寫進檔案。回傳寫了哪些檔（可能是空的）；寫檔失敗回 None。"""
        vals = self._editable(values)
        send = settings.with_coupled(f for f in changed if f != "buffs")
        buffs = (settings.buffs_from(buff_rows, self.profile.buffs)
                 if "buffs" in changed else None)
        local, prof = settings.to_yaml({k: vals[k] for k in send if k in vals},
                                       buffs, self.profile.name)
        # 名稱只有另存時才會改（save_as 自己處理）。留著的話 profile 沒有 name
        # 時會被寫進 'unnamed'，而且純量補丁找不到那一行就整份重寫、洗掉註解
        prof.pop("name", None)
        if not profile:
            prof = {}
        targets = [(self.profile_path, prof), (resolve_local_path(self.config_path), local)]
        written = []
        try:
            # profile 先寫：它是手改的、比較可能有問題；寫失敗時 local.yaml 還沒動
            for path, patch in targets:
                if not patch:
                    continue
                bak = settings.write_patch(path, patch, self._backups)
                written.append(path)
                if bak:
                    self.log.warning("%s 的註解沒辦法原地保留，改成整份重寫；舊檔備份在 %s",
                                     os.path.basename(path), bak)
        except (OSError, yaml.YAMLError) as e:
            if written:
                # 前一個檔已經寫進去了：不重新載入的話畫面還把它當「未儲存」，
                # 使用者選「不儲存」關掉，以為什麼都沒寫，其實地圖已經改了
                done = "、".join(os.path.basename(p) for p in written)
                self.load()
                self._fail("IO", f"只存了一半：{done} 已經寫進去，但下一個檔沒存到（{e}）")
            else:
                self._fail("IO", f"存檔失敗: {e}")
            return None
        if written:
            # 範本只寫 Config 那一半：列全部的話日誌會說「水平距離 320 → 111
            # （寫入 local.yaml）」，看起來像範本的修改存進去了，其實還沒有
            shown = changed if profile else [f for f in changed if f in settings.LOCAL_FIELDS]
            self.log.info("已存檔：%s（寫入 %s）", self._change_text(values, shown),
                          "、".join(os.path.basename(p) for p in written))
        return written

    def _change_text(self, values, changed: List[str], limit: int = 5) -> str:
        parts = []
        for f in changed[:limit]:
            if f in ("buffs", "waypoints"):
                parts.append(f"{live.field_label(f)}（已改）")
                continue
            old = settings.display(f, self._baseline.get(f, "")) or "（空）"
            new = settings.display(f, values.get(f, "")) or "（空）"
            parts.append(f"{live.field_label(f)} {old} → {new}")
        text = "、".join(parts)
        return text + (f" 等 {len(changed)} 項" if len(changed) > limit else "")

    def save(self, values: Dict[str, object], buff_rows) -> bool:
        """只寫改過的欄位，寫之前先驗證，寫完重新載入（確保讀得回來）。

        沒有任何修改就什麼都不寫——以前每按一次開始就整份重寫 profile，
        巡邏點的 tolerance / grab、buff 的施放時間、所有註解都被洗掉。
        範本只寫 Config 的部分，Profile 有改就回 False（error_code
        TEMPLATE_READONLY），由畫面問要不要另存為自己的地圖。
        """
        if not self._writable():
            return False
        changed = self.changes(values, buff_rows)
        if not changed:
            return True
        if not self._validated(values, buff_rows, changed):
            return False
        if self.is_template and settings.PROFILE_FIELDS & set(changed):
            written = self._write(values, buff_rows, changed, profile=False)
            if written is None:
                return False
            if written:
                self.load()
            self.error = (f"「{self.map_name}」是範本，存進去會改到大家共用的範例檔——"
                          "另存成你自己的地圖")
            self.error_code = "TEMPLATE_READONLY"
            self.log.warning("%s", self.error)
            return False
        if self._write(values, buff_rows, changed, profile=True) is None:
            return False
        return self.load()

    def save_as(self, name: str, values: Dict[str, object], buff_rows) -> Optional[str]:
        """另存為我的地圖：複製目前的 profile（註解跟著走）、改名、套用修改、切換過去。

        回傳新檔路徑；失敗回 None（self.error 說明原因）。範本本身一個 byte 都不動。
        """
        if not self._writable():
            return None
        changed = self.changes(values, buff_rows)
        if changed and not self._validated(values, buff_rows, changed):
            return None
        folder = os.path.dirname(self.profile_path) or "."
        try:
            path = settings.profile_filename(name, folder)
        except ValueError as e:
            self._fail("INVALID", str(e))
            return None
        src, src_name, was_template = self.profile_path, self.map_name, self.is_template
        try:
            with open(src, "r", encoding="utf-8", newline="") as f:
                text = f.read()
            new = settings.patch_scalars(text, {"name": name.strip()})
            if new is None:
                # 沒有 name 那一行（或寫法特殊）：加在最前面，其餘照抄
                eol = "\r\n" if "\r\n" in text else "\n"
                new = f"name: {yaml.safe_dump(name.strip(), allow_unicode=True).splitlines()[0]}{eol}" + text
            with open(path, "x", encoding="utf-8", newline="") as f:
                f.write(new)
        except (OSError, yaml.YAMLError) as e:
            self._fail("IO", f"另存失敗: {e}")
            return None
        self.profile_path = path
        ok = changed == [] or self._write(values, buff_rows, changed, profile=True) is not None
        err = (self.error, self.error_code)
        self.load()
        if not ok:
            # 檔案已經建好、也切過去了，只是修改沒寫進去：留著原因給畫面看
            self.error, self.error_code = err
            return None
        self.log.info("已建立 %s（從「%s」複製%s）", path, src_name,
                      "；範本沒有被修改" if was_template else "")
        return path

    def switch_profile(self, path: str) -> bool:
        """換一張地圖。讀不起來也會切過去（畫面顯示原因、鎖住存檔），再換別張就好。"""
        if self.busy:
            self.error = "執行中不能換地圖，先停止"
            return False
        same = os.path.normcase(os.path.abspath(path)) == \
            os.path.normcase(os.path.abspath(self.profile_path))
        if same and self.loaded_ok:
            return True
        self.profile_path = path
        return self.load()

    def profile_choices(self) -> List[Tuple[str, str]]:
        """地圖選單 [(名稱, 路徑)]：自己的地圖在前、範本在後。目前這張一定在裡面。"""
        folder = os.path.dirname(self.profile_path) or "."
        items = settings.profile_choices(folder)
        here = os.path.normcase(os.path.abspath(self.profile_path))
        if not any(os.path.normcase(os.path.abspath(p)) == here for _l, p in items):
            items.insert(0, (self.map_name, self.profile_path))
        return items

    def profiles(self) -> List[str]:
        d = os.path.dirname(self.profile_path) or "."
        try:
            return sorted(os.path.join(d, f) for f in os.listdir(d)
                          if f.endswith((".yaml", ".yml")))
        except OSError:
            return [self.profile_path]

    # ---- 控制台自己的狀態（gui_state.json）----

    def remember(self, **items) -> bool:
        """記進 gui_state.json（geometry、last_tab…）。整份換掉再寫，背景執行緒也能呼叫。"""
        with self._state_lock:
            state = dict(self.state)
            state.update(items)
            self.state = state
            return setup.save_state(self.state_path, state)

    def _remember_profile(self) -> None:
        path = os.path.abspath(self.profile_path)
        if self.state.get("last_profile") != path:
            self.remember(last_profile=path)

    def _profile_key(self) -> str:
        return os.path.basename(self.profile_path)

    def regions_fp(self) -> str:
        """目前校正結果的指紋；minimap: auto 算成 'auto'（執行時解出來的座標不算）。"""
        if self.cfg is None:
            return ""
        regions = dict(self.cfg.regions)
        if self.cfg.minimap_auto:
            regions["minimap"] = "auto"
        return setup.regions_fingerprint(regions, self.cfg.calibrated_for)

    # ---- 執行 ----

    @property
    def busy(self) -> bool:
        return self.running or self.recording or bool(self.tool_label)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def recording(self) -> bool:
        return self._rec_thread is not None and self._rec_thread.is_alive()

    def status(self) -> Status:
        return self.runner.status if self.runner else Status()

    def _capture(self):
        if self._capture_factory:
            return self._capture_factory(self.cfg)
        if not IS_WINDOWS:
            raise RuntimeError("即時擷取只支援 Windows")
        return WindowCapture(self.cfg.window_title, self.cfg.capture_method,
                             self.log)

    def _window_error(self, e: Exception) -> str:
        if not IS_WINDOWS and not self._capture_factory:
            return "這台不是 Windows：即時擷取只支援 Windows，只能用截圖離線測試"
        return f"找不到遊戲視窗「{self.cfg.window_title}」。遊戲要先開，並用視窗模式"

    def start(self, dry_run: bool = False) -> bool:
        self.error, self.error_code = "", ""
        if self.running:
            self.error = "已經在執行中"
            return False
        if self.recording:
            self.error = "錄製中，先停止錄製"
            return False
        if self.tool_label:
            self.error = f"工具還在執行（{self.tool_label}）"
            return False
        if not self.loaded_ok or not self.cfg:
            self.error, self.error_code = NOT_LOADED, "NOT_LOADED"
            return False
        # 檔案裡的值：必填、巡邏點、喝藥線、熱鍵衝突不論有沒有改都要擋
        problems = settings.validate(self._editable(self.values()), self.buff_rows(),
                                     changed=[], hotkeys=self._hotkeys())
        if settings.first_error(problems):
            self.error = "還不能開始：\n" + live.problem_list(problems)
            self.error_code = "INVALID"
            return False

        self.crash = self.start_error = self.last_error = self.tool_cancelled = ""
        self.tool_failed = None
        self._show_run, self._stopping, self._dry_recorded = True, False, False
        self.monitor = live.Monitor()
        try:
            capture = self._capture()
        except Exception as e:
            self.start_error = self._window_error(e)
            self.error = self.start_error
            self.log.error("%s（%s）", self.start_error, e)
            self._bump()
            return False
        try:
            keyboard = Keyboard(NullBackend() if (dry_run or not IS_WINDOWS) else None)
            detector = make_detector(self.cfg.vision, self.profile.templates_dir, self.log)
            # 給 runner 一份自己的 cfg：minimap: auto 會在執行時改寫 regions，
            # 不該回頭改到表單與準備清單看的那一份
            self.runner = Runner(copy.deepcopy(self.cfg), self.profile, capture, keyboard,
                                 detector, self.log, dry_run=dry_run)
        except Exception as e:
            self.start_error = f"準備執行時出錯：{e}"
            self.error = self.start_error
            self.log.error("%s", self.start_error)
            self._bump()
            return False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        try:
            self.runner.run()
        except Exception as e:                       # 執行緒裡爆掉要看得到
            self.crash = f"{type(e).__name__}: {e}"
            self.log.exception("執行時發生例外: %s", e)
        finally:
            self._maybe_record_dry(self.runner.status)
            self._bump()

    def _maybe_record_dry(self, st: Status) -> None:
        """預演跑到開場自檢通過、40 個 tick 以上，就記下「這張地圖預演過」。"""
        if (self._dry_recorded or not self.runner or not self.runner.dry_run
                or st.preflight != "ok" or st.ticks < setup.DRY_OK_TICKS):
            return
        self._dry_recorded = True
        with self._state_lock:
            state = dict(self.state)
            dry = dict(state.get("dry_ok") or {})
            dry[self._profile_key()] = self.regions_fp()
            state["dry_ok"] = dry
            self.state = state
            setup.save_state(self.state_path, state)
        self._bump()

    def stop(self, reason: str = GUI_STOP) -> None:
        if self.runner:
            if self.running:
                self._stopping = True
            self.runner.safety.request_stop(reason)
        self._rec_stop.set()

    def pause(self) -> bool:
        """暫停（明確的，不是切換）。已經在暫停就什麼都不做、回 False。

        不用切換：watchdog 剛自動暫停、使用者的點擊緊接著落下時，切換會把它
        默默地繼續下去。
        """
        if not self.runner or not self.running:
            return False
        if self.runner.safety.pause(GUI_PAUSE, by_user=True):
            self.log.info("已暫停（%s）", GUI_PAUSE)
            return True
        return False

    def resume(self) -> bool:
        if not self.runner or not self.running:
            return False
        if self.runner.safety.resume():
            self.log.info("繼續執行")
            return True
        return False

    def toggle_pause(self) -> bool:
        """舊的切換介面（保留給既有呼叫端）。回傳切換後是否在暫停。"""
        if not self.runner:
            return False
        if self.runner.safety.paused:
            self.resume()
        else:
            self.pause()
        return self.runner.safety.paused

    # ---- 錄製路線 ----

    @property
    def record_seconds(self) -> float:
        return self.recorder.seconds if self.recorder else 0.0

    @property
    def record_tracked(self) -> int:
        return self.recorder.tracked if self.recorder else 0

    def start_record(self) -> bool:
        if self.busy or not self.loaded_ok:
            self.error = NOT_LOADED if not self.loaded_ok else "執行中，請先停止"
            return False
        try:
            capture = self._capture()
        except Exception as e:
            self.error = self._window_error(e)
            self.log.error(self.error)
            return False
        perceiver = Perceiver(self.cfg, make_detector(self.cfg.vision,
                                                      self.profile.templates_dir))

        def sample(now: float):
            state = perceiver.perceive(capture.grab(), now)
            return state.minimap_xy if state.minimap_xy else (None, None)

        self.recorder = Recorder(sample, KeyWatcher())
        self._rec_stop.clear()
        self._rec_thread = threading.Thread(target=self._record_loop, daemon=True)
        self._rec_thread.start()
        self.log.info("開始錄製：照平常的方式走一趟路線，走完按「停止錄製」")
        return True

    def _record_loop(self) -> None:
        interval = 1.0 / max(self.cfg.fps, 1.0)
        self.recorder.start()
        while not self._rec_stop.is_set():
            t0 = time.monotonic()
            try:
                self.recorder.step()
            except Exception as e:
                self.log.error("錄製中斷: %s", e)
                break
            time.sleep(max(interval - (time.monotonic() - t0), 0.0))

    def stop_record(self) -> str:
        """停止錄製，回傳可以貼進巡邏點欄位的文字（patrol 區塊另存在 last_route）。"""
        self._rec_stop.set()
        if self._rec_thread:
            self._rec_thread.join(timeout=2.0)
        if not self.recorder or not self.recorder.samples:
            self.log.warning("沒有錄到任何畫面")
            return ""
        rec = self.recorder
        if rec.tracked < max(len(rec.samples) // 2, 4):
            self.log.warning(
                "錄製期間有 %d/%d 幀找不到小地圖上的角色黃點，路線可能不完整——"
                "先到「準備 › 4 辨識自檢」確認小地圖 region",
                len(rec.samples) - rec.tracked, len(rec.samples))
        points = compress(rec.samples, tolerance=self.profile.patrol.tolerance,
                          y_tolerance=self.profile.patrol.y_tolerance,
                          jump_key=self.profile.patrol.jump_key)
        self.last_route = to_yaml_block(points)
        self.log.info("錄製結束：%.0f 秒、%d 幀 -> %s",
                      rec.seconds, len(rec.samples), coverage(points))
        self.log.info("錄到的路線：%s", describe(points))
        if len(points) < 2:
            self.log.warning(
                "只錄到 %d 個點——角色要真的左右走到底才量得出巡邏範圍。"
                "跨距太小的話也可能是小地圖 region 框錯了（用「辨識自檢」確認）",
                len(points))
        return self.waypoints_text(points)

    @staticmethod
    def waypoints_text(points) -> str:
        """把巡邏點轉成 UI 那格文字：單層地圖是 "30, 90"，多層一行一個點。"""
        if not points:
            return ""
        dicts = [p.to_dict() for p in points]
        if all(list(d) == ["x"] for d in dicts):
            return ", ".join(str(p.x) for p in points)
        return settings.waypoint_lines(dicts)

    # ---- 外部工具 ----

    def run_tool(self, argv: List[str], label: str, then_reload: bool = False,
                 watch: Optional[str] = None,
                 after: Optional[Callable[[], None]] = None) -> bool:
        """在背景跑 tools/ 底下的腳本，輸出收進系統日誌。一次只跑一個。

        校正要用 OpenCV 的框選視窗、偵錯要開另一個視窗，都不適合塞進 tkinter
        的事件迴圈，開子行程最乾淨。以前可以同時開兩個校正視窗搶寫
        local.yaml，校正視窗開著也能按開始。

        watch：工具成功時要產生的檔案。結束時 mtime 沒變就算「沒有存到」
        （框選視窗按 c 也是 return code 0）。after：成功時在工具執行緒裡呼叫。
        """
        self.error = ""
        if self.tool_label:
            self.error = f"{self.tool_label}還在執行，等它的視窗關掉再試"
            return False
        if self.running or self.recording:
            self.error = "執行中，請先停止"
            return False
        self.tool_label = label
        self.tool_failed = None
        self.tool_cancelled = self.start_error = self.crash = ""
        self._show_run = False
        self._bump()
        t = threading.Thread(target=self._tool_thread,
                             args=(argv, label, then_reload, watch, after), daemon=True)
        self._tool_thread_obj = t
        t.start()
        return True

    def _tool_thread(self, argv: List[str], label: str, then_reload: bool,
                     watch: Optional[str], after: Optional[Callable[[], None]]) -> None:
        tool = {"tool": True}
        try:
            self.log.info("執行%s…（會另外開一個視窗，完成後回到這裡）", label)
            before = _mtime(watch)
            # 子行程寫到 pipe 時用的是系統地區編碼（繁中 Windows 是 cp950），
            # 這邊卻照 UTF-8 解——中文訊息就會整段變亂碼。強制兩邊都講 UTF-8。
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
            try:
                proc = subprocess.run([sys.executable, *argv], capture_output=True,
                                      text=True, encoding="utf-8", errors="replace",
                                      env=env)
            except OSError as e:
                self.tool_failed = (label, str(e))
                self.log.error("%s 啟動失敗: %s", label, e)
                return
            out = [ln.rstrip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
            for line in out:
                self.log.info("  %s", line, extra=tool)
            err = _useful_stderr(proc.stderr)
            for line in err:
                self.log.warning("  %s", line, extra=tool)
            if proc.returncode != 0:
                last = err[-1] if err else (out[-1].strip() if out else
                                            f"return code {proc.returncode}")
                self.tool_failed = (label, last)
                self.log.error("%s 失敗（return code %d）", label, proc.returncode)
                return
            if watch and setup.tool_outcome(before, _mtime(watch), 0) == "cancelled":
                self.tool_cancelled = label
                self.log.warning("%s沒有存到任何東西（框選視窗直接按了 c 或被關掉）", label)
            else:
                self.log.info("%s 完成", label)
            if after is not None:
                after()
            if then_reload:
                self.load()
        finally:
            self.tool_label = ""
            self._bump()

    def calibrate(self) -> bool:
        return self.run_tool(
            ["tools/calibrate.py", "--config", self.config_path, "--write"],
            "校正", then_reload=True)

    def grab_nametag(self) -> bool:
        """截角色名牌（準備 › 3）。成敗看名牌檔的 mtime 有沒有變。

        只要 Config 讀得到就能截：名牌綁的是這台電腦與角色，跟地圖設定檔壞不壞無關。
        """
        if self.cfg is None:
            self.error = "全域設定讀不起來，先修好 config/default.yaml 或 local.yaml"
            return False
        if self.busy:
            self.error = "執行中，請先停止"
            return False
        ui_dir = self.cfg.vision.ui_templates_dir
        return self.run_tool(
            ["tools/grab_template.py", "--config", self.config_path,
             "--dir", ui_dir, "--name", "player_nametag"],
            "截角色名牌", watch=os.path.join(ui_dir, "player_nametag.png"))

    def check_vision(self, out: str = "logs/check.png") -> bool:
        """辨識自檢：子行程拍一張、寫出標註圖與 JSON 報告，成功後讀回 vision_result。"""
        if self.busy:
            self.error = "執行中，請先停止"
            return False
        if not self.loaded_ok:
            # 自檢要照地圖設定畫攻擊範圍、跑一次決策，設定檔讀不起來就做不了
            self.error = f"地圖設定讀不起來，自檢需要它：{self.load_error}"
            return False
        report = os.path.splitext(out)[0] + ".json"
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        try:
            os.remove(report)           # 舊報告絕不能被當成這一次的結果
        except OSError:
            pass
        self._vision_fp = self.regions_fp()
        return self.run_tool(
            ["tools/debug_view.py", "--config", self.config_path,
             "--profile", self.profile_path, "--snapshot", out, "--report", report],
            "辨識自檢", after=lambda: self.read_vision_report(report))

    def read_vision_report(self, path: str) -> bool:
        """讀 debug_view --report 寫的 JSON；成功就讓下一次 poll 打開結果視窗。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                report = VisionReport.from_json(f.read())
        except (OSError, ValueError, TypeError) as e:
            self.log.warning("讀不到辨識自檢報告 %s：%s", path, e)
            return False
        self.vision_result = report
        self._vision_new = True
        return True

    def vision_verdicts(self):
        """[(status, 文字, 動作代號)]，見 selfcheck.verdicts；沒有報告是 []。"""
        return verdicts(self.vision_result) if self.vision_result else []

    def confirm_vision(self) -> bool:
        """「看起來都對 √」：記下自檢時的校正指紋。還有 × 的項目時不收。"""
        if self.vision_result is None:
            self.error = "還沒有辨識自檢的結果"
            return False
        if any(status == "fail" for status, _t, _a in self.vision_verdicts()):
            self.error = "還有 × 的項目，先修好再確認"
            return False
        fp = self._vision_fp or self.regions_fp()
        self.remember(vision_ok={"fp": fp, "at": time.time()})
        self._bump()
        return True

    # ---- 準備清單 ----

    def window_titles(self) -> List[str]:
        """遊戲視窗選單的候選標題（非 Windows 是空的）。"""
        try:
            return game_window_candidates(window.other_window_titles())
        except Exception:
            return []

    def facts(self, now: Optional[float] = None) -> setup.SetupFacts:
        """準備清單要的事實。generation 沒變就 3 秒才重查一次（找視窗要列舉所有視窗）。"""
        now = time.monotonic() if now is None else now
        cached = self._facts_cache
        if cached and cached[0] == self.generation and now - cached[1] < FACTS_TTL:
            return cached[2]
        f = self._collect_facts()
        self._facts_cache = (self.generation, now, f)
        return f

    def _collect_facts(self) -> setup.SetupFacts:
        cfg = self.cfg
        found, size, matches = "", None, ()
        if cfg is not None and window.IS_WINDOWS:
            try:
                win = window.find_game_window(cfg.window_title)
                if win is not None:
                    found, size = win.title, tuple(win.size)
                matches = tuple(title_conflicts(cfg.window_title,
                                                window.other_window_titles()))
            except Exception:
                pass
        nametag = False
        if cfg is not None:
            ui = cfg.vision.ui_templates_dir
            nametag = any(os.path.exists(os.path.join(ui, n)) for n in doctor.NAMETAG_FILES)
        fp = self.regions_fp()
        state = self.state
        return setup.SetupFacts(
            loaded=self.loaded_ok, load_error=self.load_error,
            windows=window.IS_WINDOWS,
            window_title=cfg.window_title if cfg is not None else "",
            window_found=found, window_size=size, title_matches=matches,
            calibrated_for=cfg.calibrated_for if cfg is not None else None,
            nametag=nametag, regions_fp=fp,
            vision_fp=str((state.get("vision_ok") or {}).get("fp", "")),
            profile_name=self.map_name, profile_file=os.path.basename(self.profile_path),
            template=self.is_template,
            waypoints=self._baseline.get("waypoints", "auto"),
            dry_ok=bool(fp) and (state.get("dry_ok") or {}).get(self._profile_key()) == fp,
            tool=self.tool_label)

    def steps(self) -> List[setup.Step]:
        return setup.evaluate(self.facts())

    def refresh_readiness(self) -> bool:
        """在背景跑一次環境檢查（doctor.run_all），結果整個指給 self.readiness。

        importer 用 find_spec 不真的 import：ultralytics 一 import 就連帶載入
        torch，要好幾秒。已經在跑就不重複開。
        """
        if self._readiness_thread is not None and self._readiness_thread.is_alive():
            return False
        cfg_path, prof_path = self.config_path, self.profile_path

        def work():
            try:
                self.readiness = doctor.run_all(
                    cfg_path, prof_path,
                    importer=lambda m: importlib.util.find_spec(m) is not None)
            except Exception as e:      # 自檢本身出錯不能拖垮控制台
                self.readiness = [("執行環境", [doctor.Check(
                    "環境檢查", doctor.FAIL, f"檢查本身出錯：{e}", "")])]

        self._readiness_thread = threading.Thread(target=work, daemon=True)
        self._readiness_thread.start()
        return True

    # ---- 給 UI 取用 ----

    def _context(self) -> live.Context:
        rec = None
        if self.recording and self.recorder is not None:
            rec = (self.recorder.seconds, self.recorder.tracked, len(self.recorder.samples))
        alive = self.running
        return live.Context(
            thread_alive=alive, stopping=self._stopping and alive,
            recording=self.recording, record_info=rec,
            tool_label=self.tool_label, tool_failed=self.tool_failed,
            tool_cancelled=self.tool_cancelled,
            loaded=self.loaded_ok, load_error=self.load_error, load_path=self.load_path,
            dry_choice=self.dry_choice, last_error=self.last_error, crash=self.crash,
            start_error=self.start_error, show_run=self._show_run,
            vision_ready=self._vision_new, generation=self.generation)

    def poll(self, now: Optional[float] = None) -> Tuple[live.View, List[LogLine]]:
        """畫面每 200 ms 呼叫一次：(執行卡內容, 新的 log 行)。不做檔案 I/O。"""
        now = time.monotonic() if now is None else now
        st = self.status()
        self._maybe_record_dry(st)
        ctx = self._context()
        if ctx.vision_ready:
            self._vision_new = False
        return self.monitor.view(st, ctx, self._limits, now), self.drain_logs()

    def drain_logs(self) -> List[LogLine]:
        # 一行一行 popleft：以前 list() 再 clear()，兩步之間別的執行緒 append
        # 進來的那幾行就直接不見了
        out: List[LogLine] = []
        while True:
            try:
                out.append(self.lines.popleft())
            except IndexError:
                return out

    def history_lines(self) -> List[LogLine]:
        """最近 3000 行的快照（「只看問題」重畫用）。"""
        for _ in range(5):
            try:
                return list(self.history)
            except RuntimeError:       # 複製途中被 append：再試一次
                continue
        return []

    def open_path(self, path: str, opener: Optional[Callable[[str], None]] = None) -> bool:
        """用系統預設程式開檔案或資料夾（Windows 是 os.startfile，其他平台交給瀏覽器）。"""
        if not path:
            return False
        path = os.path.abspath(path)
        try:
            (opener or self._opener or _default_open)(path)
            return True
        except Exception as e:
            self.log.warning("打不開 %s：%s", path, e)
            return False

    def open_action(self, action: str) -> bool:
        """執行卡上「開東西」類的動作：notepad | anomalies | logs | report。"""
        if action == "notepad":
            path = self.load_path or self.profile_path
            if self.open_path(path):
                return True
            if IS_WINDOWS:                  # 沒有關聯程式時退回記事本
                try:
                    subprocess.Popen(["notepad.exe", os.path.abspath(path)])
                    return True
                except OSError:
                    return False
            return False
        if action == "anomalies":
            return self.open_path(ANOMALY_DIR if os.path.isdir(ANOMALY_DIR) else "logs")
        if action == "logs":
            os.makedirs("logs", exist_ok=True)
            return self.open_path("logs")
        if action == "report":
            return self.open_path(self.status().report_path)
        return False
