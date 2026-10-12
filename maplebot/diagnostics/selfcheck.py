"""辨識自檢：拍一張畫面、跑一遍感知與決策，把結果存成標註圖＋預覽圖＋報告。

`tools/debug_view.py --snapshot` 與 GUI 的「辨識自檢」都走 snapshot()。以前
GUI 只丟一句「存到 logs/check.png，打開來確認」，CLI 則自己印一串讀值——
同一件事兩份邏輯，改了一邊另一邊就會說出不一樣的話。現在兩邊吃同一份
VisionReport：CLI 照舊印出來，GUI 讀 JSON 之後用 verdicts() 逐條判讀。

GUI 仍然開子行程跑 debug_view，而不是在自己的行程裡呼叫 snapshot()：
OpenCV 崩潰會帶走整個控制台，YOLO 載模型要卡好幾秒。所以報告只能經由檔案
交回去，這個模組也不能 import tkinter（CI 沒有顯示環境）。
"""
import json
import os
import time
from dataclasses import asdict, dataclass, field, fields
from typing import List, Optional, Tuple

import cv2

from ..brain import fsm
from .doctor import FAIL, OK, WARN, check_status_bars
from .overlay import annotate, pct
from ..perception import Perceiver
from ..vision.mobs import make_detector

# verdicts() 第三欄的動作代號，GUI 用 {代號: callback} 分派到按鈕
CALIBRATE, NAMETAG, VISION_TAB = "calibrate", "nametag", "tab:vision"


@dataclass
class VisionReport:
    """一次自檢的結果。座標都是純 int tuple，才能直接寫成 JSON。"""
    hp: Optional[float] = None              # 0.0 ~ 1.0，None = region 不在畫面內
    mp: Optional[float] = None
    exp: Optional[float] = None
    minimap_xy: Optional[Tuple[int, int]] = None
    others: int = 0                         # 小地圖上的其他玩家數
    screen_xy: Optional[Tuple[int, int]] = None   # 角色定位；None = 退回畫面正中央
    mobs: int = 0
    followers: int = 0
    action: str = ""                        # 決策的類別名稱（Wait、Attack…）
    reason: str = ""                        # 只有 Wait / Panic 帶理由，其他是 ""
    size: Tuple[int, int] = (0, 0)          # 擷取畫面的 (寬, 高)
    image: str = ""                         # 原尺寸標註圖的絕對路徑
    preview: str = ""                       # 縮圖的絕對路徑
    # doctor.check_status_bars 的 (status, name, detail, fix)
    bar_checks: List[Tuple[str, str, str, str]] = field(default_factory=list)
    # 拍的當下有沒有載入角色名牌模板。分得出「還沒截」與「截了但這張比不中」
    nametag_template: bool = False
    # 每隻怪的 (name, cx, cy, w, h)，playfield 座標。CLI 要逐隻印出來，
    # 對照標註圖時也靠它認出是哪個框錯了
    mob_boxes: List[Tuple[str, int, int, int, int]] = field(default_factory=list)

    def to_json(self) -> str:
        # 保持 ASCII：子行程寫、GUI 讀，Windows 上任一邊忘了指定 utf-8 就會
        # 用 cp950 解碼而讀壞中文理由；跳脫成 \uXXXX 兩邊都不用管編碼
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "VisionReport":
        """JSON 沒有 tuple，讀回來把 list 換回 tuple，round-trip 才會相等。

        不認得的鍵直接略過、缺的鍵用預設值：GUI 與 debug_view 版本不一致時
        （舊的 check.json 還留在 logs/）不該讓整個結果視窗開不起來。
        """
        data = json.loads(text)
        known = {f.name for f in fields(cls)}
        kw = {k: v for k, v in data.items() if k in known}
        for k in ("minimap_xy", "screen_xy", "size"):
            if kw.get(k) is not None:
                kw[k] = tuple(kw[k])
        for k in ("bar_checks", "mob_boxes"):
            if k in kw:
                kw[k] = [tuple(row) for row in kw[k]]
        return cls(**kw)


def fit(w: int, h: int, box_w: int, box_h: int) -> Tuple[int, int]:
    """等比縮到放得進 box_w x box_h；本來就放得下就維持原尺寸，不放大。

    放大只會把標註文字弄糊，不會多出任何資訊。
    """
    s = min(box_w / w, box_h / h, 1.0)
    return max(1, round(w * s)), max(1, round(h * s))


def preview_path(out_path: str) -> str:
    """logs/check.png -> logs/check_preview.png。一律 PNG：tk.PhotoImage 不讀 JPG。"""
    return os.path.splitext(out_path)[0] + "_preview.png"


def _write(path: str, img) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    # cv2.imwrite 寫失敗只回 False、不丟例外。GUI 靠子行程的結束碼判斷成敗，
    # 靜默失敗的話它會把上一次留下的舊圖當成這次的結果秀出來
    if not cv2.imwrite(path, img):
        raise OSError(f"寫不出圖檔: {path}")


def _xy(p) -> Optional[Tuple[int, int]]:
    # 感知層的座標可能是 numpy 整數，json 不收
    return None if p is None else (int(p[0]), int(p[1]))


def _ratio(v) -> Optional[float]:
    return None if v is None else float(v)


def snapshot(cfg, profile, capture, out_path: str,
             preview_box: Tuple[int, int] = (640, 400),
             perceiver: Optional[Perceiver] = None) -> VisionReport:
    """抓一幀，跑 Perceiver -> fsm.decide -> overlay.annotate，寫出兩張圖並回傳報告。

    out_path 存原尺寸標註圖（座標讀值要對得上），preview_path(out_path) 存縮到
    preview_box 內的縮圖。縮圖在這裡用 INTER_AREA 先縮好，因為 GUI 那邊的
    PhotoImage.subsample 只能整數倍縮：806x629 的畫面 subsample(2) 只剩
    403x315，塞 640x400 的位置太小；這裡縮出來是 513x400。

    perceiver 不給就照設定自己建一個；debug_view 會傳它自己的進來，
    這樣印出來的 detector.explain() 講的就是這一次的偵測。
    """
    if perceiver is None:
        perceiver = Perceiver(cfg, make_detector(cfg.vision, profile.templates_dir))
    pf = cfg.region("playfield")
    center = (pf[2] // 2, pf[3] // 2)

    now = time.monotonic()      # perceive 與 decide 一定要拿同一個 now
    frame = capture.grab()
    state = perceiver.perceive(frame, now)
    action = fsm.decide(state, cfg, profile, fsm.Runtime(), now, center)
    followers = perceiver.last_followers
    canvas = annotate(frame, state, action, cfg, followers=followers, profile=profile)

    h, w = canvas.shape[:2]
    pw, ph = fit(w, h, *preview_box)
    small = canvas if (pw, ph) == (w, h) else \
        cv2.resize(canvas, (pw, ph), interpolation=cv2.INTER_AREA)
    small_path = preview_path(out_path)
    _write(out_path, canvas)
    _write(small_path, small)

    return VisionReport(
        hp=_ratio(state.hp), mp=_ratio(state.mp), exp=_ratio(state.exp),
        minimap_xy=_xy(state.minimap_xy),
        others=len(state.other_players),
        screen_xy=_xy(state.screen_xy),
        mobs=len(state.mobs),
        followers=len(followers),
        action=type(action).__name__,
        reason=str(getattr(action, "reason", "") or ""),
        size=(int(w), int(h)),
        image=os.path.abspath(out_path),
        preview=os.path.abspath(small_path),
        bar_checks=[(c.status, c.name, c.detail, c.fix)
                    for c in check_status_bars(cfg, frame)],
        nametag_template=perceiver.nametag is not None,
        mob_boxes=[(str(m.name), int(m.cx), int(m.cy), int(m.w), int(m.h))
                   for m in state.mobs],
    )


Verdict = Tuple[str, str, Optional[str]]


def verdicts(report: VisionReport,
             nametag_exists: Optional[bool] = None) -> List[Verdict]:
    """把報告翻成給人看的判讀：[(status, 文字, 動作代號或 None)]。

    status 是 doctor 的 'ok' / 'warn' / 'fail'；文字不含符號，√ ！ × 由畫面
    依 status 加上。nametag_exists 不給就用報告自己記的「拍的當下有沒有
    名牌模板」——拍完才截名牌的話，拿現在的狀態判讀會誤報「比不中名牌」。
    """
    has_tag = report.nametag_template if nametag_exists is None else nametag_exists
    out: List[Verdict] = []

    if report.hp is None:
        out.append((FAIL, "讀不到 HP：HP 條的 region 跑到畫面外了", CALIBRATE))
    elif report.hp <= 0.0:
        # bar_ratio 找不到紅色一律回 0.0；自檢時角色不會是死的，0% 幾乎都是框歪
        out.append((FAIL, "HP 讀成 0%：HP region 多半框到外框、數字或旁邊的 UI",
                    CALIBRATE))
    else:
        out.append((OK, f"HP {pct(report.hp)}・MP {pct(report.mp)}・"
                        f"EXP {pct(report.exp)}：跟遊戲裡顯示的一樣嗎？", None))
    if report.mp is None:
        out.append((WARN, "讀不到 MP（沒框 MP 條的話，喝 MP 藥不會觸發）", None))

    if report.minimap_xy is not None:
        x, y = report.minimap_xy
        out.append((OK, f"小地圖上找到角色 ({x}, {y})（綠圈）", None))
    else:
        out.append((FAIL, "小地圖上找不到角色黃點：小地圖 region 要只框地圖本體",
                    CALIBRATE))
    if report.others > 0:
        out.append((WARN, f"小地圖上有 {report.others} 位其他玩家（紅圈），"
                          "有人時會暫停", None))

    if report.screen_xy is not None:
        # 沒有名牌模板還量得到，就是第二順位的組隊紅條給的
        out.append((OK, "找到角色名牌（紫色）" if has_tag
                    else "靠組隊紅條找到角色（紫色）", None))
    elif not has_tag:
        out.append((WARN, "還沒截角色名牌，角色位置改用畫面正中央——可能把自己當成怪",
                    NAMETAG))
    else:
        out.append((WARN, "這張畫面比不中名牌（被擋住、換了角色或解析度）", NAMETAG))

    if report.mobs > 0:
        out.append((OK, f"框到 {report.mobs} 隻怪物（黃框）。"
                        "框到背景就到「辨識」把描邊黑度調低（8→4）", None))
    else:
        out.append((WARN, "沒框到任何怪物。畫面上明明有怪的話，把描邊黑度往上調"
                          "（有些客戶端的描邊不是純黑，要 16~32 才抓得到）",
                    VISION_TAB))
    if report.followers > 0:
        out.append((OK, f"{report.followers} 個跟隨物（灰框，不會被攻擊）", None))

    for status, name, detail, fix in report.bar_checks:
        # doctor 的修法都是「執行 python tools/calibrate.py --write」，在 GUI 裡
        # 就是校正按鈕；長篇的原因說明留在報告裡，不塞進一行判讀
        act = CALIBRATE if status != OK and "calibrate.py" in fix else None
        out.append((status, f"{name}：{detail}" if detail else name, act))
    return out
