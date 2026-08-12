"""GUI 的大腦：開關 bot、錄製路線、存讀設定、收集 log。

完全不碰 tkinter——視窗那層只負責把欄位讀出來丟進這裡、再把狀態畫回去。
這樣整個「按下開始會發生什麼」都能離線測試，不用開視窗也不用開遊戲。
"""
import logging
import os
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Tuple

from ..capture import ImageCapture, WindowCapture
from ..config import ConfigError, load_config, load_profile, resolve_local_path
from ..control.input_win import IS_WINDOWS, Keyboard, NullBackend
from ..perception import Perceiver
from ..recorder import KeyWatcher, Recorder
from ..route import compress, coverage, describe, to_yaml_block
from ..runner import Runner, Status
from ..vision.mobs import make_detector
from . import settings

LOG_LINES = 400


class _BufferHandler(logging.Handler):
    """把 log 收進 deque 給 UI 顯示。"""

    def __init__(self, lines: Deque[str]):
        super().__init__()
        self.lines = lines
        self.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
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


class Controller:
    def __init__(self, config_path: str = "config/default.yaml",
                 profile_path: str = "config/profiles/example.yaml",
                 logger: Optional[logging.Logger] = None,
                 capture_factory: Optional[Callable] = None):
        self.config_path = config_path
        self.profile_path = profile_path
        self.lines: Deque[str] = deque(maxlen=LOG_LINES)
        self.log = logger or logging.getLogger("maplebot")
        self.log.addHandler(_BufferHandler(self.lines))
        self._capture_factory = capture_factory
        self.cfg = None
        self.profile = None
        self.runner: Optional[Runner] = None
        self._thread: Optional[threading.Thread] = None
        self._rec_thread: Optional[threading.Thread] = None
        self._rec_stop = threading.Event()
        self.recorder: Optional[Recorder] = None
        self.last_route: str = ""
        self.error: str = ""

    # ---- 設定 ----

    def load(self) -> bool:
        try:
            self.cfg = load_config(self.config_path)
            self.profile = load_profile(self.profile_path)
        except ConfigError as e:
            self.error = str(e)
            self.log.error("設定錯誤: %s", e)
            return False
        self.error = ""
        self.log.info("已載入設定: %s", " + ".join(self.cfg.sources))
        return True

    def values(self) -> Dict[str, object]:
        return settings.from_config(self.cfg, self.profile)

    def buff_rows(self) -> List[Tuple[str, str]]:
        return settings.buff_rows(self.profile)

    def save(self, values: Dict[str, object], buff_rows) -> bool:
        """寫回 local.yaml 與 profile，然後重新載入（確保存進去的真的讀得回來）。"""
        local, prof = settings.to_yaml(
            values, settings.buffs_from(buff_rows),
            self.profile.name if self.profile else "my map")
        try:
            settings.merge_into(resolve_local_path(self.config_path), local)
            settings.merge_into(self.profile_path, prof)
        except OSError as e:
            self.error = f"存檔失敗: {e}"
            self.log.error(self.error)
            return False
        self.log.info("設定已存檔（%s、%s）",
                      resolve_local_path(self.config_path), self.profile_path)
        return self.load()

    # ---- 執行 ----

    @property
    def busy(self) -> bool:
        return self.running or self.recording

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

    def start(self, dry_run: bool = False) -> bool:
        if self.busy or not self.cfg:
            return False
        try:
            capture = self._capture()
        except Exception as e:
            self.error = f"找不到遊戲視窗: {e}"
            self.log.error(self.error)
            return False
        keyboard = Keyboard(NullBackend() if (dry_run or not IS_WINDOWS) else None)
        detector = make_detector(self.cfg.vision, self.profile.templates_dir, self.log)
        self.runner = Runner(self.cfg, self.profile, capture, keyboard, detector,
                             self.log, dry_run=dry_run)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        try:
            self.runner.run()
        except Exception as e:                       # 執行緒裡爆掉要看得到
            self.log.exception("執行時發生例外: %s", e)

    def stop(self) -> None:
        if self.runner:
            self.runner.safety.stop = True
        self._rec_stop.set()

    def toggle_pause(self) -> bool:
        if not self.runner:
            return False
        self.runner.safety.paused = not self.runner.safety.paused
        self.log.info("已%s", "暫停" if self.runner.safety.paused else "繼續")
        return self.runner.safety.paused

    # ---- 錄製路線 ----

    def start_record(self) -> bool:
        if self.busy or not self.cfg:
            return False
        try:
            capture = self._capture()
        except Exception as e:
            self.error = f"找不到遊戲視窗: {e}"
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
        """停止錄製，回傳可以貼進 profile 的 patrol 區塊（也存進 last_route）。"""
        self._rec_stop.set()
        if self._rec_thread:
            self._rec_thread.join(timeout=2.0)
        if not self.recorder or not self.recorder.samples:
            self.log.warning("沒有錄到任何畫面")
            return ""
        rec = self.recorder
        if rec.tracked < max(len(rec.samples) // 2, 4):
            self.log.warning(
                "錄製期間有 %d/%d 幀找不到小地圖玩家點，路線可能不完整——"
                "先用 tools/debug_view.py --snapshot 確認小地圖 ROI",
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
                "跨距太小的話也可能是小地圖 ROI 框錯了（用「辨識自檢」確認）",
                len(points))
        return self.waypoints_text(points)

    @staticmethod
    def waypoints_text(points) -> str:
        """把巡邏點轉成 UI 那格文字（單層地圖就是 "30, 90"）。"""
        if not points:
            return ""
        if all(p.y is None and not p.keys and not p.descend for p in points):
            return ", ".join(str(p.x) for p in points)
        import yaml
        return yaml.safe_dump([p.to_dict() for p in points], allow_unicode=True,
                              default_flow_style=True).strip()

    # ---- 外部工具 ----

    def run_tool(self, argv: List[str], label: str,
                 then_reload: bool = False) -> bool:
        """在背景跑 tools/ 底下的腳本，輸出收進系統日誌。

        校正要用 OpenCV 的框選視窗、偵錯要開另一個視窗，都不適合塞進 tkinter
        的事件迴圈，開子行程最乾淨。
        """
        if self.busy:
            self.error = "執行中，請先停止"
            return False
        threading.Thread(target=self._tool_thread, args=(argv, label, then_reload),
                         daemon=True).start()
        return True

    def _tool_thread(self, argv: List[str], label: str, then_reload: bool) -> None:
        import subprocess
        import sys
        self.log.info("執行%s…（會另外開一個視窗，完成後回到這裡）", label)
        # 子行程寫到 pipe 時用的是系統地區編碼（繁中 Windows 是 cp950），
        # 這邊卻照 UTF-8 解——中文訊息就會整段變亂碼。強制兩邊都講 UTF-8。
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        try:
            proc = subprocess.run([sys.executable, *argv], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  env=env)
        except OSError as e:
            self.log.error("%s 啟動失敗: %s", label, e)
            return
        for line in (proc.stdout or "").splitlines():
            if line.strip():
                self.log.info("  %s", line.rstrip())
        for line in _useful_stderr(proc.stderr):
            self.log.warning("  %s", line)
        if proc.returncode != 0:
            self.log.error("%s 失敗（return code %d）", label, proc.returncode)
            return
        self.log.info("%s 完成", label)
        if then_reload:
            self.load()

    def calibrate(self) -> bool:
        return self.run_tool(
            ["tools/calibrate.py", "--config", self.config_path, "--write"],
            "ROI 校正", then_reload=True)

    def check_vision(self, out: str = "logs/check.png") -> bool:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        return self.run_tool(
            ["tools/debug_view.py", "--config", self.config_path,
             "--profile", self.profile_path, "--snapshot", out],
            f"辨識自檢（結果存到 {out}）")

    # ---- 給 UI 取用 ----

    def drain_logs(self) -> List[str]:
        out = list(self.lines)
        self.lines.clear()
        return out

    def profiles(self) -> List[str]:
        d = os.path.dirname(self.profile_path) or "."
        try:
            return sorted(os.path.join(d, f) for f in os.listdir(d)
                          if f.endswith((".yaml", ".yml")))
        except OSError:
            return [self.profile_path]
