"""把擷取（與昂貴的偵測）搬到背景執行緒。

對應商業版的 `thread_debug_config.json`——它把 `capture`、`video_capture`、
`minimap_detection`、`status_detection`、`status_check` 拆成各自獨立的開關，
也就是那些工作本來就跑在不同的執行緒上。

**為什麼要這一層**：這個專案的主迴圈是
`capture -> perceive -> decide -> execute` 一路同步做完，所以整輪的頻率被最慢
的那一段壓住。把 2560x1440 的畫面餵進去量（fixture 放大，`loop.fps: 8` 的
預算是 125ms）：

    perceive 全部          392 ms
      detector.detect      339 ms   <- 86%，就是它
      角色定位              30 ms
      小地圖                 3 ms

也就是**一段 detect 就把整個 tick 的預算吃掉三倍**，而擷取還要排在它後面。

兩個東西各自解一半：

  * `FrameSource` 把**擷取**搬到背景，主迴圈永遠拿當下最新的一幀，不是
    「上一輪辨識完才去抓的」那張。
  * `DetectorWorker` 把**那 339ms** 搬到背景，主迴圈剩約 53ms。

**三個保證**：

1. **只留最新的。** 幀與工作都只留最後一份。用佇列的話，偵測比主迴圈慢時
   排在裡面的全是過期畫面，算出來的框永遠落後好幾輪——那比「這一輪沒有新框」
   更糟，因為它看起來是新的。
2. **太舊的不給。** 這是安全需求不是效能需求：擷取執行緒卡住（遊戲最小化、
   PrintWindow 失效）時，把幾秒前的畫面交給決策層，bot 會照著過期畫面繼續打
   ——HP 早就掉到危險線了它還以為是滿的。超過 `max_age` 就回 None，跳過這個 tick。
3. **一份工作裡的東西都來自同一幀。** 怪的框要跟同一幀的角色位置綁在一起，
   否則挖掉「自己」會挖到空地、角色本人被當成怪打（見 DetectorWorker 的說明）。

**decide() 完全不受影響**：這一層只換「畫面與框從哪來」，決策仍然是吃
GameState 的純函式（ADR 0002）。測試預設跑單執行緒路徑——`threads` 預設全關。
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
    """吃「工作」、吐「最新結果」的背景執行緒。

    **刻意不是「自己去抓畫面來算」。** 怪的框必須跟**同一幀**的角色位置綁在
    一起：搜尋框要以角色為中心（`_search_roi`）、描邊偵測要照角色實際位置把
    自己挖掉（`drop_at`）。worker 自己去抓畫面的話，框與角色位置就來自不同幀
    ——挖掉「自己」會挖到空地，而角色本人被當成一隻怪打整晚，那正是
    `vision/player_bar.py` 與 `vision/nametag.py` 整個存在的理由。

    所以由主迴圈把「這一幀 ＋ 這一幀量到的角色位置」整包送進來。順帶的好處是
    定位器（nametag 的區域搜尋、軌跡追蹤）只有主迴圈碰得到，沒有共用狀態要鎖。

    為什麼值得搬：2560x1440 實測 `perceive` 共 392ms，其中 `detector.detect`
    就佔 339ms（86%），角色定位只有 30ms。把那 339ms 搬走，主迴圈剩約 53ms。

    代價是拿到的框會舊一點（最多一次偵測的時間）。所以**只適合會動但錯一拍
    不致命的東西**——HP、角色位置這些留在主迴圈每幀做。
    """

    def __init__(self, work: Callable[[object], object], logger=None,
                 clock: Callable[[], float] = time.monotonic):
        self._work = work
        self._log = logger
        self._clock = clock
        self._inbox = Latest()          # 只留最新的工作：舊的畫面算了也沒用
        self._wake = threading.Event()
        self.result = Latest()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.errors = 0
        self.dropped = 0                # 還沒被處理就被新工作蓋掉的次數
        self.done = 0

    # ---- 生命週期 ----

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="detect",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    # ---- 主迴圈這一側 ----

    def submit(self, job) -> None:
        """送一份工作進去。還沒被拿走的舊工作會被蓋掉。

        排隊的話，偵測比主迴圈慢時佇列裡全是過期畫面，算出來的框永遠落後
        好幾輪——那比「這一輪沒有新框」更糟，因為它看起來是新的。
        """
        pending, _ = self._inbox.get()
        if pending is not None:
            self.dropped += 1
        self._inbox.put(job, self._clock())
        self._wake.set()

    def latest(self):
        """回傳 `(最近一次算完的結果, 那份工作的時間戳)`。沒有結果時第一項是 None。"""
        return self.result.get()

    # ---- 工作執行緒 ----

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.05)
            self._wake.clear()
            if self._stop.is_set():
                break
            job, stamp = self._inbox.get()
            if job is None:
                continue
            self._inbox.put(None, stamp)        # 取走，避免重算同一份
            try:
                out = self._work(job)
            except Exception as e:      # noqa: BLE001
                # 偵測失敗不該讓執行緒死掉——死了主迴圈只會看到「怪永遠是 0 隻」，
                # 而那看起來跟「這張地圖沒怪」一模一樣
                self.errors += 1
                if self._log is not None and self.errors == 1:
                    self._log.warning("偵測執行緒發生錯誤（會繼續重試）：%s", e)
                continue
            self.done += 1
            # 時間戳用**工作送進來的時間**，不是算完的時間：下游要知道的是
            # 「這批框是哪一幀的」，不是「什麼時候算完的」
            self.result.put(out, stamp)
