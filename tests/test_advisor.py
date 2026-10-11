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


def _sent_headers(monkeypatch, api_key):
    import urllib.request

    import numpy as np
    sent = []

    def fake_urlopen(req, *a, **k):
        sent.append(dict(req.header_items()))
        return _Resp({"choices": [{"message": {"content": '{"status": "ok"}'}}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    cfg = AdvisorCfg(enabled=True, api_key=api_key)
    Advisor(cfg, lambda note: None, logging.getLogger("test.advisor")) \
        ._ask(np.zeros((40, 60, 3), dtype=np.uint8))
    return sent[0]


def test_api_key_is_sent_as_bearer(monkeypatch):
    """遠端模型伺服器要 key：沒帶的話每輪都是 401，督導層整場沒作用。"""
    headers = _sent_headers(monkeypatch, "secret-key")
    assert headers.get("Authorization") == "Bearer secret-key"


def test_no_api_key_sends_no_authorization(monkeypatch):
    """本機 Ollama 不用 key——不能送一個空的 Bearer 過去。"""
    headers = _sent_headers(monkeypatch, "")
    assert "Authorization" not in headers


# ---- 督導點名：一直打不死時問「正在打的是不是怪」----

def _capture_requests(monkeypatch, reply='{"kind": "player", "note": "腳下有名牌"}'):
    import urllib.request
    sent = []

    def fake_urlopen(req, *a, **k):
        sent.append(json.loads(req.data))
        return _Resp({"choices": [{"message": {"content": reply}}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return sent


def _decode_sent_image(payload):
    import base64

    import cv2
    import numpy as np
    url = payload["messages"][0]["content"][1]["image_url"]["url"]
    jpg = base64.b64decode(url.split(",", 1)[1])
    return cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)


def test_target_check_sends_a_zoomed_crop_with_the_box_marked(monkeypatch):
    """送的是框附近放大的局部：整張畫面送過去時框太小，同一個框問 10 次只對一半。"""
    import numpy as np
    sent = _capture_requests(monkeypatch)
    adv, _ = _advisor()
    frame = np.full((400, 600, 3), 90, np.uint8)
    frame[150:180, 300:340] = (0, 200, 0)               # 框裡的東西
    original = frame.copy()

    verdict = adv.check_target(frame, (300, 150, 40, 30))

    assert verdict == {"kind": "player", "note": "腳下有名牌"}
    assert "洋紅色方框" in sent[0]["messages"][0]["content"][0]["text"]
    assert sent[0]["temperature"] == 0.2
    img = _decode_sent_image(sent[0])
    assert max(img.shape[:2]) == 512
    magenta = (img[:, :, 2] > 200) & (img[:, :, 0] > 200) & (img[:, :, 1] < 80)
    green = (img[:, :, 1] > 150) & (img[:, :, 2] < 80)
    ys, xs = np.nonzero(magenta)
    gy, gx = np.nonzero(green)
    # 洋紅框要圍住框裡的東西（畫錯地方，模型回答的就是另一個東西）
    assert xs.min() <= gx.min() and gx.max() <= xs.max()
    assert ys.min() <= gy.min() and gy.max() <= ys.max()
    assert (frame == original).all()                    # 不能畫在主迴圈的原圖上


def test_target_crop_near_the_screen_edge_still_marks_the_box():
    """怪貼在畫面邊緣時裁切會被截掉一邊，框還是要在圖裡。"""
    import numpy as np

    from maplebot.brain.advisor import crop_for_target
    frame = np.full((300, 400, 3), 90, np.uint8)
    img = crop_for_target(frame, (0, 270, 30, 30))
    magenta = (img[:, :, 2] > 200) & (img[:, :, 0] > 200) & (img[:, :, 1] < 80)
    assert magenta.sum() > 100


def test_target_check_async_hands_the_verdict_back(monkeypatch):
    import threading

    import numpy as np
    _capture_requests(monkeypatch, '{"kind": "pet", "note": "寵物"}')
    adv, _ = _advisor()
    got, done = [], threading.Event()

    def on_result(verdict):
        got.append(verdict)
        done.set()

    assert adv.check_target_async(np.zeros((60, 80, 3), np.uint8), (5, 5, 10, 10), on_result)
    assert done.wait(5)
    assert got == [{"kind": "pet", "note": "寵物"}]


def test_only_one_target_check_in_flight(monkeypatch):
    """模型伺服器常只有一個 slot：排隊只會讓答案更舊，第二個直接不送。"""
    import threading
    import urllib.request

    import numpy as np
    release = threading.Event()

    def slow_urlopen(req, *a, **k):
        release.wait(5)
        return _Resp({"choices": [{"message": {"content": '{"kind": "monster"}'}}]})

    monkeypatch.setattr(urllib.request, "urlopen", slow_urlopen)
    adv, _ = _advisor()
    frame = np.zeros((60, 80, 3), np.uint8)
    done = threading.Event()
    assert adv.check_target_async(frame, (5, 5, 10, 10), lambda v: done.set())
    assert adv.check_target_async(frame, (5, 5, 10, 10), lambda v: None) is False
    release.set()
    assert done.wait(5)
    # 上一個回來之後就可以再問
    assert adv.check_target_async(frame, (5, 5, 10, 10), lambda v: None)


def test_a_failed_target_check_reports_none(monkeypatch):
    """督導層自己壞掉不能讓 bot 停下來：回 None，12 秒保護照常兜底。"""
    import threading
    import urllib.request

    import numpy as np

    def broken(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", broken)
    adv, _ = _advisor()
    got, done = [], threading.Event()
    adv.check_target_async(np.zeros((60, 80, 3), np.uint8), (5, 5, 10, 10),
                           lambda v: (got.append(v), done.set()))
    assert done.wait(5)
    assert got == [None]


def test_target_check_is_off_when_the_advisor_or_the_feature_is_off():
    import numpy as np
    frame = np.zeros((60, 80, 3), np.uint8)
    off = Advisor(AdvisorCfg(enabled=False), lambda n: None, logging.getLogger("t"))
    assert off.check_target_async(frame, (5, 5, 10, 10), lambda v: None) is False
    no_target = Advisor(AdvisorCfg(enabled=True, target_check=False), lambda n: None,
                        logging.getLogger("t"))
    assert no_target.check_target_async(frame, (5, 5, 10, 10), lambda v: None) is False


# ---- 離線標註：整張畫面請模型自己框（grounding）----

def test_grounding_reads_the_native_format_in_a_code_fence():
    """關掉思考後模型用原生格式回：```json 包著、bbox_2d、0~1000 相對座標。"""
    from maplebot.brain.advisor import parse_grounding
    text = ('```json\n[{"bbox_2d": [100, 200, 300, 600], "label": "monster"},\n'
            ' {"bbox": [500, 500, 520, 540], "label": "Item"}]\n```')
    assert parse_grounding(text, 1000, 500) == [
        ("monster", (100, 100, 200, 200)), ("item", (500, 250, 20, 20))]


def test_grounding_salvages_the_objects_before_a_truncated_tail():
    from maplebot.brain.advisor import parse_grounding
    text = ('[{"bbox_2d": [0, 0, 100, 100], "label": "monster"}, '
            '{"bbox_2d": [200, 200, 300, 300], "label": "pet"}, {"bbox_2d": [40')
    assert [label for label, _ in parse_grounding(text, 100, 100)] == ["monster", "pet"]


def test_no_answer_and_an_empty_answer_are_different():
    """沒答到要重問；答「畫面上沒東西」是負樣本——混在一起會把怪全標成背景。"""
    from maplebot.brain.advisor import parse_grounding
    assert parse_grounding("我看不出來", 100, 100) is None
    assert parse_grounding("```json\n[]\n```", 100, 100) == []


def test_a_runaway_row_is_dropped_but_a_real_row_is_kept():
    """實測：模型在平台上鋪了 51 個一模一樣、等距的 22x16「item」。"""
    from maplebot.brain.advisor import _drop_runaway
    runaway = [("item", (423 + 22 * i - (i % 3 == 2), 388, 22, 16)) for i in range(8)]
    real = [("monster", (840, 125, 46, 50)), ("monster", (890, 126, 45, 49)),
            ("monster", (938, 125, 46, 50)), ("monster", (1060, 125, 46, 50))]
    coins = [("item", (300, 640, 20, 20)), ("item", (322, 640, 20, 20))]
    assert _drop_runaway(runaway + real + coins) == real + coins


def test_ground_turns_thinking_off_and_sends_a_lossless_frame(monkeypatch):
    """思考開著要 2~9 秒、常被 max_tokens 截斷；JPEG 會把 40px 小怪的描邊吃掉。"""
    import numpy as np
    sent = _capture_requests(monkeypatch,
                             reply='[{"bbox_2d": [500, 500, 750, 1000], "label": "monster"}]')
    adv, _ = _advisor()
    frame = np.zeros((100, 200, 3), np.uint8)
    frame[50:, 100:150] = (0, 200, 0)

    assert adv.ground(frame) == [("monster", (100, 50, 50, 50))]
    assert sent[0]["chat_template_kwargs"] == {"enable_thinking": False}
    url = sent[0]["messages"][0]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert (_decode_sent_image(sent[0]) == frame).all()


def test_target_check_still_lets_the_model_think(monkeypatch):
    """點名的提示是在思考開著時調出來的（67/70），不能被 grounding 的設定帶走。"""
    import numpy as np
    sent = _capture_requests(monkeypatch)
    adv, _ = _advisor()
    adv.check_target(np.zeros((100, 100, 3), np.uint8), (10, 10, 20, 20))
    assert "chat_template_kwargs" not in sent[0]
    assert sent[0]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg")


def test_a_paused_bot_is_not_asked_about_the_same_frame_again(monkeypatch):
    """暫停時沒有新畫面：一直重問同一張，每兩輪就重複暫停、存截圖、響警示。"""
    import threading
    import time

    import numpy as np
    cfg = AdvisorCfg(enabled=True, interval=0.01)
    adv = Advisor(cfg, lambda note: None, logging.getLogger("t"))
    asked = []
    seen_two = threading.Event()

    def fake_ask(frame):
        asked.append(frame)
        if len(asked) >= 2:
            seen_two.set()
        return {"status": "ok"}

    monkeypatch.setattr(adv, "_ask", fake_ask)
    adv.latest_frame = np.zeros((10, 10, 3), np.uint8)
    adv.start()
    time.sleep(0.15)                                   # 十幾輪都是同一張
    assert len(asked) == 1
    adv.latest_frame = np.ones((10, 10, 3), np.uint8)  # 恢復後的新畫面照問
    assert seen_two.wait(1.0)
    adv.stop()
