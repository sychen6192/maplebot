"""怪物偵測。

預設用 OpenCV 模板匹配（TemplateMobDetector）：把怪物截圖丟進
data/templates/mobs/ 即可，會自動加上左右翻轉版本。
進階可換成 YOLO（vision/yolo_mobs.py），介面相同。
"""
import glob
import os
from dataclasses import dataclass
from typing import List, Protocol, Tuple

import cv2
import numpy as np


@dataclass
class Mob:
    cx: int
    cy: int
    w: int
    h: int
    score: float
    name: str


class MobDetector(Protocol):
    def detect(self, playfield_bgr: np.ndarray) -> List[Mob]: ...


def _nms(boxes: List[Tuple[int, int, int, int]], scores: List[float], iou_thr: float = 0.35):
    if not boxes:
        return []
    arr = np.array(boxes, dtype=np.float32)
    sc = np.array(scores, dtype=np.float32)
    x1, y1 = arr[:, 0], arr[:, 1]
    x2, y2 = arr[:, 0] + arr[:, 2], arr[:, 1] + arr[:, 3]
    areas = arr[:, 2] * arr[:, 3]
    order = sc.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
        order = order[1:][iou <= iou_thr]
    return keep


class TemplateMobDetector:
    MAX_RESULTS = 30

    def __init__(self, templates_dir: str, threshold: float = 0.72):
        self.threshold = threshold
        self.templates: List[Tuple[str, np.ndarray]] = []
        pattern = os.path.join(templates_dir, "**", "*.png")
        for path in sorted(glob.glob(pattern, recursive=True)):
            img = cv2.imread(path, cv2.IMREAD_COLOR)
            if img is None:
                continue
            name = os.path.splitext(os.path.basename(path))[0]
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            self.templates.append((name, gray))
            self.templates.append((name, cv2.flip(gray, 1)))  # 怪物會左右轉向

    def detect(self, playfield_bgr: np.ndarray) -> List[Mob]:
        if not self.templates:
            return []
        gray = cv2.cvtColor(playfield_bgr, cv2.COLOR_BGR2GRAY)
        boxes: List[Tuple[int, int, int, int]] = []
        scores: List[float] = []
        names: List[str] = []
        for name, tpl in self.templates:
            th, tw = tpl.shape
            if gray.shape[0] < th or gray.shape[1] < tw:
                continue
            res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(res >= self.threshold)
            for x, y in zip(xs, ys):
                boxes.append((int(x), int(y), tw, th))
                scores.append(float(res[y, x]))
                names.append(name)
        keep = _nms(boxes, scores)[: self.MAX_RESULTS]
        return [
            Mob(
                cx=boxes[i][0] + boxes[i][2] // 2,
                cy=boxes[i][1] + boxes[i][3] // 2,
                w=boxes[i][2],
                h=boxes[i][3],
                score=scores[i],
                name=names[i],
            )
            for i in keep
        ]


def make_detector(vision_cfg, templates_dir: str, logger=None) -> MobDetector:
    if vision_cfg.mob_detector == "outline":
        from .outline_mobs import OutlineMobDetector

        return OutlineMobDetector(
            black_level=vision_cfg.outline_black_level,
            min_area=vision_cfg.outline_min_area,
            max_area=vision_cfg.outline_max_area,
            close_kernel=vision_cfg.outline_close_kernel,
            player_box=vision_cfg.outline_player_box,
        )
    if vision_cfg.mob_detector == "yolo":
        from .yolo_mobs import YoloMobDetector

        return YoloMobDetector(vision_cfg.yolo_model, vision_cfg.yolo_confidence)
    if vision_cfg.mob_detector == "remote":
        from .remote_mobs import RemoteMobDetector

        return RemoteMobDetector(
            vision_cfg.remote_endpoint,
            confidence=vision_cfg.yolo_confidence,
            timeout=vision_cfg.remote_timeout,
            jpeg_quality=vision_cfg.remote_jpeg_quality,
            max_width=vision_cfg.remote_max_width,
            logger=logger,
        )
    return TemplateMobDetector(templates_dir, vision_cfg.mob_match_threshold)
