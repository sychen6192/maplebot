"""推理伺服器的請求處理（不載入 ultralytics，用假模型驗證協定與設定）。"""
import importlib.util
import json
import os
import threading
from http.server import ThreadingHTTPServer

import cv2
import numpy as np
import pytest

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tools", "serve_yolo.py")


def _load_tool():
    spec = importlib.util.spec_from_file_location("serve_yolo", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeTensor(list):
    """模仿 ultralytics 回傳的 tensor：支援 .tolist()。"""

    def tolist(self):
        return list(self)


class _FakeBox:
    def __init__(self, xyxy, cls, conf):
        self.xyxy = [_FakeTensor(xyxy)]
        self.cls = [cls]
        self.conf = [conf]


class _FakeResult:
    names = {0: "snail"}

    def __init__(self):
        self.boxes = [_FakeBox([10.0, 20.0, 40.0, 44.0], 0, 0.87)]


class _FakeModel:
    def __init__(self):
        self.calls = []

    def predict(self, img, conf=0.5, device="0", verbose=False):
        self.calls.append({"shape": img.shape, "conf": conf})
        return [_FakeResult()]


@pytest.fixture
def server():
    mod = _load_tool()
    model = _FakeModel()
    handler = mod.build_handler(model, 0.5, "cpu", "fake.pt", False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", model, handler
    srv.shutdown()


def test_nagle_disabled_on_handler(server):
    """標頭與內容分兩次寫入，Nagle 沒關就會被 delayed ACK 拖住。"""
    _, _, handler = server
    assert handler.disable_nagle_algorithm is True
    assert handler.protocol_version == "HTTP/1.1"   # keep-alive 需要


def test_health(server):
    import urllib.request
    base, _, _ = server
    with urllib.request.urlopen(f"{base}/health", timeout=5) as r:
        data = json.loads(r.read())
    assert data["status"] == "ok" and data["model"] == "fake.pt"


def test_detect_returns_centered_boxes(server):
    from maplebot.vision.remote_mobs import RemoteMobDetector

    base, model, _ = server
    det = RemoteMobDetector(f"{base}/detect", confidence=0.33, timeout=5, max_width=0)
    mobs = det.detect(np.full((100, 200, 3), 80, dtype=np.uint8))

    assert len(mobs) == 1
    m = mobs[0]
    assert (m.cx, m.cy, m.w, m.h) == (25, 32, 30, 24)   # xyxy 轉中心點+寬高
    assert m.name == "snail" and m.score == pytest.approx(0.87)
    assert model.calls[0]["conf"] == pytest.approx(0.33)  # 客戶端門檻有帶過去


def test_bad_image_rejected(server):
    import urllib.error
    import urllib.request
    base, _, _ = server
    req = urllib.request.Request(f"{base}/detect", data=b"not an image",
                                 method="POST",
                                 headers={"Content-Type": "image/jpeg"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 400


def test_unknown_path_404(server):
    import urllib.error
    import urllib.request
    base, _, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"{base}/nope", timeout=5)
    assert e.value.code == 404
