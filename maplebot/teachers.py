"""自動標註的「老師」：負責產生 YOLO 預標註的偵測器。

知識蒸餾的想法是：先有一個「能用但不夠快／不夠好」的老師，用它把一堆畫面
標起來，再把學生（YOLO）練到比老師更快更穩。這裡有兩種老師：

- **outline（描邊）**：就是 bot 現在跑的那個偵測器。**完全不用截模板**，
  換地圖直接標。代價是它不知道怪的種類，全部標成同一類 `mob`。
- **template（模板匹配）**：要先用 tools/grab_template.py 把每種怪截下來，
  換地圖要重截，但標出來的框有怪種資訊。

兩件事要先講清楚：

1. **老師抓不到的，學生也學不到。** 標之前先用 tools/debug_view.py 把門檻
   調到畫面上真的框得到怪，再開始標；不要指望 YOLO 幫你補上老師漏掉的。
2. 描邊老師走的是**跟即時執行完全同一條路**（塗掉疊在畫面上的 UI、找出角色
   實際位置把自己挖掉），不然訓練資料裡會多出「小地圖是怪」「角色自己是怪」
   這種標註——而那正是學生會學得最牢的東西。

標之前先看幾張：`python tools/autolabel.py --preview 6`
"""
import re
from dataclasses import replace
from typing import List, Optional, Tuple

import numpy as np

from .config import REFERENCE_WIDTH
from .vision import nametag, player_bar, playfield
from .vision.mobs import Mob, TemplateMobDetector, make_detector

Labeled = List[Tuple[int, Mob]]     # [(類別編號, 框), ...]

TEACHERS = ("outline", "template")


def class_from_template_name(name: str) -> str:
    """模板檔名 snail_01 -> 類別 snail。"""
    return re.sub(r"_\d+$", "", name)


class TemplateTeacher:
    """模板匹配老師：有怪種資訊，但每種怪都要先截圖。"""

    def __init__(self, templates_dir: str, threshold: float,
                 single_class: bool = False, class_name: str = "mob"):
        self.det = TemplateMobDetector(templates_dir, threshold)
        if not self.det.templates:
            raise ValueError(f"{templates_dir} 裡沒有任何模板 PNG，"
                             "先用 tools/grab_template.py 蒐集，"
                             "或改用描邊老師（--teacher outline，不用模板）")
        self.single_class = single_class
        self.class_name = class_name
        if single_class:
            self.classes = [class_name]
        else:
            self.classes = sorted({class_from_template_name(n)
                                   for n, _ in self.det.templates})
        self._idx = {c: i for i, c in enumerate(self.classes)}

    def label(self, img: np.ndarray) -> Labeled:
        out: Labeled = []
        for mob in self.det.detect(img):
            cls = (self.class_name if self.single_class
                   else class_from_template_name(mob.name))
            out.append((self._idx[cls], mob))
        return out

    def explain(self) -> str:
        return f"模板 {len(self.det.templates) // 2} 種（含左右翻轉共 {len(self.det.templates)} 張）"

    def reset(self) -> None:
        """清空統計。預覽用掉的那幾張不該算進正式那一輪的比率裡。"""


class OutlineTeacher:
    """描邊偵測老師：不用任何模板，你畫面上描邊抓得到的怪它就標得到。

    偵測器是用 `make_detector` 建的——跟 bot 執行時建的是同一個函式，所以
    調 config 裡的 outline_* 參數，老師與實跑會一起變，不會各走各的。
    """

    def __init__(self, cfg, class_name: str = "mob",
                 black_level: Optional[int] = None):
        vc = cfg.vision
        if black_level is not None:
            vc = replace(vc, outline_black_level=black_level)
        # 強制走描邊：使用者可能已經把 mob_detector 換成 yolo，那不能拿來當老師
        # （學生教學生，錯的地方只會被放大）
        self.det = make_detector(replace(vc, mob_detector="outline"), "")
        self.classes = [class_name]

        pf = cfg.regions.get("playfield")
        self.origin = (pf[0], pf[1]) if pf else (0, 0)
        self.exclude = list(vc.mob_exclude)
        self.overlays = playfield.overlay_rects(cfg.regions)
        self.player_box = vc.outline_player_box
        self.locate_player_bar = vc.locate_player_bar
        self.nametag = nametag.load_locator(
            vc.ui_templates_dir, vc.nametag_offset,
            vc.nametag_threshold) if vc.locate_nametag else None
        # 有幾張圖真的量到角色位置。量不到就只能照畫面正中央挖，而「角色永遠
        # 在正中央」在楓谷是錯的（實測可以差 200px），那些圖很可能把角色標成怪。
        self.images = 0
        self.player_found = 0

    def label(self, img: np.ndarray) -> Labeled:
        self.images += 1
        img = playfield.blank_rects(img, self.exclude, self.origin)
        scale = img.shape[1] / REFERENCE_WIDTH

        xy = self.nametag.locate(img, scale) if self.nametag is not None else None
        if xy is None and self.locate_player_bar:
            xy = player_bar.find_player_bar(img, scale=scale,
                                            mask_out=self.overlays)
        if xy is not None:
            self.player_found += 1
        self.det.player_xy = xy

        mobs = playfield.drop_at(self.det.detect(img), xy,
                                 self.player_box, scale)
        return [(0, m) for m in mobs]

    def reset(self) -> None:
        self.images = 0
        self.player_found = 0

    def explain(self) -> str:
        """最後一張圖的偵測統計 + 角色定位命中率。"""
        out = self.det.explain()
        if not self.images:
            return out
        miss = self.images - self.player_found
        out += (f"\n    角色定位：{self.player_found}/{self.images} 張量到"
                "（名牌或組隊紅條）")
        if not miss:
            return out
        out += (f"；其餘 {miss} 張是照畫面正中央挖掉自己的。"
                "\n    角色本來就常常不在正中央，那些圖很可能把角色標成怪"
                "（學生會學得非常牢）。三個辦法：")
        out += ("\n      1. 用 PNG 重新蒐集：collect_dataset.py --format png"
                "（JPEG 會把紅條壓到碎成好幾塊，量不到）")
        out += "\n      2. 進遊戲自己跟自己組隊，角色頭上才會有那條紅條"
        out += ("\n      3. 把 outline_player_box 調大（例如 [220, 200]）"
                "，寧可多挖掉一塊也不要把角色標成怪")
        return out


def make_teacher(kind: str, cfg, templates_dir: str = "",
                 threshold: Optional[float] = None, single_class: bool = False,
                 class_name: str = "mob", black_level: Optional[int] = None):
    """依名稱建立老師。cfg 是 AppCfg（描邊老師要用到 regions 與 vision）。"""
    if kind == "outline":
        return OutlineTeacher(cfg, class_name=class_name, black_level=black_level)
    if kind == "template":
        if threshold is None:
            threshold = cfg.vision.mob_match_threshold
        return TemplateTeacher(templates_dir, threshold,
                               single_class=single_class, class_name=class_name)
    raise ValueError(f"未知的老師 {kind!r}，只能是 {' / '.join(TEACHERS)}")
