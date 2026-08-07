"""選配：本地 VLM 督導層（slow loop）。

即時反應（打怪/走位/喝水）由快速迴圈負責；這裡每隔 interval 秒
把當前畫面丟給本地 VLM（vLLM / LM Studio / Ollama 的 OpenAI 相容
端點，例如 RTX 5090 上跑 Qwen2.5-VL-7B），請它判斷「大局」：
是否卡死、是否出現對話框/驗證視窗/異常畫面。VLM 建議暫停時，
只會把 bot 切到暫停狀態，不會執行任何 VLM 生成的指令。
"""
import base64
import json
import threading
import time
from typing import Callable, Optional

import cv2
import numpy as np

from ..config import AdvisorCfg

_PROMPT = (
    "這是 MapleStory 遊戲畫面。請判斷目前狀況並只回傳一行 JSON："
    '{"status": "ok" 或 "stuck" 或 "abnormal", "note": "一句話說明"}。'
    "角色卡住不動、出現對話框、驗證視窗、斷線訊息、黑屏都算 abnormal。"
)


class Advisor:
    def __init__(self, cfg: AdvisorCfg, on_abnormal: Callable[[str], None], logger):
        self.cfg = cfg
        self.on_abnormal = on_abnormal
        self.log = logger
        self.latest_frame: Optional[np.ndarray] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.cfg.enabled:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="advisor")
        self._thread.start()
        self.log.info("VLM 督導層已啟動: %s @ %s（每 %.0fs 檢查一次）",
                      self.cfg.model, self.cfg.endpoint, self.cfg.interval)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.cfg.interval):
            frame = self.latest_frame
            if frame is None:
                continue
            try:
                verdict = self._ask(frame)
            except Exception as e:
                self.log.warning("VLM 督導層呼叫失敗（略過本輪）: %s", e)
                continue
            if verdict is None:
                continue
            status = verdict.get("status", "ok")
            note = verdict.get("note", "")
            if status == "ok":
                self.log.debug("VLM 督導: ok %s", note)
            else:
                self.log.warning("VLM 督導判定 %s: %s", status, note)
                self.on_abnormal(f"{status}: {note}")

    def _ask(self, frame_bgr: np.ndarray) -> Optional[dict]:
        import urllib.request

        ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            return None
        b64 = base64.b64encode(buf.tobytes()).decode()
        payload = {
            "model": self.cfg.model,
            "max_tokens": 120,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }],
        }
        req = urllib.request.Request(
            self.cfg.endpoint,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"].strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
