"""遠端偵測客戶端：對真的 HTTP 伺服器驗證編碼、解析與各種失敗情境。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from maplebot.vision.remote_mobs import RemoteMobDetector, parse_payload

FRAME = np.full((120, 200, 3), 90, dtype=np.uint8)


def _serve(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/detect"


class _Base(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def good_server():
    received = {}

    class H(_Base):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            received["body"] = self.rfile.read(n)
            received["path"] = self.path
            received["ctype"] = self.headers.get("Content-Type")
            self._send(200, json.dumps({"mobs": [
                {"name": "snail", "cx": 100, "cy": 60, "w": 30, "h": 24, "score": 0.91},
                {"name": "slime", "cx": 20, "cy": 40, "w": 26, "h": 22, "score": 0.77},
            ]}).encode())

    server, url = _serve(H)
    yield url, received
    server.shutdown()


def test_detect_returns_mobs(good_server):
    url, _ = good_server
    mobs = RemoteMobDetector(url, timeout=5).detect(FRAME)
    assert len(mobs) == 2
    assert (mobs[0].name, mobs[0].cx, mobs[0].cy) == ("snail", 100, 60)
    assert mobs[0].score == pytest.approx(0.91)


def test_sends_jpeg_and_confidence(good_server):
    url, received = good_server
    RemoteMobDetector(url, confidence=0.35, timeout=5).detect(FRAME)
    assert received["ctype"] == "image/jpeg"
    assert received["body"][:2] == b"\xff\xd8"      # JPEG magic
    assert "conf=0.35" in received["path"]


def test_connection_refused_returns_empty():
    det = RemoteMobDetector("http://127.0.0.1:9/detect", timeout=0.5)
    assert det.detect(FRAME) == []
    assert det.failures == 1


def test_server_error_returns_empty():
    class H(_Base):
        def do_POST(self):
            self._send(500, b"boom")

    server, url = _serve(H)
    try:
        assert RemoteMobDetector(url, timeout=5).detect(FRAME) == []
    finally:
        server.shutdown()


def test_malformed_json_returns_empty():
    class H(_Base):
        def do_POST(self):
            self._send(200, b"not json at all")

    server, url = _serve(H)
    try:
        assert RemoteMobDetector(url, timeout=5).detect(FRAME) == []
    finally:
        server.shutdown()


def test_missing_fields_returns_empty():
    class H(_Base):
        def do_POST(self):
            self._send(200, json.dumps({"mobs": [{"name": "x"}]}).encode())

    server, url = _serve(H)
    try:
        assert RemoteMobDetector(url, timeout=5).detect(FRAME) == []
    finally:
        server.shutdown()


def test_failures_reset_after_success(good_server):
    url, _ = good_server
    det = RemoteMobDetector(url, timeout=5)
    det.failures = 3
    det.detect(FRAME)
    assert det.failures == 0


def test_empty_endpoint_rejected():
    with pytest.raises(ValueError):
        RemoteMobDetector("")


def test_parse_payload_defaults():
    mobs = parse_payload({"mobs": [{"cx": 1, "cy": 2, "w": 3, "h": 4}]})
    assert mobs[0].name == "mob" and mobs[0].score == 0.0
    assert parse_payload({}) == []
