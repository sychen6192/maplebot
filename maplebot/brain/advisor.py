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
    "abnormal 只包括：畫面正中央跳出**需要按按鈕才能關掉的互動式對話框**"
    "（確定/取消）、驗證碼視窗、斷線或錯誤訊息、整片黑屏、角色死亡畫面。"
    "以下都是**正常的常駐介面，一律算 ok**：畫面上下緣的 HP/MP/EXP 條與功能列、"
    "左上角小地圖、右上角任務指引面板、角色腳下的名字與勳章名牌、"
    "聊天視窗與跑馬燈公告、畫面上的傷害數字與技能特效。"
    "只要沒有擋住操作的互動式視窗就回 ok。"
)


class Advisor:
    def __init__(self, cfg: AdvisorCfg, on_abnormal: Callable[[str], None], logger):
        self.cfg = cfg
        self.on_abnormal = on_abnormal
        self.log = logger
        self.latest_frame: Optional[np.ndarray] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # 連續判定 abnormal 幾輪了。一次誤判就暫停等於整晚白掛——實測 VLM 會
        # 把角色腳下常駐的勳章名牌看成「彈出視窗」。真正的異常（對話框、
        # 斷線、死亡）不會下一輪就自己消失，多等一輪的代價極小。
        self._abnormal_streak = 0

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
            self.consider(verdict)

    def consider(self, verdict: dict) -> bool:
        """收下一輪判定；回傳有沒有真的通報異常（＝暫停 bot）。

        要連續 cfg.confirm 輪都判定異常才通報：一次誤判就暫停等於整晚白掛，
        而真正的異常（對話框、斷線、死亡）不會下一輪就自己消失。
        """
        status = verdict.get("status", "ok")
        note = verdict.get("note", "")
        if status == "ok":
            self._abnormal_streak = 0
            self.log.debug("VLM 督導: ok %s", note)
            return False
        self._abnormal_streak += 1
        if self._abnormal_streak < self.cfg.confirm:
            self.log.info("VLM 督導判定 %s（第 %d 次，要連續 %d 次才動作）: %s",
                          status, self._abnormal_streak, self.cfg.confirm, note)
            return False
        self.log.warning("VLM 督導連續 %d 次判定 %s: %s",
                         self._abnormal_streak, status, note)
        self._abnormal_streak = 0
        self.on_abnormal(f"{status}: {note}")
        return True

    def _ask(self, frame_bgr: np.ndarray) -> Optional[dict]:
        import urllib.request

        ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            return None
        b64 = base64.b64encode(buf.tobytes()).decode()
        payload = {
            "model": self.cfg.model,
            # thinking 模型（qwen3 系列）會先在 reasoning 欄位裡推理，
            # 上限給太小會在想到一半被截斷，content 永遠是空字串。
            "max_tokens": 1500,
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
        msg = data["choices"][0]["message"]
        # thinking 模型的答案在 content，但推理被截斷時 content 會是空的——
        # 這時去 reasoning 欄位撈：模型通常在推理結尾就把 JSON 寫出來了。
        for text in (msg.get("content") or "",
                     msg.get("reasoning") or msg.get("reasoning_content") or ""):
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                continue
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
        return None
