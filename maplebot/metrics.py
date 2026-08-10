"""每個 tick 的耗時分解、實際頻率，以及給報告用的取樣序列。

設 `loop.fps: 8` 的意思是「每 125ms 跑一輪」，但那是**預算**不是保證。
擷取加辨識真的超過預算時，迴圈只會安靜地變慢——log 上完全看不出來，
使用者只覺得「反應好像有點鈍」，然後跑去調辨識參數。

所以這裡量的是**分段**耗時，不是只有一個總 FPS：「慢」本身不可行動，
「慢在遠端推理那一段」才可行動。

`execute` 那一段例外：它包含按鍵按住的時間（cast_seconds、走位的
step_seconds），本來就該是幾百毫秒，不是效能問題。判斷是否超支時
要把它扣掉，否則每個報告都會說「execute 最慢」而那是廢話。
"""
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional

# execute 的耗時是「刻意的等待」（按住技能鍵/方向鍵），不算效能瓶頸
BLOCKING_STAGES = frozenset({"execute"})

# 各階段慢下來時，該去看哪裡。key 對應 runner 裡 stage() 用的名字。
STAGE_HINTS: Dict[str, str] = {
    "capture": "擷取變慢通常是視窗太大或 window.capture 用 printwindow："
               "改小遊戲視窗，或在 config 設 window.capture: screen 比較快",
    "perceive": "辨識變慢多半是怪物偵測：設 vision.mob_search_box（只看角色周圍）、"
                "調高 vision.mob_interval 降低偵測頻率，或改用 yolo/onnx 路線",
    "decide": "決策是純函式，慢到會被看見很不尋常——請開 issue 附上這份報告",
    "monitor": "系統監看變慢代表 psutil 掃行程很吃力：調高 monitor.interval",
}


@dataclass
class Stage:
    """單一階段的滾動統計。只留最近 N 筆，長時間掛機才不會愈算愈鈍。"""
    name: str
    samples: Deque[float] = field(default_factory=lambda: deque(maxlen=240))

    def add(self, seconds: float) -> None:
        self.samples.append(seconds)

    @property
    def avg_ms(self) -> float:
        return 1000.0 * sum(self.samples) / len(self.samples) if self.samples else 0.0

    @property
    def max_ms(self) -> float:
        return 1000.0 * max(self.samples) if self.samples else 0.0

    def pct_ms(self, q: float = 0.95) -> float:
        """第 q 百分位。平均值會被偶爾的長尾拉不動，但長尾才是造成掉幀的元凶。"""
        if not self.samples:
            return 0.0
        ordered = sorted(self.samples)
        idx = min(int(q * len(ordered)), len(ordered) - 1)
        return 1000.0 * ordered[idx]


class LoopMetrics:
    """主迴圈的碼表。純計算，不碰 I/O，所以測得到。"""

    def __init__(self, target_fps: float, window: int = 240,
                 clock: Callable[[], float] = time.monotonic):
        self.target_fps = max(target_fps, 0.1)
        self.budget = 1.0 / self.target_fps
        self._clock = clock
        self._window = window
        self.stages: Dict[str, Stage] = {}
        self.totals: Deque[float] = deque(maxlen=window)
        self.tick_ends: Deque[float] = deque(maxlen=window)
        self.ticks = 0
        self.overruns = 0          # 扣掉 execute 後仍然超出預算的 tick 數

    # ---- 記錄 ----

    def _stage(self, name: str) -> Stage:
        st = self.stages.get(name)
        if st is None:
            st = Stage(name, deque(maxlen=self._window))
            self.stages[name] = st
        return st

    @contextmanager
    def stage(self, name: str):
        start = self._clock()
        try:
            yield
        finally:
            self._stage(name).add(self._clock() - start)

    def record(self, name: str, seconds: float) -> None:
        """已經自己量好時間時用這個（例如量了一段不方便包 with 的程式碼）。"""
        self._stage(name).add(seconds)

    def tick(self, total_seconds: float, working_seconds: Optional[float] = None) -> None:
        """一輪結束。working_seconds 是扣掉刻意等待後的實作業時間。"""
        self.ticks += 1
        self.totals.append(total_seconds)
        self.tick_ends.append(self._clock())
        if working_seconds is None:
            working_seconds = total_seconds
        if working_seconds > self.budget:
            self.overruns += 1

    # ---- 讀取 ----

    @property
    def fps(self) -> float:
        """實際頻率，用 tick 之間的真實時間差算——不是把耗時倒數回去。

        兩者的差別在「主動 sleep 補足預算」的那段時間：倒數回去會報出
        一個永遠達標的漂亮數字，而使用者感受到的是牆上時鐘的頻率。
        """
        if len(self.tick_ends) < 2:
            return 0.0
        span = self.tick_ends[-1] - self.tick_ends[0]
        return (len(self.tick_ends) - 1) / span if span > 0 else 0.0

    @property
    def overrun_ratio(self) -> float:
        return self.overruns / self.ticks if self.ticks else 0.0

    def busiest(self) -> Optional[Stage]:
        """扣掉刻意等待後，平均最花時間的那一段。"""
        candidates = [s for n, s in self.stages.items()
                      if n not in BLOCKING_STAGES and s.samples]
        return max(candidates, key=lambda s: s.avg_ms) if candidates else None

    def advice(self) -> str:
        """只有在真的跟不上時才給建議——沒事就別製造雜訊。"""
        if self.overrun_ratio < 0.2 or self.ticks < 20:
            return ""
        worst = self.busiest()
        if worst is None:
            return ""
        hint = STAGE_HINTS.get(worst.name, "")
        return (f"有 {self.overrun_ratio:.0%} 的 tick 跑不完 {self.budget * 1000:.0f}ms 預算，"
                f"最花時間的是 {worst.name}（平均 {worst.avg_ms:.0f}ms）。"
                + (f"{hint}。" if hint else "")
                + f"也可以直接把 loop.fps 從 {self.target_fps:g} 調低——"
                  "跑得穩比跑得快重要")

    def snapshot(self) -> dict:
        return {
            "target_fps": self.target_fps,
            "actual_fps": round(self.fps, 2),
            "ticks": self.ticks,
            "overruns": self.overruns,
            "overrun_ratio": round(self.overrun_ratio, 4),
            "stages_ms": {
                name: {"avg": round(s.avg_ms, 1),
                       "p95": round(s.pct_ms(), 1),
                       "max": round(s.max_ms, 1)}
                for name, s in sorted(self.stages.items())
            },
        }

    def summary(self) -> str:
        parts = [f"{self.fps:.1f}/{self.target_fps:g} FPS"]
        for name, s in sorted(self.stages.items(), key=lambda kv: -kv[1].avg_ms):
            if s.samples:
                parts.append(f"{name} {s.avg_ms:.0f}ms")
        if self.overruns:
            parts.append(f"超支 {self.overrun_ratio:.0%}")
        return "｜".join(parts)


@dataclass
class Sample:
    """報告曲線上的一個點。欄位刻意都是純量，序列化不用特別處理。"""
    t: float                            # 從開跑算起的秒數
    hp: Optional[float] = None
    mp: Optional[float] = None
    exp: Optional[float] = None
    mobs: int = 0
    fps: float = 0.0
    cpu: Optional[float] = None         # 遊戲行程 CPU %
    mem_mb: Optional[float] = None      # 遊戲行程 RSS

    def as_dict(self) -> dict:
        return {"t": round(self.t, 1), "hp": self.hp, "mp": self.mp, "exp": self.exp,
                "mobs": self.mobs, "fps": round(self.fps, 2),
                "cpu": self.cpu, "mem_mb": self.mem_mb}


class Series:
    """定時取樣的歷史，給收工報告畫曲線用。

    上限是筆數不是時間：掛 12 小時 @10s 是 4320 筆，記憶體幾百 KB，
    但沒有上限的話一直掛就是一直漲。滿了丟最舊的。
    """

    def __init__(self, interval: float = 10.0, cap: int = 8640):
        self.interval = max(interval, 0.0)
        self.samples: Deque[Sample] = deque(maxlen=cap)
        self._last_at: Optional[float] = None
        self.dropped = 0

    def maybe_add(self, now: float, started: float, **fields) -> bool:
        if self.interval <= 0:
            return False
        if self._last_at is not None and now - self._last_at < self.interval:
            return False
        self._last_at = now
        if len(self.samples) == self.samples.maxlen:
            self.dropped += 1
        self.samples.append(Sample(t=now - started, **fields))
        return True

    def rows(self) -> List[dict]:
        return [s.as_dict() for s in self.samples]

    def column(self, name: str) -> List[Optional[float]]:
        return [getattr(s, name) for s in self.samples]
