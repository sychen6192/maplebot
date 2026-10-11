"""選配：本地 VLM 督導層（slow loop）。

即時反應（打怪/走位/喝水）由快速迴圈負責；這裡每隔 interval 秒
把當前畫面丟給本地 VLM（vLLM / LM Studio / Ollama 的 OpenAI 相容
端點，例如 RTX 5090 上跑 Qwen2.5-VL-7B），請它判斷「大局」：
是否卡死、是否出現對話框/驗證視窗/異常畫面。VLM 建議暫停時，
只會把 bot 切到暫停狀態，不會執行任何 VLM 生成的指令。
"""
import base64
import json
import re
import threading
import time
from typing import Callable, List, Optional, Tuple

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


# 離線標註用：整張畫面請模型自己框出每個物件（grounding），給 YOLO 當標籤。
#
# 為什麼不是「描邊老師框、督導逐框判」：描邊的閉合核在 1366 寬是 35px，相鄰
# 的東西會黏成一框——蝸牛＋旁邊的楓幣＋蝸牛殼、三隻並排的水靈都只有一框，逐框
# 判也只能說「裡面主要是怪」，學生學到的框就是糊的；一整排擠在一起的菇菇寬過
# 上限還會整串被丟掉。10 張實測：督導判過是怪的老師框 65/68 被框到，另外多框到
# 32 隻老師漏掉的；框成怪的 118 個裡錯 2~3 個（裝飾用的大香菇、蝸牛殼＋楓幣）。
#
# 「地上一叢香菇是裝飾」那句是抽查補上的：右邊傳送門旁那叢沒有臉的香菇道具，
# 沒講的話一張圖會被框成 5 隻怪（巡邏的右端點就在那裡，學生學會了每圈都會去
# 打它）；講了之後 4 張圖各問兩次，那叢一個都沒框，真的怪一隻沒少。
#
# 關掉思考（chat_template_kwargs）：開著要 2~9 秒、輸出長的常在想到一半被
# max_tokens 截斷（10 次裡 5 次拿不到答案）；關掉後模型直接用原生的
# bbox_2d 格式回，中位數 3.2 秒。座標是 0~1000 的相對座標。
_GROUND_PROMPT = (
    "這是 MapleStory 遊戲畫面。框出畫面中每一隻怪物（蝸牛、水靈、菇菇、樹妖等會動的生物；"
    "被打時腳下會有「Lv.數字 名字」），也框出掉在地上的東西（蝸牛殼、楓幣、藥水、道具，label 用 item）、"
    "玩家角色（腳下有名字牌、沒有 Lv，label 用 player）和寵物（label 用 pet）。"
    "每個物件一個框、框要貼齊物件本身；背景的草叢、乾草堆、樹、花、介面不要框；"
    "地上一叢一叢的香菇是背景裝飾（沒有眼睛、嘴巴），也不要框——菇菇怪物有眼睛和嘴巴。"
    '以 JSON 陣列回傳，座標用 0~1000 的相對座標：[{"bbox_2d": [x1, y1, x2, y2], "label": "monster"}]'
)
_GROUND_TEMPERATURE = 0.2
# 一張畫面 20~30 個物件、每個約 25 token。給太多只是讓陷入重複的回答（見
# _drop_runaway）多跑幾秒；被截斷的尾巴 parse_grounding 會逐個物件撈回來。
_GROUND_MAX_TOKENS = 2000
_RUNAWAY_MIN = 4


def _drop_runaway(objs: List[Tuple[str, Tuple[int, int, int, int]]]
                  ) -> List[Tuple[str, Tuple[int, int, int, int]]]:
    """丟掉模型陷入重複時吐出來的整串框。

    實測一張畫面在平台上排了 51 個「item」：大小全是 22x16、y 一樣、x 每隔
    21~22px 一個，鋪滿整條平台。真的怪或掉落物並排，大小和間距不會分毫不差，
    所以同 label、同大小、在同一條線上等距排 _RUNAWAY_MIN 個以上就整串丟掉。
    """
    groups: dict = {}
    for i, (label, (_, _, w, h)) in enumerate(objs):
        groups.setdefault((label, w, h), []).append(i)
    bad = set()
    for idx in groups.values():
        if len(idx) < _RUNAWAY_MIN:
            continue
        for along, across in ((0, 1), (1, 0)):          # 橫排、直排
            line = [objs[i][1][across] for i in idx]
            if max(line) - min(line) > 1:
                continue
            pos = sorted(objs[i][1][along] for i in idx)
            steps = [b - a for a, b in zip(pos, pos[1:])]
            if max(steps) - min(steps) <= 2:
                bad.update(idx)
    return [o for i, o in enumerate(objs) if i not in bad]


def parse_grounding(text: str, width: int, height: int
                    ) -> Optional[List[Tuple[str, Tuple[int, int, int, int]]]]:
    """把模型回的 grounding 文字解成 [(label, (x, y, w, h))]，座標換回像素。

    模型常包一層 ```json、或把 bbox_2d 寫成 bbox；輸出被截斷時整個陣列 parse
    不了，就退回逐個物件撈，能救幾個是幾個。完全沒有 JSON 回 None（沒答到），
    跟回 []（模型說畫面上沒東西）分開——標註時前者要重問，後者是負樣本。
    """
    start, end = text.find("["), text.rfind("]")
    try:
        items = json.loads(text[start:end + 1]) if 0 <= start < end else None
    except json.JSONDecodeError:
        items = None
    if not isinstance(items, list):
        chunks = re.findall(r"\{[^{}]*\}", text)
        if not chunks:
            return None
        items = []
        for chunk in chunks:
            try:
                items.append(json.loads(chunk))
            except json.JSONDecodeError:
                continue
    def px(v: float, size: int) -> float:
        return min(max(v, 0.0), 1000.0) * size / 1000

    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        b = item.get("bbox_2d") or item.get("bbox")
        if not isinstance(b, list) or len(b) != 4:
            continue
        try:
            x1, y1, x2, y2 = (float(v) for v in b)
        except (TypeError, ValueError):
            continue
        x1, x2 = sorted((px(x1, width), px(x2, width)))
        y1, y2 = sorted((px(y1, height), px(y2, height)))
        w, h = int(round(x2 - x1)), int(round(y2 - y1))
        if w < 2 or h < 2:
            continue
        label = str(item.get("label") or item.get("kind") or "unknown").strip().lower()
        out.append((label, (int(round(x1)), int(round(y1)), w, h)))
    return _drop_runaway(out)


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
        asked = None
        while not self._stop.wait(self.cfg.interval):
            frame = self.latest_frame
            # 暫停時主迴圈不再擷取，latest_frame 停在暫停前那張。實測斷線那晚
            # 對同一張斷線畫面每 20~45 秒問一次：每兩次就再「暫停」一次、再存
            # 一張一樣的截圖、再響一次警示，還一直佔著模型唯一的 slot
            if frame is None or frame is asked:
                continue
            asked = frame
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

    def ground(self, frame_bgr: np.ndarray, timeout: float = 60.0
               ) -> Optional[List[Tuple[str, Tuple[int, int, int, int]]]]:
        """整張畫面請模型框出每個物件，回傳 [(label, (x, y, w, h))]（像素座標）。

        給離線標註用，不在即時迴圈裡呼叫：一次 3~5 秒，而且送 PNG——小怪只有
        40px 寬，JPEG 的色塊會吃掉描邊。逾時另外給，整張畫面比單框問題慢得多。
        回 None 表示沒拿到看得懂的答案（見 parse_grounding）。
        """
        h, w = frame_bgr.shape[:2]
        for text in self._complete(_GROUND_PROMPT, frame_bgr, _GROUND_TEMPERATURE,
                                   think=False, image_format="png",
                                   max_tokens=_GROUND_MAX_TOKENS, timeout=timeout):
            found = parse_grounding(text, w, h)
            if found is not None:
                return found
        return None

    def _ask(self, frame_bgr: np.ndarray) -> Optional[dict]:
        return self._chat(_PROMPT, frame_bgr)

    def _complete(self, prompt: str, frame_bgr: np.ndarray,
                  temperature: Optional[float] = None, think: bool = True,
                  image_format: str = "jpg", max_tokens: int = 1500,
                  timeout: Optional[float] = None) -> Tuple[str, ...]:
        """送一張圖＋提示，回傳 (content, reasoning) 兩段文字給呼叫端自己解析。"""
        import urllib.request

        if image_format == "png":
            ok, buf = cv2.imencode(".png", frame_bgr)
            mime = "image/png"
        else:
            ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
            mime = "image/jpeg"
        if not ok:
            return ()
        b64 = base64.b64encode(buf.tobytes()).decode()
        payload = {
            "model": self.cfg.model,
            # thinking 模型（qwen3 系列）會先在 reasoning 欄位裡推理，
            # 上限給太小會在想到一半被截斷，content 永遠是空字串。
            "max_tokens": max_tokens,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if not think:
            # llama.cpp / vLLM 把這個轉給 chat template；不認得的伺服器會忽略
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        req = urllib.request.Request(
            self.cfg.endpoint,
            data=json.dumps(payload).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=timeout or self.cfg.timeout) as resp:
            data = json.loads(resp.read())
        msg = data["choices"][0]["message"]
        return (msg.get("content") or "",
                msg.get("reasoning") or msg.get("reasoning_content") or "")

    def _chat(self, prompt: str, frame_bgr: np.ndarray,
              temperature: Optional[float] = None) -> Optional[dict]:
        # thinking 模型的答案在 content，但推理被截斷時 content 會是空的——
        # 這時去 reasoning 欄位撈：模型通常在推理結尾就把 JSON 寫出來了。
        for text in self._complete(prompt, frame_bgr, temperature):
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                continue
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
        return None
