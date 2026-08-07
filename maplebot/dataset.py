"""YOLO 資料集工具：模板匹配自動預標註（bootstrap）與訓練集打包。

流程：collect_dataset 蒐集畫面 -> autolabel_dir 用模板匹配器產生
YOLO 格式預標註 -> labelImg 人工校對 -> prepare_dataset 切分
train/val 並產生 dataset.yaml -> ultralytics 訓練。

標籤格式與 labelImg 相容：每張圖同名 .txt（`cls cx cy w h`，
皆為 0~1 正規化值）加上一份 classes.txt。
"""
import glob
import os
import random
import re
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import cv2
import yaml

from .vision.mobs import Mob, TemplateMobDetector

IMG_EXTS = (".jpg", ".jpeg", ".png")


def class_from_template_name(name: str) -> str:
    """模板檔名 snail_01 -> 類別 snail。"""
    return re.sub(r"_\d+$", "", name)


def yolo_line(cls_id: int, mob: Mob, img_w: int, img_h: int) -> str:
    return (f"{cls_id} {mob.cx / img_w:.6f} {mob.cy / img_h:.6f} "
            f"{mob.w / img_w:.6f} {mob.h / img_h:.6f}")


def list_images(images_dir: str) -> List[str]:
    out: List[str] = []
    for ext in IMG_EXTS:
        out.extend(glob.glob(os.path.join(images_dir, f"*{ext}")))
    return sorted(out)


def write_yolo_labels(images_dir: str, labels_per_image: Dict[str, list],
                      classes: List[str]) -> None:
    """把 {影像路徑: [(cls_id, cx, cy, w, h), ...]} 寫成 YOLO 標籤 + classes.txt。

    座標是影像像素；會讀每張圖尺寸換算成 0~1。沒有框的影像寫空檔（背景負樣本）。
    供 label_gdino.py（GroundingDINO 老師）與其他自訂老師共用。
    """
    for path, boxes in labels_per_image.items():
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            continue
        h, w = img.shape[:2]
        lines = []
        for cls_id, cx, cy, bw, bh in boxes:
            lines.append(f"{int(cls_id)} {cx / w:.6f} {cy / h:.6f} "
                         f"{bw / w:.6f} {bh / h:.6f}")
        with open(os.path.splitext(path)[0] + ".txt", "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
    with open(os.path.join(images_dir, "classes.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(classes) + "\n")


@dataclass
class AutoLabelResult:
    images: int = 0
    labeled: int = 0          # 至少有一個框的影像數
    boxes: int = 0
    classes: List[str] = field(default_factory=list)
    unlabeled_files: List[str] = field(default_factory=list)


def autolabel_dir(images_dir: str, templates_dir: str, threshold: float,
                  single_class: bool = False, class_name: str = "mob",
                  progress=None) -> AutoLabelResult:
    """對資料夾內所有影像跑模板匹配，寫出同名 .txt 與 classes.txt。

    沒偵測到怪的影像也會寫出空 .txt——校對時人工補框，
    留空則成為負樣本（背景），能有效壓低誤報。
    """
    det = TemplateMobDetector(templates_dir, threshold)
    if not det.templates:
        raise ValueError(f"{templates_dir} 裡沒有任何模板 PNG，先用 tools/grab_template.py 蒐集")

    if single_class:
        classes = [class_name]
    else:
        classes = sorted({class_from_template_name(n) for n, _ in det.templates})
    cls_idx = {c: i for i, c in enumerate(classes)}

    res = AutoLabelResult(classes=classes)
    paths = list_images(images_dir)
    for index, path in enumerate(paths, 1):
        if progress:
            progress(index, len(paths), res.boxes)
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            continue
        res.images += 1
        h, w = img.shape[:2]
        lines = []
        for mob in det.detect(img):
            cls = class_name if single_class else class_from_template_name(mob.name)
            lines.append(yolo_line(cls_idx[cls], mob, w, h))
        with open(os.path.splitext(path)[0] + ".txt", "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        if lines:
            res.labeled += 1
            res.boxes += len(lines)
        else:
            res.unlabeled_files.append(os.path.basename(path))

    with open(os.path.join(images_dir, "classes.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(classes) + "\n")
    return res


@dataclass
class PrepareResult:
    train: int = 0
    val: int = 0
    negatives: int = 0        # 沒有任何框的背景影像
    classes: List[str] = field(default_factory=list)
    yaml_path: str = ""


def prepare_dataset(raw_dir: str, out_dir: str, val_fraction: float = 0.15,
                    seed: int = 42) -> PrepareResult:
    """把 raw_dir 的影像+標籤切成 train/val，輸出 ultralytics 資料集結構。"""
    classes_path = os.path.join(raw_dir, "classes.txt")
    if not os.path.exists(classes_path):
        raise FileNotFoundError(f"找不到 {classes_path}，請先跑 tools/autolabel.py")
    with open(classes_path, encoding="utf-8") as f:
        classes = [line.strip() for line in f if line.strip()]

    images = list_images(raw_dir)
    if len(images) < 4:
        raise ValueError(f"{raw_dir} 只有 {len(images)} 張影像，太少了（建議 300 張以上）")

    rng = random.Random(seed)
    rng.shuffle(images)
    n_val = max(1, round(len(images) * val_fraction))
    splits: List[Tuple[str, List[str]]] = [("val", images[:n_val]), ("train", images[n_val:])]

    res = PrepareResult(classes=classes)
    for split, paths in splits:
        img_dir = os.path.join(out_dir, "images", split)
        lbl_dir = os.path.join(out_dir, "labels", split)
        os.makedirs(img_dir, exist_ok=True)
        os.makedirs(lbl_dir, exist_ok=True)
        for img_path in paths:
            stem = os.path.splitext(os.path.basename(img_path))[0]
            shutil.copy2(img_path, os.path.join(img_dir, os.path.basename(img_path)))
            txt_src = os.path.splitext(img_path)[0] + ".txt"
            txt_dst = os.path.join(lbl_dir, stem + ".txt")
            if os.path.exists(txt_src):
                shutil.copy2(txt_src, txt_dst)
                with open(txt_src, encoding="utf-8") as f:
                    if not f.read().strip():
                        res.negatives += 1
            else:
                open(txt_dst, "w").close()   # 無標籤 = 背景負樣本
                res.negatives += 1
            if split == "train":
                res.train += 1
            else:
                res.val += 1

    data = {
        "path": os.path.abspath(out_dir),
        "train": "images/train",
        "val": "images/val",
        "names": {i: c for i, c in enumerate(classes)},
    }
    res.yaml_path = os.path.join(out_dir, "dataset.yaml")
    with open(res.yaml_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    return res


# 遊戲畫面特化的訓練參數：2D 橫向卷軸沒有旋轉/上下翻轉/透視變形，
# 關掉這些增強讓模型收斂更快也更準；左右翻轉保留（怪物會轉向）。
GAME_TRAIN_OVERRIDES: Dict[str, float] = {
    "degrees": 0.0,
    "shear": 0.0,
    "perspective": 0.0,
    "flipud": 0.0,
    "fliplr": 0.5,
}


def train(data_yaml: str, model: str = "yolo11n.pt", imgsz: int = 800,
          epochs: int = 80, batch: int = -1, device: str = "0",
          project: str = "runs/mobs", name: str = "mobs") -> str:
    """訓練並回傳最佳權重路徑。ultralytics 為選配相依，延後 import。"""
    try:
        from ultralytics import YOLO
    except ImportError as e:
        raise ImportError("訓練需要先安裝 ultralytics："
                          "pip install -r requirements-server.txt") from e
    net = YOLO(model)
    net.train(
        data=data_yaml,
        imgsz=imgsz,
        epochs=epochs,
        batch=batch,
        device=device,
        # 用絕對路徑，否則 ultralytics 會再套一層 runs/detect/ 進去
        project=os.path.abspath(project),
        name=name,
        patience=20,
        **GAME_TRAIN_OVERRIDES,
    )
    return str(net.trainer.best)
