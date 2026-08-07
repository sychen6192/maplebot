"""遠端怪物偵測：把畫面丟給另一台機器上的 YOLO 推理伺服器。

適用情境：遊戲跑在筆電/主力機，GPU 在另一台工作站。
伺服器端請跑 tools/serve_yolo.py。

協定刻意做得很簡單（雙方都只用標準函式庫）：
  POST <endpoint>  body = JPEG bytes, Content-Type: image/jpeg
  回應 JSON: {"mobs": [{"name","cx","cy","w","h","score"}, ...]}
座標是相對於送出去的那張圖（也就是 playfield ROI）。

連線會保持重用（HTTP keep-alive）——每次重開 TCP 在區網無所謂，但走
VPN/WiFi 時每張圖多付一次握手來回，延遲會差很多。連線被對方關掉時
會自動重連一次。

網路出問題時回傳空清單而不是拋例外——bot 會當作「沒看到怪」繼續巡邏，
不會因為斷線就崩潰或做出危險動作。
"""
import http.client
import json
import time
import urllib.parse
from typing import List, Optional

import cv2
import numpy as np

from .mobs import Mob

WARN_INTERVAL = 10.0   # 連線失敗的警告最多幾秒印一次，避免洗版


def parse_payload(data: dict) -> List[Mob]:
    mobs = []
    for m in data.get("mobs", []):
        mobs.append(Mob(
            cx=int(m["cx"]), cy=int(m["cy"]),
            w=int(m["w"]), h=int(m["h"]),
            score=float(m.get("score", 0.0)),
            name=str(m.get("name", "mob")),
        ))
    return mobs


class RemoteMobDetector:
    def __init__(self, endpoint: str, confidence: float = 0.5, timeout: float = 1.0,
                 jpeg_quality: int = 80, max_width: int = 640, logger=None):
        if not endpoint:
            raise ValueError("vision.mob_detector=remote 時必須設定 vision.remote_endpoint")
        parsed = urllib.parse.urlparse(endpoint)
        if not parsed.hostname:
            raise ValueError(f"remote_endpoint 格式不正確: {endpoint!r}"
                             "（要像 http://192.168.1.50:8100/detect）")
        self.endpoint = endpoint
        self._https = parsed.scheme == "https"
        self._host = parsed.hostname
        self._port = parsed.port or (443 if self._https else 80)
        self._path = parsed.path or "/detect"
        self.confidence = confidence
        self.timeout = timeout
        self.jpeg_quality = jpeg_quality
        self.max_width = max_width
        self.log = logger
        self.failures = 0
        self._conn: Optional[http.client.HTTPConnection] = None
        self._last_warn = 0.0

    # ---- 連線管理 ----

    def _connect(self) -> http.client.HTTPConnection:
        if self._conn is None:
            cls = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
            self._conn = cls(self._host, self._port, timeout=self.timeout)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _warn(self, msg: str) -> None:
        now = time.monotonic()
        if now - self._last_warn < WARN_INTERVAL:
            return
        self._last_warn = now
        if self.log:
            self.log.warning("遠端偵測失敗（暫時當作沒看到怪）: %s", msg)
        else:
            print(f"[remote] {msg}")

    def _post(self, body: bytes) -> Optional[bytes]:
        """送出一張圖；連線被對方關掉時重連一次再試。"""
        path = f"{self._path}?conf={self.confidence}"
        headers = {"Content-Type": "image/jpeg", "Content-Length": str(len(body))}
        last_err = None
        for attempt in (1, 2):
            try:
                conn = self._connect()
                conn.request("POST", path, body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read()          # 一定要讀完，連線才能重用
                if resp.status != 200:
                    self._warn(f"HTTP {resp.status}")
                    return None
                return data
            except (http.client.HTTPException, OSError, TimeoutError) as e:
                last_err = e
                self.close()                # 連線已壞，下一輪重建
        self._warn(f"{type(last_err).__name__}: {last_err}")
        return None

    # ---- 偵測 ----

    def detect(self, playfield_bgr: np.ndarray) -> List[Mob]:
        # YOLO 內部本來就會把圖縮到 640px，所以送原尺寸只是浪費頻寬——
        # 先縮好再送，收到框後按比例還原成 playfield 座標。
        img = playfield_bgr
        scale = 1.0
        h, w = playfield_bgr.shape[:2]
        if self.max_width and w > self.max_width:
            scale = self.max_width / w
            img = cv2.resize(img, (self.max_width, max(int(round(h * scale)), 1)),
                             interpolation=cv2.INTER_AREA)

        ok, buf = cv2.imencode(".jpg", img,
                               [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        if not ok:
            self._warn("JPEG 編碼失敗")
            return []

        raw = self._post(buf.tobytes())
        if raw is None:
            self.failures += 1
            return []

        try:
            mobs = parse_payload(json.loads(raw))
        except json.JSONDecodeError as e:
            self.failures += 1
            self._warn(f"回應不是合法 JSON: {e}")
            return []
        except (KeyError, TypeError, ValueError) as e:
            self.failures += 1
            self._warn(f"回應格式不符: {e}")
            return []
        self.failures = 0

        if scale != 1.0:
            inv = 1.0 / scale
            mobs = [Mob(cx=round(m.cx * inv), cy=round(m.cy * inv),
                        w=round(m.w * inv), h=round(m.h * inv),
                        score=m.score, name=m.name) for m in mobs]
        return mobs
