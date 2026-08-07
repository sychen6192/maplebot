"""遠端怪物偵測：把畫面丟給另一台機器上的 YOLO 推理伺服器。

適用情境：遊戲跑在筆電/主力機，GPU 在另一台工作站。
伺服器端請跑 tools/serve_yolo.py。

協定刻意做得很簡單（雙方都只用標準函式庫）：
  POST <endpoint>  body = JPEG bytes, Content-Type: image/jpeg
  回應 JSON: {"mobs": [{"name","cx","cy","w","h","score"}, ...]}
座標是相對於送出去的那張圖（也就是 playfield ROI）。

網路出問題時回傳空清單而不是拋例外——bot 會當作「沒看到怪」繼續巡邏，
不會因為斷線就崩潰或做出危險動作。
"""
import json
import time
import urllib.error
import urllib.request
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
                 jpeg_quality: int = 80, logger=None):
        if not endpoint:
            raise ValueError("vision.mob_detector=remote 時必須設定 vision.remote_endpoint")
        self.endpoint = endpoint
        self.confidence = confidence
        self.timeout = timeout
        self.jpeg_quality = jpeg_quality
        self.log = logger
        self.failures = 0
        self._last_warn = 0.0

    def _warn(self, msg: str) -> None:
        now = time.monotonic()
        if now - self._last_warn < WARN_INTERVAL:
            return
        self._last_warn = now
        if self.log:
            self.log.warning("遠端偵測失敗（暫時當作沒看到怪）: %s", msg)
        else:
            print(f"[remote] {msg}")

    def detect(self, playfield_bgr: np.ndarray) -> List[Mob]:
        ok, buf = cv2.imencode(".jpg", playfield_bgr,
                               [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        if not ok:
            self._warn("JPEG 編碼失敗")
            return []

        url = f"{self.endpoint}?conf={self.confidence}"
        req = urllib.request.Request(url, data=buf.tobytes(), method="POST",
                                     headers={"Content-Type": "image/jpeg"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            self.failures += 1
            self._warn(f"{type(e).__name__}: {e}")
            return []
        except json.JSONDecodeError as e:
            self.failures += 1
            self._warn(f"回應不是合法 JSON: {e}")
            return []

        try:
            mobs = parse_payload(data)
        except (KeyError, TypeError, ValueError) as e:
            self.failures += 1
            self._warn(f"回應格式不符: {e}")
            return []
        self.failures = 0
        return mobs
