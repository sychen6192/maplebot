"""用訓練好的模型在小地圖上找玩家點（選配）。

**為什麼值得為這麼小的東西訓練一個模型**：因為商業版就是這麼做的。拆開那份
客戶端之後，它自己的辨識資產只有兩顆神經網路，其中一顆（`CC\\yellow\\`）
專門用來在小地圖上找那顆 4x4 的黃點——fp16、約 3.0M 參數、6MB 權重，
完整的 YOLOv8/11n 級三頭偵測器（stride 8/16/32、單類別）。

一個要賣錢的實作，為一件看起來三行顏色門檻就能解決的事花這種成本，只有一個
解釋：**顏色門檻在這裡不夠用**。而那正是這個專案踩到的坑——自由市場的黃地板
會讓顏色偵測回報地板而不是角色。

介面刻意跟 `minimap.find_player_candidates` 一樣（回傳 `(x, y, score)` 清單），
所以上層的軌跡追蹤（`vision/track.py`）照常運作，換偵測器不用改 Perceiver。

訓練流程（全部沿用既有的管線，只是換個 region 與老師）：

    python tools/collect_dataset.py --region minimap --count 400 --interval 1
    python tools/autolabel.py --teacher minimap_dot
    python tools/prepare_dataset.py
    python tools/train_yolo.py --data datasets/yolo/dataset.yaml --imgsz 320
    python tools/export_onnx.py            # 掛機那台就不用裝 PyTorch

老師會**自動跳過顏色分不出來的畫面**（見 teachers.MinimapDotTeacher），
所以訓練資料裡不會混進「地板是玩家」這種標註。
"""
import os
from typing import List, Optional, Tuple

import numpy as np

Candidate = Tuple[int, int, float]

MODEL_SUFFIXES = (".pt", ".onnx", ".engine", ".torchscript")

# 小地圖只有 128x58 左右，用 YOLO 預設的 640 等於把它放大五倍去推理，
# 沒有更多資訊卻更慢。320 在實務上足夠涵蓋 stride 32 那一層的感受野。
DEFAULT_IMGSZ = 320


class MinimapDotDetector:
    def __init__(self, model_path: str, confidence: float = 0.4,
                 device: str = "", imgsz: Optional[int] = None):
        if not model_path:
            raise ValueError(
                "vision.minimap_detector=yolo 時必須設定 vision.minimap_model")
        if not model_path.lower().endswith(MODEL_SUFFIXES):
            raise ValueError(
                f"vision.minimap_model 認不得的副檔名：{model_path!r}。"
                f"支援 {'、'.join(MODEL_SUFFIXES)}")
        if not os.path.exists(model_path):
            raise ValueError(
                f"找不到模型 {model_path}——先訓練一個"
                "（見 maplebot/vision/minimap_yolo.py 開頭的流程），"
                "或把 vision.minimap_detector 改回 color")
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise ImportError("小地圖模型偵測需要先 pip install ultralytics") from e
        self.model = YOLO(model_path)
        self.confidence = confidence
        self.device = device or None
        self.imgsz = imgsz or DEFAULT_IMGSZ
        self.path = model_path

    def explain(self) -> str:
        kind = "ONNX" if self.path.lower().endswith(".onnx") else "PyTorch"
        return (f"小地圖模型 {kind} {self.path}｜信心門檻 {self.confidence}"
                f"｜推理尺寸 {self.imgsz}｜裝置 {self.device or 'auto'}")

    def candidates(self, minimap_bgr: np.ndarray) -> List[Candidate]:
        if minimap_bgr.size == 0:
            return []
        kwargs = {"conf": self.confidence, "imgsz": self.imgsz, "verbose": False}
        if self.device:
            kwargs["device"] = self.device
        out: List[Candidate] = []
        for r in self.model.predict(minimap_bgr, **kwargs):
            for box in r.boxes:
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
                out.append((int((x1 + x2) / 2), int((y1 + y2) / 2),
                            float(box.conf[0])))
        return out


class ColorDotDetector:
    """把既有的顏色偵測包成同一個介面，讓上層不必分辨用的是哪一種。"""

    def __init__(self, vision_cfg):
        from . import minimap
        self._minimap = minimap
        self.cfg = vision_cfg
        self.template = minimap.load_player_template(vision_cfg)

    def explain(self) -> str:
        return ("小地圖玩家點：顏色遮罩"
                + ("＋模板" if self.template is not None else "（沒有模板檔）"))

    def candidates(self, minimap_bgr: np.ndarray) -> List[Candidate]:
        return self._minimap.find_player_candidates(
            minimap_bgr, self.cfg, template=self.template)


def make_minimap_detector(vision_cfg):
    """依 vision.minimap_detector 建偵測器。照 vision/mobs.py:make_detector 的模式。"""
    if vision_cfg.minimap_detector == "yolo":
        return MinimapDotDetector(vision_cfg.minimap_model,
                                  confidence=vision_cfg.minimap_confidence,
                                  device=vision_cfg.yolo_device,
                                  imgsz=vision_cfg.minimap_imgsz or None)
    return ColorDotDetector(vision_cfg)
