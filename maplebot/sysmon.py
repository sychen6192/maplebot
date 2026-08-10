"""遊戲行程與系統資源監看（slow loop，每 interval 秒一次）。

會多這一層不是為了好看的 CPU 曲線，是為了兩個主迴圈**看不到**的失敗：

1. **遊戲不見了**——當掉、被關掉、或帳號被踢下線。視窗一消失，擷取要嘛
   丟例外要嘛抓到後面那個視窗，而 bot 還在對著桌面按技能鍵、把方向鍵
   送進別的程式。「行程還在不在」是所有訊號裡最不會誤判的一個：
   比黑屏偵測直接（讀圖也會黑），比「找不到玩家點」直接（換圖也會找不到）。

2. **機器扛不住**——CPU 滿載時主迴圈會從 8 FPS 掉到 2 FPS。這時「反應變鈍」
   不是辨識參數的錯，但少了這個數字，人就會跑去調 mob_match_threshold。

psutil 沒裝就整層停用（`available == False`），主迴圈完全不受影響——
這是選配依賴，不該讓「想單純掛機的人」被迫裝一堆東西。
"""
import os
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

try:
    import psutil
    HAVE_PSUTIL = True
except ImportError:                                   # pragma: no cover - 取決於環境
    psutil = None                                     # type: ignore[assignment]
    HAVE_PSUTIL = False

IS_WINDOWS = sys.platform == "win32"

# 同一類警報最短重報間隔。CPU 高檔會連續好幾分鐘，每 5 秒吵一次沒人會看
ALERT_COOLDOWN = 120.0


@dataclass
class SysSnapshot:
    """一次取樣。全部是純量，直接進報告的 JSON。"""
    at: float                                # monotonic
    game_alive: bool = False
    game_pid: Optional[int] = None
    game_cpu: Optional[float] = None         # %（單顆核心為 100%）
    game_mem_mb: Optional[float] = None
    game_uptime: Optional[float] = None      # 秒
    bot_cpu: Optional[float] = None
    bot_mem_mb: Optional[float] = None
    sys_cpu: Optional[float] = None
    sys_mem: Optional[float] = None          # %
    disk: Optional[float] = None             # %

    def as_dict(self) -> dict:
        def r(v, n=1):
            return None if v is None else round(v, n)
        return {"at": round(self.at, 1), "game_alive": self.game_alive,
                "game_pid": self.game_pid, "game_cpu": r(self.game_cpu),
                "game_mem_mb": r(self.game_mem_mb), "game_uptime": r(self.game_uptime, 0),
                "bot_cpu": r(self.bot_cpu), "bot_mem_mb": r(self.bot_mem_mb),
                "sys_cpu": r(self.sys_cpu), "sys_mem": r(self.sys_mem),
                "disk": r(self.disk)}


def evaluate(snap: SysSnapshot, cpu_threshold: float, mem_threshold: float,
             game_mem_threshold_mb: float) -> List[Tuple[str, str]]:
    """把一次取樣翻成 (類型, 訊息) 清單。純函式——門檻邏輯要測得到。

    刻意不在這裡做冷卻或記錄，那是呼叫端的事；這裡只回答
    「以這一瞬間的數字來看，有什麼該講的」。
    """
    out: List[Tuple[str, str]] = []
    if snap.sys_cpu is not None and snap.sys_cpu >= cpu_threshold:
        out.append(("sys_cpu",
                    f"系統 CPU {snap.sys_cpu:.0f}%——主迴圈會開始掉幀，"
                    f"反應變慢先看這裡再去調辨識參數"))
    if snap.sys_mem is not None and snap.sys_mem >= mem_threshold:
        out.append(("sys_mem",
                    f"系統記憶體 {snap.sys_mem:.0f}%——接近開始吃 swap，"
                    f"擷取延遲會突然變很長"))
    if (game_mem_threshold_mb > 0 and snap.game_mem_mb is not None
            and snap.game_mem_mb >= game_mem_threshold_mb):
        out.append(("game_mem",
                    f"遊戲行程佔用 {snap.game_mem_mb:.0f}MB，"
                    f"超過 {game_mem_threshold_mb:.0f}MB。長時間掛機的客戶端會愈吃愈多，"
                    f"重開遊戲通常就好"))
    return out


class SystemMonitor:
    """慢迴圈監看器。poll() 每個 tick 都可以呼叫，內部自己節流。"""

    def __init__(self, cfg, logger=None):
        self.cfg = cfg
        self.log = logger
        self.available = HAVE_PSUTIL and cfg.enabled
        self.game_lost = False           # 一旦確認遊戲行程消失就閂住
        self.last: Optional[SysSnapshot] = None
        self.history: List[SysSnapshot] = []
        self._proc = None                # psutil.Process（遊戲）
        self._self = None                # psutil.Process（我們自己）
        self._next_at = 0.0
        self._fired: dict = {}           # 類型 -> 上次發出的時間（冷卻用）
        self._peak_game_mem = 0.0
        self._peak_sys_cpu = 0.0
        if self.available:
            try:
                self._self = psutil.Process(os.getpid())
                self._self.cpu_percent(None)      # 第一次呼叫一定回 0.0，先丟掉
            except Exception:                     # pragma: no cover - 權限問題
                self._self = None

    # ---- 綁定遊戲行程 ----

    def attach_window(self, hwnd: int) -> Optional[int]:
        """從遊戲視窗 handle 取得 PID——比用名字猜可靠得多。

        名字比對在「開了兩個客戶端」或「客戶端執行檔叫 MapleStory.exe 但
        視窗標題是中文」時都會抓錯人。我們手上本來就有正在擷取的那個
        視窗，直接問它屬於誰即可。
        """
        if not self.available or not IS_WINDOWS or not hwnd:
            return None
        import ctypes
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return self.attach_pid(int(pid.value)) if pid.value else None

    def attach_pid(self, pid: int) -> Optional[int]:
        if not self.available or not pid:
            return None
        try:
            self._proc = psutil.Process(pid)
            self._proc.cpu_percent(None)
            if self.log:
                self.log.info("已鎖定遊戲行程 %s (PID %d)", self._proc.name(), pid)
            return pid
        except Exception as e:                    # pragma: no cover - 權限/競態
            if self.log:
                self.log.debug("無法鎖定遊戲行程 PID %d: %s", pid, e)
            self._proc = None
            return None

    def attach_by_name(self, hint: str) -> Optional[int]:
        """退路：用行程名關鍵字找。多個符合時挑吃最多記憶體的那個
        （遊戲主行程一定比 launcher/crash handler 肥）。"""
        if not self.available or not hint:
            return None
        best, best_mem = None, -1.0
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if hint.lower() not in (p.info["name"] or "").lower():
                    continue
                mem = p.memory_info().rss
                if mem > best_mem:
                    best, best_mem = p.info["pid"], mem
            except Exception:
                continue
        return self.attach_pid(best) if best else None

    # ---- 取樣 ----

    def _sample(self, now: float) -> SysSnapshot:
        snap = SysSnapshot(at=now)
        if self._proc is not None:
            try:
                with self._proc.oneshot():
                    snap.game_alive = self._proc.is_running()
                    snap.game_pid = self._proc.pid
                    snap.game_cpu = self._proc.cpu_percent(None)
                    snap.game_mem_mb = self._proc.memory_info().rss / 1048576.0
                    snap.game_uptime = time.time() - self._proc.create_time()
            except Exception:
                # NoSuchProcess / AccessDenied 都走這裡。前者是「遊戲關了」，
                # 後者是「我們權限不夠」——分不出來時一律當成沒了比較安全：
                # 誤判的代價是停機，漏判的代價是對著桌面亂按。
                snap.game_alive = False
        if self._self is not None:
            try:
                snap.bot_cpu = self._self.cpu_percent(None)
                snap.bot_mem_mb = self._self.memory_info().rss / 1048576.0
            except Exception:                     # pragma: no cover
                pass
        try:
            # interval=None 是「跟上次呼叫比」的非阻塞版本。用 interval=1
            # 會讓主迴圈整整停一秒——監看不該比被監看的東西還吵
            snap.sys_cpu = psutil.cpu_percent(None)
            snap.sys_mem = psutil.virtual_memory().percent
            snap.disk = psutil.disk_usage(os.path.abspath(os.sep)).percent
        except Exception:                         # pragma: no cover
            pass
        return snap

    def poll(self, now: float) -> Optional[SysSnapshot]:
        """時候到了就取樣，否則回 None。回傳的 snapshot 已經記進 history。"""
        if not self.available or now < self._next_at:
            return None
        self._next_at = now + max(self.cfg.interval, 1.0)
        snap = self._sample(now)
        self.last = snap
        self.history.append(snap)
        if len(self.history) > 4096:              # 掛很久也不要無限長
            del self.history[:1024]
        if snap.game_mem_mb:
            self._peak_game_mem = max(self._peak_game_mem, snap.game_mem_mb)
        if snap.sys_cpu:
            self._peak_sys_cpu = max(self._peak_sys_cpu, snap.sys_cpu)
        if self._proc is not None and not snap.game_alive:
            self.game_lost = True
        return snap

    def alerts(self, snap: SysSnapshot) -> List[Tuple[str, str]]:
        """套用門檻並過掉冷卻期內的重複警報。"""
        out = []
        for kind, msg in evaluate(snap, self.cfg.cpu_threshold, self.cfg.mem_threshold,
                                  self.cfg.game_mem_threshold_mb):
            if snap.at - self._fired.get(kind, -1e9) < ALERT_COOLDOWN:
                continue
            self._fired[kind] = snap.at
            out.append((kind, msg))
        return out

    # ---- 報告 ----

    def summary(self) -> str:
        if not self.available:
            return "系統監看未啟用" + ("" if HAVE_PSUTIL else "（未安裝 psutil）")
        s = self.last
        if s is None:
            return "系統監看尚未取樣"
        parts = []
        if s.game_mem_mb is not None:
            parts.append(f"遊戲 {s.game_cpu:.0f}% CPU / {s.game_mem_mb:.0f}MB")
        if s.bot_mem_mb is not None:
            parts.append(f"bot {s.bot_cpu:.0f}% CPU / {s.bot_mem_mb:.0f}MB")
        if s.sys_cpu is not None:
            parts.append(f"系統 {s.sys_cpu:.0f}% CPU / {s.sys_mem:.0f}% RAM")
        return "｜".join(parts) if parts else "系統監看無資料"

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "psutil": HAVE_PSUTIL,
            "game_lost": self.game_lost,
            "peak_game_mem_mb": round(self._peak_game_mem, 1) or None,
            "peak_sys_cpu": round(self._peak_sys_cpu, 1) or None,
            "last": self.last.as_dict() if self.last else None,
        }
