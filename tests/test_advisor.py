"""VLM 督導層：誤判要擋得住，thinking 模型的回答要讀得到。

這兩件事都是實機踩出來的：
  * qwen3 系列會先在 reasoning 欄位推理，max_tokens 給太小時 content 是空的
    ——督導層等於整場沒作用，卻只在 log 留下一行逾時
  * VLM 會把角色腳下常駐的勳章名牌看成「彈出視窗」，一次誤判就把整晚掛機
    暫停在那裡
"""
import json
import logging

import pytest

from maplebot.brain.advisor import Advisor
from maplebot.config import AdvisorCfg


def _advisor(confirm=2):
    fired = []
    cfg = AdvisorCfg(enabled=True, confirm=confirm)
    return Advisor(cfg, fired.append, logging.getLogger("test.advisor")), fired


def test_single_abnormal_does_not_pause():
    """一次誤判不能停掉整晚。"""
    adv, fired = _advisor(confirm=2)
    assert adv.consider({"status": "abnormal", "note": "看到勳章名牌"}) is False
    assert fired == []


def test_two_in_a_row_pauses():
    adv, fired = _advisor(confirm=2)
    adv.consider({"status": "abnormal", "note": "對話框"})
    assert adv.consider({"status": "abnormal", "note": "對話框"}) is True
    assert len(fired) == 1
    assert "對話框" in fired[0]


def test_an_ok_in_between_resets_the_streak():
    """誤判之後畫面正常了，計數要歸零——否則兩次相隔十分鐘的誤判也會累積成暫停。"""
    adv, fired = _advisor(confirm=2)
    adv.consider({"status": "abnormal", "note": "一次誤判"})
    adv.consider({"status": "ok", "note": "正常"})
    assert adv.consider({"status": "abnormal", "note": "另一次誤判"}) is False
    assert fired == []


def test_streak_resets_after_firing():
    """通報過後重新計數，不會每一輪都重複通報。"""
    adv, fired = _advisor(confirm=2)
    adv.consider({"status": "abnormal", "note": "x"})
    adv.consider({"status": "abnormal", "note": "x"})
    assert adv.consider({"status": "abnormal", "note": "x"}) is False
    assert len(fired) == 1


def test_confirm_one_fires_immediately():
    adv, fired = _advisor(confirm=1)
    assert adv.consider({"status": "stuck", "note": "卡住"}) is True


def test_missing_status_is_treated_as_ok():
    """回傳格式跑掉時當作正常——督導層自己壞掉不該把 bot 停下來。"""
    adv, fired = _advisor(confirm=1)
    assert adv.consider({}) is False
    assert fired == []


class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ask_with(monkeypatch, message):
    import urllib.request

    import numpy as np
    adv, _ = _advisor()
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: _Resp({"choices": [{"message": message}]}))
    frame = np.zeros((40, 60, 3), dtype=np.uint8)
    return adv._ask(frame)


def test_reads_the_answer_from_content(monkeypatch):
    got = _ask_with(monkeypatch, {"content": '{"status": "ok", "note": "正常"}'})
    assert got == {"status": "ok", "note": "正常"}


def test_falls_back_to_reasoning_when_content_is_empty(monkeypatch):
    """thinking 模型推理被截斷時 content 是空的，答案還在 reasoning 裡。"""
    got = _ask_with(monkeypatch, {
        "content": "",
        "reasoning": '我看了畫面…結論 {"status": "abnormal", "note": "對話框"}'})
    assert got == {"status": "abnormal", "note": "對話框"}


def test_reasoning_content_field_also_works(monkeypatch):
    got = _ask_with(monkeypatch, {
        "content": None,
        "reasoning_content": '{"status": "stuck", "note": "沒動"}'})
    assert got == {"status": "stuck", "note": "沒動"}


def test_unparsable_reply_is_none(monkeypatch):
    assert _ask_with(monkeypatch, {"content": "我不知道"}) is None
