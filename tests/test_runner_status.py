"""Safety 的暫停／停止原因。

GUI 的執行卡只讀這幾格來回答「為什麼停了」。熱鍵、watchdog、督導層、
控制台四個來源都走同一組 pause/resume/request_stop，原因不能互相蓋掉，
也不能在恢復後殘留。
"""
import logging

from maplebot.safety import _VK, Safety


def _safety():
    return Safety("f12", "f9", logging.getLogger("maplebot.test.safety"))


def test_pause_records_why_and_who():
    s = _safety()
    assert s.pause("連續 10 分鐘沒有經驗進帳") is True
    assert s.paused is True
    assert s.pause_reason == "連續 10 分鐘沒有經驗進帳"
    assert s.paused_by_user is False


def test_a_second_pause_keeps_the_first_reason():
    """watchdog 剛暫停、使用者的點擊緊接著落下：要留下的是 watchdog 的理由，
    那才是回來的人需要看的。"""
    s = _safety()
    s.pause("黑屏持續 5 秒（斷線、換頻道或讀圖）")
    assert s.pause("你在控制台按了暫停", by_user=True) is False
    assert "黑屏" in s.pause_reason
    assert s.paused_by_user is False


def test_resume_clears_the_reason():
    s = _safety()
    s.pause("你在控制台按了暫停", by_user=True)
    assert s.resume() is True
    assert (s.paused, s.pause_reason, s.paused_by_user) == (False, "", False)
    assert s.resume() is False                 # 本來就沒暫停


def test_the_first_stop_reason_wins():
    """停機後使用者又按停止，報告裡的結束原因仍該是停機的那一句。"""
    s = _safety()
    s.request_stop("PANIC：HP 見底")
    s.request_stop("你在控制台按了停止")
    assert s.stop is True
    assert s.stop_reason == "PANIC：HP 見底"


def _press(s, *keys):
    """模擬 GetAsyncKeyState：列出的鍵目前按住，其他都放開。"""
    down = {_VK[k] for k in keys}
    s._gaks = lambda vk: 0x8000 if vk in down else 0


def test_hotkey_reasons_name_the_key():
    s = _safety()
    _press(s, "f9")
    s.poll()
    assert (s.paused, s.pause_reason, s.paused_by_user) == (True, "你按了 F9", True)

    _press(s)                                  # 放開
    s.poll()
    _press(s, "f9")                            # 再按一次 = 繼續
    s.poll()
    assert (s.paused, s.pause_reason) == (False, "")

    _press(s, "f12")
    s.poll()
    assert s.stop is True
    assert s.stop_reason == "你按了 F12"


def test_hotkey_resumes_a_watchdog_pause():
    s = _safety()
    s.pause("連續 15 秒找不到角色的小地圖黃點（換圖、被傳走或斷線）")
    _press(s, "f9")
    s.poll()
    assert s.paused is False
    assert s.pause_reason == ""


def test_holding_the_key_does_not_toggle_again():
    s = _safety()
    _press(s, "f9")
    s.poll()
    s.poll()                                   # 還按著：不是新的一次
    assert s.paused is True
