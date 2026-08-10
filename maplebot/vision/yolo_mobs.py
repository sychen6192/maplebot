"""YOLO 怪物偵測（選配）。

需要 `pip install ultralytics`，並用自己蒐集的資料集訓練模型
（流程見 README「進階：ML 感知層」）。RTX 5090 上 YOLO11n/s
推理可達數百 FPS，適合當即時感知層。

`.pt` 與 `.onnx` 都收。ONNX 的意義是「不用扛 PyTorch」：ultralytics 走
onnxruntime 後端時，安裝體積從 2GB 級掉到幾十 MB，CPU 推理也普遍比
PyTorch eager 快——沒有顯卡、或不想在掛機的機器上裝 CUDA 的人走這條。
匯出用 tools/export_onnx.py。
"""
import os
from typing import List, Optional

import numpy as np

from .mobs import Mob

MODEL_SUFFIXES = (".pt", ".onnx", ".engine", ".torchscript")


def is_pretrained_name(model: str) -> bool:
    """像 yolo11n.pt 這種官方權重「名稱」（不是路徑），ultralytics 會自動下載。

    用來區分「使用者打錯路徑」與「刻意用官方預訓練權重」。
    """
    if not model or os.sep in model or "/" in model:
        return False
    lowered = model.lower()
    return lowered.startswith("yolo") and lowered.endswith(".pt")


class YoloMobDetector:
    def __init__(self, model_path: str, confidence: float = 0.5,
                 device: str = "", imgsz: Optional[int] = None):
        if not model_path:
            raise ValueError("vision.mob_detector=yolo 時必須設定 vision.yolo_model")
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise ImportError("YOLO 偵測需要先 pip install ultralytics") from e
        if (not model_path.lower().endswith(MODEL_SUFFIXES)
                and not is_pretrained_name(model_path)):
            raise ValueError(
                f"vision.yolo_model 認不得的副檔名：{model_path!r}。"
                f"支援 {'、'.join(MODEL_SUFFIXES)}")
        self.model = YOLO(model_path)
        self.confidence = confidence
        # 空字串交給 ultralytics 自己挑（有 CUDA 就用 CUDA）。
        # 指定 device 的用途是「這台有兩張卡」或「刻意壓在 CPU 上跑」
        self.device = device or None
        self.imgsz = imgsz
        self.path = model_path

    def explain(self) -> str:
        kind = "ONNX" if self.path.lower().endswith(".onnx") else "PyTorch"
        return (f"YOLO {kind} 模型 {self.path}"
                f"｜信心門檻 {self.confidence}"
                f"｜裝置 {self.device or 'auto'}")

    def detect(self, playfield_bgr: np.ndarray) -> List[Mob]:
        kwargs = {"conf": self.confidence, "verbose": False}
        if self.device:
            kwargs["device"] = self.device
        if self.imgsz:
            kwargs["imgsz"] = self.imgsz
        results = self.model.predict(playfield_bgr, **kwargs)
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
