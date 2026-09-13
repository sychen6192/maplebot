"""把擷取（與昂貴的偵測）搬到背景執行緒。

對應商業版的 `thread_debug_config.json`——它把 `capture`、`video_capture`、
`minimap_detection`、`status_detection`、`status_check` 拆成各自獨立的開關，
也就是那些工作本來就跑在不同的執行緒上。

**為什麼要這一層**：這個專案的主迴圈是
`capture -> perceive -> decide -> execute` 一路同步做完，所以整輪的頻率被
最慢的那一段壓住。實測 2560x1440 的畫面 `perceive` 平均 81ms
（見 vision/nametag.py 的註解），`loop.fps: 8` 的 125ms 預算幾乎被它吃光，
而擷取還要再排隊等它做完——結果是拿到的畫面總是「上一輪辨識完才去抓的」，
反應延遲比帳面上的 fps 更差。

**兩個保證，缺一不可**：

1. **只給最新的一幀。** 背景執行緒持續擷取、只保留最後一張；主迴圈永遠拿到
   當下最新的畫面，不是排隊等來的舊畫面。
2. **太舊的幀不給。** 這是安全需求不是效能需求：擷取執行緒卡住（遊戲最小化、
   PrintWindow 失效）時，如果還把幾秒前的畫面交給決策層，bot 會照著一張
   過期的畫面繼續打——HP 早就掉到危險線了它還以為是滿的。所以超過
   `max_age` 就回 None，讓主迴圈跳過這個 tick。

**decide() 完全不受影響**：這一層只換「畫面從哪來」，決策仍然是吃 GameState
的純函式（ADR 0002）。所有測試也都跑單執行緒路徑——`threads` 預設全關。
"""
import threading
import time
from typing import Callable, Optional, Tuple

import numpy as np


class FrameSource:
    """背景執行緒持續擷取，只保留最新一幀。

    grab 可以是任何 `() -> ndarray`（`WindowCapture.grab` 或測試用的假物件）。
    """

    def __init__(self, grab: Callable[[], np.ndarray], interval: float = 0.0,
                 max_age: float = 0.5, logger=None,
                 clock: Callable[[], float] = time.monotonic):
        self._grab = grab
        self._interval = max(interval, 0.0)
        self.max_age = max_age
        self._log = logger
        self._clock = clock
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._stamp = 0.0
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self.errors = 0
        self.last_error: Optional[BaseException] = None
        self.stale = 0          # 因為太舊而被拒絕的次數（給 runner 回報）

    # ---- 生命週期 ----

    def start(self, timeout: float = 2.0) -> bool:
        """啟動並等第一幀。回傳是否在 timeout 內拿到畫面。"""
        if self._thread is not None:
            return self._ready.is_set()
        self._thread = threading.Thread(target=self._run, name="capture",
                                        daemon=True)
        self._thread.start()
        return self._ready.wait(timeout)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    # ---- 擷取迴圈 ----

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self._grab()
            except Exception as e:      # noqa: BLE001 - 擷取失敗不該讓執行緒死掉
                # 擷取會因為各種暫時性原因失敗（視窗剛好在切換、PrintWindow
                # 偶發回 None）。讓執行緒死掉的話主迴圈只會看到「畫面停住」，
                # 查不出原因——記下來繼續試，太舊的幀自然會被 get() 擋掉。
                self.errors += 1
                self.last_error = e
                if self._log is not None and self.errors == 1:
                    self._log.warning("擷取執行緒發生錯誤（會繼續重試）：%s", e)
                self._stop.wait(0.05)
                continue
            now = self._clock()
            with self._lock:
                self._frame, self._stamp = frame, now
            self._ready.set()
            if self._interval:
                self._stop.wait(self._interval)

    # ---- 取用 ----

    def get(self) -> Tuple[Optional[np.ndarray], float]:
        """回傳 `(最新的一幀, 幀齡秒數)`；沒有或太舊時第一項是 None。

        太舊就不給，是因為「照著過期畫面決策」比「這個 tick 不動」危險得多。
        """
        with self._lock:
            frame, stamp = self._frame, self._stamp
        if frame is None:
            return None, float("inf")
        age = self._clock() - stamp
        if self.max_age is not None and age > self.max_age:
            self.stale += 1
            return None, age
        return frame, age


class Latest:
    """一格的「最新結果」信箱：寫入會蓋掉還沒被讀走的舊值。

    佇列會累積延遲——偵測比主迴圈慢的時候，佇列裡排的全是過期結果，
    讀到的永遠是幾輪前的畫面算出來的。這裡刻意只留最後一個。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._value = None
        self._stamp = 0.0
        self.updates = 0

    def put(self, value, stamp: float) -> None:
        with self._lock:
            self._value, self._stamp = value, stamp
            self.updates += 1

    def get(self):
        with self._lock:
            return self._value, self._stamp


class DetectorWorker:
    """把一個昂貴的偵測搬到背景執行緒，主迴圈讀最後一次的結果。

    這是把 `vision.mob_interval`（同一條執行緒上降頻）升級成真正的並行：
    降頻只是讓主迴圈少做幾次，做的那幾次還是要等；搬到背景之後主迴圈完全
    不等，代價是拿到的框最舊會是一次偵測的時間之前的。

    **所以只適合會移動但不致命的東西**（怪的位置）。HP、角色位置這些
    「錯一拍就出事」的辨識留在主迴圈每幀做。
    """

    def __init__(self, work: Callable[[np.ndarray], object],
                 source: FrameSource, interval: float = 0.0, logger=None,
                 clock: Callable[[], float] = time.monotonic):
        self._work = work
        self._source = source
        self._interval = max(interval, 0.0)
        self._log = logger
        self._clock = clock
        self.result = Latest()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.errors = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="detect",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            frame, _ = self._source.get()
            if frame is None:
                self._stop.wait(0.02)
                continue
            try:
                out = self._work(frame)
            except Exception as e:      # noqa: BLE001
                self.errors += 1
                if self._log is not None and self.errors == 1:
                    self._log.warning("偵測執行緒發生錯誤（會繼續重試）：%s", e)
                self._stop.wait(0.05)
                continue
            self.result.put(out, self._clock())
            if self._interval:
                self._stop.wait(self._interval)
