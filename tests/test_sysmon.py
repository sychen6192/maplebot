"""系統監看：門檻判定、警報冷卻、遊戲行程消失偵測。

不去真的量 CPU——那不可重現。門檻邏輯是純函式，餵假的 snapshot 就好；
取樣本身（psutil 那一段）在 CI 上沒有遊戲行程可綁，能驗的是「沒綁到的
時候不會亂報遊戲死掉」。
"""
from maplebot.config import MonitorCfg
from maplebot.sysmon import ALERT_COOLDOWN, SysSnapshot, SystemMonitor, evaluate


def snap(**kw):
    return SysSnapshot(at=kw.pop("at", 100.0), **kw)


# ---- 門檻判定 ----

def test_quiet_when_everything_is_fine():
    assert evaluate(snap(sys_cpu=40, sys_mem=50, game_mem_mb=1200), 92, 90, 4096) == []


def test_flags_high_system_cpu():
    kinds = [k for k, _ in evaluate(snap(sys_cpu=95, sys_mem=50), 92, 90, 4096)]
    assert kinds == ["sys_cpu"]


def test_cpu_message_points_at_the_real_cause():
    """CPU 滿載時「反應變鈍」不是辨識參數的錯——訊息要先擋下那個誤判。"""
    _, msg = evaluate(snap(sys_cpu=99), 92, 90, 0)[0]
    assert "掉幀" in msg


def test_flags_bloated_game_process():
    kinds = [k for k, _ in evaluate(snap(game_mem_mb=5000), 92, 90, 4096)]
    assert kinds == ["game_mem"]


def test_game_mem_threshold_zero_disables_that_check():
    assert evaluate(snap(game_mem_mb=99999), 92, 90, 0) == []


def test_missing_readings_are_not_treated_as_zero():
    """psutil 讀不到時欄位是 None，不能當成 0 也不能當成爆表。"""
    assert evaluate(snap(), 92, 90, 4096) == []


# ---- 冷卻 ----

def _monitor(**kw):
    cfg = MonitorCfg(**kw)
    m = SystemMonitor(cfg)
    m.available = True          # 這幾個測試不碰 psutil，只驗冷卻邏輯
    return m


def test_same_alert_does_not_repeat_within_cooldown():
    m = _monitor(cpu_threshold=90)
    assert len(m.alerts(snap(at=0.0, sys_cpu=99))) == 1
    assert m.alerts(snap(at=30.0, sys_cpu=99)) == []
    assert len(m.alerts(snap(at=ALERT_COOLDOWN + 1, sys_cpu=99))) == 1


def test_different_alert_types_have_separate_cooldowns():
    m = _monitor(cpu_threshold=90, mem_threshold=80)
    m.alerts(snap(at=0.0, sys_cpu=99))
    kinds = [k for k, _ in m.alerts(snap(at=1.0, sys_cpu=99, sys_mem=95))]
    assert kinds == ["sys_mem"]      # CPU 還在冷卻，記憶體是新的


# ---- 節流與遊戲行程 ----

def test_poll_respects_the_interval():
    m = _monitor(interval=5.0)
    assert m.poll(0.0) is not None
    assert m.poll(2.0) is None
    assert m.poll(5.1) is not None


def test_no_false_game_lost_when_never_attached():
    """離線用截圖跑時沒有遊戲行程可綁。這種情況絕不能報「遊戲當了」
    然後把 dry-run 停掉。"""
    m = _monitor()
    m.poll(0.0)
    assert m.game_lost is False


def test_game_lost_latches_once_the_process_is_gone():
    class Dead:
        pid = 4242

        def oneshot(self):
            raise RuntimeError("boom")   # 模擬 NoSuchProcess

    m = _monitor()
    m._proc = Dead()
    m.poll(0.0)
    assert m.game_lost is True
    # 閂住：之後即使取樣失敗也不會又變回 False
    m.poll(100.0)
    assert m.game_lost is True


def test_disabled_monitor_never_polls():
    m = SystemMonitor(MonitorCfg(enabled=False))
    assert m.available is False
    assert m.poll(0.0) is None
    assert "未啟用" in m.summary()


def test_as_dict_is_json_friendly():
    import json
    m = _monitor()
    m.poll(0.0)
    json.dumps(m.as_dict())
