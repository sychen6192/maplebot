"""YOLO 怪物偵測（選配）。

需要 `pip install ultralytics`，並用自己蒐集的資料集訓練模型
（流程見 README「進階：ML 感知層」）。RTX 5090 上 YOLO11n/s
推理可達數百 FPS，適合當即時感知層。
"""
from typing import List

import numpy as np

from .mobs import Mob


class YoloMobDetector:
    def __init__(self, model_path: str, confidence: float = 0.5):
        if not model_path:
            raise ValueError("vision.mob_detector=yolo 時必須設定 vision.yolo_model")
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise ImportError("YOLO 偵測需要先 pip install ultralytics") from e
        self.model = YOLO(model_path)
        self.confidence = confidence

    def detect(self, playfield_bgr: np.ndarray) -> List[Mob]:
        results = self.model.predict(playfield_bgr, conf=self.confidence, verbose=False)
        mobs: List[Mob] = []
        for r in results:
            names = r.names
            for box in r.boxes:
                x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
                mobs.append(
                    Mob(
                        cx=(x1 + x2) // 2,
                        cy=(y1 + y2) // 2,
                        w=x2 - x1,
                        h=y2 - y1,
                        score=float(box.conf[0]),
                        name=str(names.get(int(box.cls[0]), int(box.cls[0]))),
                    )
                )
        return mobs
