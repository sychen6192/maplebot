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
from typing import Callable, Optional, Tuple

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

# 一直打不死時問「正在打的那個框是什麼」。實測 1366x768 訓練場的偵測框有 4 成
# 不是怪：其他玩家、寵物、角色自己、地上的掉落物（蝸牛殼被描邊偵測當成怪）。
#
# 送的是框附近**裁切放大**的圖，不是整張畫面。同一個框重複問 10 次才看得出來：
# 整張 1366x768 送過去（框只佔一小塊），「掛機玩家＋寵物」那個框在伺服器預設
# 溫度下只答對 5/10，某隻藍寶在溫度 0.2 下 7/10 被說成玩家——單問一次看起來
# 很準是運氣。
#
# 名牌的說法也是量出來的：怪被打的時候腳下**也會**出現「Lv.6 綠水靈」這種名牌，
# 只說「怪沒有名字」的話模型會把正在打的怪看成玩家；掉落物沒提的話 30 次只抓到
# 17 次。照下面寫法、裁切放大（512px）、溫度 0.2，14 個框各問 10 次：不是怪的
# 抓到 67/70、真的怪誤判 0/70（含頂著 Lv 名牌的），中位數 0.5 秒。
_TARGET_PROMPT = (
    "這是 MapleStory 遊戲畫面的局部放大，上面有一個洋紅色方框。請判斷方框裡主要的東西是什麼，"
    '只回傳一行 JSON：{"kind": "monster" 或 "player" 或 "pet" 或 "npc" 或 "item" 或 "nothing", '
    '"note": "一句話"}。判斷方式：玩家角色、寵物、NPC 腳下有深色底的名字牌，名字前面**沒有** Lv；'
    "怪物（蝸牛、水靈、菇菇等）平常腳下沒有名字，被攻擊時會出現「Lv.數字 名字」的名牌"
    "（例如 Lv.6 綠水靈），那仍然是怪物。掉在地上的東西（蝸牛殼、楓幣、道具）是 item，不是怪物。"
    "方框同時框到好幾樣東西時，以框裡佔最大的為準。"
)
_TARGET_COLOR = (255, 0, 255)      # BGR 洋紅：遊戲畫面裡幾乎不會出現這個顏色
_TARGET_SIDE = 512                 # 裁切圖放大到的長邊
_TARGET_PAD = 60                   # 框外至少留多少 px 的背景（名牌、旁邊的東西）
_TARGET_NAMETAG = 30               # 下方多留的 px：名牌掛在腳下
_TARGET_TEMPERATURE = 0.2


def crop_for_target(frame_bgr: np.ndarray, box: Tuple[int, int, int, int]) -> np.ndarray:
    """把 box 連同周圍（含腳下名牌）裁下來、畫上洋紅框、放大到長邊 _TARGET_SIDE。"""
    x, y, w, h = box
    pad = max(_TARGET_PAD, w, h)
    fh, fw = frame_bgr.shape[:2]
    x0, y0 = max(x - pad, 0), max(y - pad, 0)
    x1, y1 = min(x + w + pad, fw), min(y + h + pad + _TARGET_NAMETAG, fh)
    crop = frame_bgr[y0:y1, x0:x1].copy()
    cv2.rectangle(crop, (x - x0, y - y0), (x - x0 + w, y - y0 + h), _TARGET_COLOR, 2)
    scale = _TARGET_SIDE / max(crop.shape[:2])
    return cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


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
        # 模型伺服器常常只有一個 slot（實測的那台就是），排隊只會讓答案更舊，
        # 所以同一時間最多一個目標詢問在路上
        self._target_busy = threading.Lock()

    @property
    def checks_targets(self) -> bool:
        return self.cfg.enabled and self.cfg.target_check

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

    def check_target_async(self, frame_bgr: np.ndarray, box: Tuple[int, int, int, int],
                           on_result: Callable[[Optional[dict]], None]) -> bool:
        """背景問一次「box 框住的是不是怪」，答案交給 on_result（在背景執行緒呼叫）。

        box 是整張畫面（client 區）座標的 (x, y, w, h)。回傳 False = 沒送出
        （沒開、或上一個詢問還沒回來），呼叫端下一輪再試即可。
        呼叫失敗時 on_result 收到 None——督導層自己壞掉不能把 bot 停下來，
        原本的 attack_stall_seconds 兜底照常運作。
        """
        if not self.checks_targets or not self._target_busy.acquire(blocking=False):
            return False
        frame = frame_bgr.copy()      # 主迴圈下一幀會覆寫，畫框也不能畫在原圖上

        def work() -> None:
            try:
                verdict = self.check_target(frame, box)
            except Exception as e:
                self.log.warning("督導層目標詢問失敗（照舊由 attack_stall_seconds 兜底）: %s", e)
                verdict = None
            finally:
                self._target_busy.release()
            on_result(verdict)

        threading.Thread(target=work, daemon=True, name="advisor-target").start()
        return True

    def check_target(self, frame_bgr: np.ndarray,
                     box: Tuple[int, int, int, int]) -> Optional[dict]:
        """同步版：把 box 附近裁切放大、標上洋紅框再問。回傳 {"kind": ..., "note": ...}。"""
        return self._chat(_TARGET_PROMPT, crop_for_target(frame_bgr, box),
                          temperature=_TARGET_TEMPERATURE)

    def _ask(self, frame_bgr: np.ndarray) -> Optional[dict]:
        return self._chat(_PROMPT, frame_bgr)

    def _chat(self, prompt: str, frame_bgr: np.ndarray,
              temperature: Optional[float] = None) -> Optional[dict]:
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
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }],
        }
        if temperature is not None:
            payload["temperature"] = temperature
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        req = urllib.request.Request(
            self.cfg.endpoint,
            data=json.dumps(payload).encode(),
            headers=headers,
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
