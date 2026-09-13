"""背景擷取與偵測執行緒。

測試用的是**可控的時鐘與可控的來源**，不是 sleep：靠 sleep 對時的執行緒測試
在 CI 上遲早會偶發失敗，而偶發失敗的測試等於沒有測試。
"""
import threading
import time

import numpy as np
import pytest

from maplebot.pipeline import DetectorWorker, FrameSource, Latest


class _Clock:
    """手動推進的時鐘，讓「幀齡」這種跟時間有關的行為變成可窮舉的。"""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class _Source:
    """可控的擷取來源：每次 grab 回一張帶編號的圖，可以被擋住或叫它出錯。"""

    def __init__(self, fail_times: int = 0):
        self.n = 0
        self.last = None
        self.fail_times = fail_times
        self.gate = threading.Event()
        self.gate.set()
        self.calls = 0

    def __call__(self):
        self.gate.wait(2.0)
        self.calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("擷取失敗")
        self.n += 1
        # 每次回一個**新的**陣列物件，這樣測試可以用 is 判斷「拿到的是不是
        # 最後那一張」——用像素值比會在 uint8 溢位後全部長一樣
        self.last = np.full((4, 4, 3), self.n % 251, dtype=np.uint8)
        return self.last


def _wait_for(pred, timeout=2.0):
    """等一個條件成立——等的是**狀態**不是固定秒數。"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return False


def test_it_hands_over_a_frame():
    src = _Source()
    with FrameSource(src) as fs:
        frame, age = fs.get()
    assert frame is not None
    assert age >= 0


def test_a_stale_frame_is_refused():
    """擷取卡住時不能把過期畫面交給決策層。

    這是安全需求不是效能需求：HP 早就掉到危險線了，bot 還照著三秒前的畫面
    以為是滿的——那比停一個 tick 危險得多。
    """
    clock = _Clock()
    src = _Source()
    fs = FrameSource(src, max_age=0.5, clock=clock)
    assert fs.start()
    try:
        assert fs.get()[0] is not None
        src.gate.clear()            # 擷取卡住，不再有新的幀
        clock.t += 2.0              # 時間往前走
        frame, age = fs.get()
        assert frame is None
        assert age == pytest.approx(2.0, abs=0.01)
        assert fs.stale == 1
    finally:
        src.gate.set()
        fs.stop()


def test_a_fresh_frame_is_accepted_again_after_a_stall():
    clock = _Clock()
    src = _Source()
    fs = FrameSource(src, max_age=0.5, clock=clock)
    assert fs.start()
    try:
        clock.t += 2.0
        assert fs.get()[0] is None          # 太舊
        n = src.calls
        assert _wait_for(lambda: src.calls > n)
        assert fs.get()[0] is not None      # 新的一幀又可以用了
    finally:
        fs.stop()


def test_only_the_newest_frame_survives():
    """沒有佇列：主迴圈拿到的永遠是最後擷取到的那一張。

    用佇列的話，偵測比擷取慢時排在裡面的全是過期畫面，讀到的永遠是好幾輪前
    的世界。這裡刻意只留最後一張，所以 get() 拿到的必須就是來源剛產出的那個
    物件本身。
    """
    src = _Source()
    fs = FrameSource(src)
    assert fs.start()
    try:
        assert _wait_for(lambda: src.n >= 20)
        src.gate.clear()                   # 停住，讓「最後一張」有明確定義
        assert _wait_for(lambda: fs.get()[0] is src.last)
    finally:
        src.gate.set()
        fs.stop()


def test_a_capture_error_does_not_kill_the_thread():
    """擷取會因為暫時性原因失敗。執行緒死掉的話主迴圈只看到畫面停住，查不出原因。"""
    src = _Source(fail_times=3)
    fs = FrameSource(src)
    try:
        assert fs.start(timeout=3.0)
        assert fs.get()[0] is not None
        assert fs.errors == 3
    finally:
        fs.stop()


def test_start_reports_failure_when_no_frame_arrives():
    """拿不到第一幀要講出來，讓 runner 能退回主迴圈同步擷取。"""
    src = _Source()
    src.gate.clear()
    fs = FrameSource(src)
    try:
        assert fs.start(timeout=0.2) is False
    finally:
        src.gate.set()
        fs.stop()


def test_stop_is_idempotent():
    fs = FrameSource(_Source())
    fs.start()
    fs.stop()
    fs.stop()


# ---- Latest ----

def test_latest_keeps_only_the_last_value():
    box = Latest()
    box.put("a", 1.0)
    box.put("b", 2.0)
    assert box.get() == ("b", 2.0)
    assert box.updates == 2


def test_latest_starts_empty():
    assert Latest().get() == (None, 0.0)


# ---- DetectorWorker ----
# 它吃「工作」而不是自己去抓畫面，因為怪的框必須跟同一幀的角色位置綁在一起。

def test_the_worker_publishes_results():
    w = DetectorWorker(lambda job: job * 2)
    w.start()
    try:
        w.submit(21)
        assert _wait_for(lambda: w.latest()[0] == 42)
    finally:
        w.stop()


def test_the_result_carries_the_jobs_timestamp_not_the_finish_time():
    """下游要知道的是「這批框是哪一幀的」，不是「什麼時候算完的」。"""
    clock = _Clock()
    w = DetectorWorker(lambda job: job, clock=clock)
    w.start()
    try:
        clock.t = 500.0
        w.submit("a")
        assert _wait_for(lambda: w.latest()[0] == "a")
        clock.t = 900.0                       # 算完之後時間又走了很久
        assert w.latest()[1] == 500.0         # 仍然是工作送進來的時間
    finally:
        w.stop()


def test_a_new_job_replaces_one_that_has_not_started():
    """排隊會累積延遲：算完的永遠是好幾輪前的畫面，而它看起來是新的。"""
    gate = threading.Event()
    w = DetectorWorker(lambda job: (gate.wait(2.0), job)[1])
    w.start()
    try:
        w.submit("first")
        assert _wait_for(lambda: w.dropped == 0)
        for job in ("second", "third", "fourth"):
            w.submit(job)
        gate.set()
        assert _wait_for(lambda: w.latest()[0] == "fourth")
        assert w.dropped >= 2                 # 中間那幾份被蓋掉了
    finally:
        gate.set()
        w.stop()


def test_a_worker_error_does_not_kill_the_thread():
    """偵測炸掉不該讓執行緒死掉——死了主迴圈只會看到「怪永遠 0 隻」，
    而那看起來跟「這張地圖沒怪」一模一樣。"""
    state = {"n": 0}

    def flaky(job):
        state["n"] += 1
        if state["n"] <= 2:
            raise RuntimeError("偵測炸了")
        return "ok"

    w = DetectorWorker(flaky)
    w.start()
    try:
        for _ in range(3):
            w.submit("job")
            time.sleep(0.02)
        assert _wait_for(lambda: w.latest()[0] == "ok")
        assert w.errors == 2
    finally:
        w.stop()


def test_no_work_happens_without_a_job():
    calls = []
    w = DetectorWorker(lambda job: calls.append(job))
    w.start()
    try:
        time.sleep(0.12)
        assert calls == []
        assert w.latest()[0] is None
    finally:
        w.stop()


def test_stopping_an_idle_worker_returns_promptly():
    """停止不能等到下一次 poll——收工時多等半秒沒必要。"""
    w = DetectorWorker(lambda job: job)
    w.start()
    t = time.monotonic()
    w.stop()
    assert time.monotonic() - t < 0.5
