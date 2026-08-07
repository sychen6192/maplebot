"""遠端偵測客戶端：對真的 HTTP 伺服器驗證編碼、解析與各種失敗情境。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
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


def test_malformed_endpoint_rejected():
    with pytest.raises(ValueError):
        RemoteMobDetector("192.168.1.50:8100/detect")   # 少了 http://


def test_connection_is_reused_across_calls():
    """keep-alive：多次偵測應共用同一條 TCP 連線（VPN/WiFi 下省下反覆握手）。"""
    conns = []

    class H(_Base):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            conns.append(self.connection)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            self.rfile.read(n)
            self._send(200, json.dumps({"mobs": []}).encode())

    server, url = _serve(H)
    try:
        det = RemoteMobDetector(url, timeout=5)
        for _ in range(3):
            det.detect(FRAME)
        assert len(conns) == 1, f"預期重用 1 條連線，實際開了 {len(conns)} 條"
        det.close()
    finally:
        server.shutdown()


def test_recovers_after_server_closes_connection():
    """伺服器用 HTTP/1.0 每次關閉連線時，客戶端要能自動重連。"""
    class H(_Base):            # 沒設 protocol_version，預設 HTTP/1.0 會關連線
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            self.rfile.read(n)
            self._send(200, json.dumps({
                "mobs": [{"name": "m", "cx": 1, "cy": 2, "w": 3, "h": 4}]}).encode())

    server, url = _serve(H)
    try:
        det = RemoteMobDetector(url, timeout=5)
        for _ in range(3):
            assert len(det.detect(FRAME)) == 1
        assert det.failures == 0
    finally:
        server.shutdown()


def test_downscale_shrinks_payload_and_rescales_boxes():
    """送出前縮圖省頻寬，收到的框要按比例還原成原尺寸座標。"""
    sent = {}

    class H(_Base):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            sent["body"] = self.rfile.read(n)
            # 在「縮圖後」的座標系回一個框
            self._send(200, json.dumps({"mobs": [
                {"name": "m", "cx": 320, "cy": 100, "w": 32, "h": 20, "score": 0.9}]}).encode())

    big = np.random.default_rng(3).integers(0, 255, (520, 1280, 3), dtype=np.uint8)
    server, url = _serve(H)
    try:
        det = RemoteMobDetector(url, timeout=5, max_width=640)
        mobs = det.detect(big)
        # 送出去的應該是 640 寬
        got = cv2.imdecode(np.frombuffer(sent["body"], np.uint8), cv2.IMREAD_COLOR)
        assert got.shape[1] == 640
        # 1280/640 = 2 倍還原
        assert (mobs[0].cx, mobs[0].cy, mobs[0].w, mobs[0].h) == (640, 200, 64, 40)
    finally:
        server.shutdown()


def test_no_downscale_when_already_small(good_server):
    url, received = good_server
    det = RemoteMobDetector(url, timeout=5, max_width=640)
    mobs = det.detect(FRAME)                       # 200x120，本來就比 640 小
    got = cv2.imdecode(np.frombuffer(received["body"], np.uint8), cv2.IMREAD_COLOR)
    assert got.shape[1] == 200
    assert mobs[0].cx == 100                       # 座標不變


def test_parse_payload_defaults():
    mobs = parse_payload({"mobs": [{"cx": 1, "cy": 2, "w": 3, "h": 4}]})
    assert mobs[0].name == "mob" and mobs[0].score == 0.0
    assert parse_payload({}) == []
