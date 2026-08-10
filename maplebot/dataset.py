"""YOLO 資料集工具：自動預標註（bootstrap）與訓練集打包。

流程：collect_dataset 蒐集畫面 -> autolabel_dir 用「老師」偵測器產生
YOLO 格式預標註 -> （選配）labelImg 人工校對 -> prepare_dataset 切分
train/val 並產生 dataset.yaml -> ultralytics 訓練。

老師有描邊與模板兩種，定義在 teachers.py。

標籤格式與 labelImg 相容：每張圖同名 .txt（`cls cx cy w h`，
皆為 0~1 正規化值）加上一份 classes.txt。
"""
import glob
import os
import random
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import yaml

from .teachers import TemplateTeacher, class_from_template_name  # noqa: F401
from .vision.mobs import Mob

IMG_EXTS = (".jpg", ".jpeg", ".png")


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


def autolabel_dir(images_dir: str, templates_dir: str = "", threshold: float = 0.72,
                  single_class: bool = False, class_name: str = "mob",
                  progress=None, teacher=None) -> AutoLabelResult:
    """對資料夾內所有影像跑老師偵測器，寫出同名 .txt 與 classes.txt。

    teacher 是任何有 `.classes` 與 `.label(img) -> [(類別編號, Mob), ...]`
    的物件（見 teachers.py）。留 None 就沿用原本的模板匹配老師。

    沒偵測到怪的影像也會寫出空 .txt——校對時人工補框，
    留空則成為負樣本（背景），能有效壓低誤報。
    """
    if teacher is None:
        teacher = TemplateTeacher(templates_dir, threshold,
                                  single_class=single_class, class_name=class_name)

    res = AutoLabelResult(classes=list(teacher.classes))
    paths = list_images(images_dir)
    for index, path in enumerate(paths, 1):
        if progress:
            progress(index, len(paths), res.boxes)
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            continue
        res.images += 1
        h, w = img.shape[:2]
        lines = [yolo_line(cls_id, mob, w, h) for cls_id, mob in teacher.label(img)]
        with open(os.path.splitext(path)[0] + ".txt", "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        if lines:
            res.labeled += 1
            res.boxes += len(lines)
        else:
            res.unlabeled_files.append(os.path.basename(path))

    with open(os.path.join(images_dir, "classes.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(res.classes) + "\n")
    return res


def draw_labels(img, labeled, classes: Sequence[str] = ("mob",)):
    """把老師標出來的框畫在影像上，回傳新影像。

    自動標註最大的風險是「標錯了但沒人看」——錯的標註餵下去，學生會非常
    忠實地學會那個錯誤（例如把角色自己或小地圖當成怪）。所以先看再練。
    """
    out = img.copy()
    for cls_id, mob in labeled:
        x1, y1 = mob.cx - mob.w // 2, mob.cy - mob.h // 2
        cv2.rectangle(out, (x1, y1), (x1 + mob.w, y1 + mob.h), (0, 255, 255), 2)
        name = classes[cls_id] if cls_id < len(classes) else str(cls_id)
        cv2.putText(out, name, (x1, max(y1 - 4, 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    return out


def preview_labels(images_dir: str, teacher, out_dir: str,
                   count: int = 6, step: Optional[int] = None) -> List[str]:
    """在資料夾裡平均取樣 count 張，畫上老師的標註存到 out_dir。

    平均取樣而不是取前幾張：蒐集資料時前幾張多半是同一個點位的重複畫面，
    看了等於沒看。
    """
    paths = list_images(images_dir)
    if not paths or count <= 0:
        return []
    if step is None:
        step = max(len(paths) // count, 1)
    picked = paths[::step][:count]
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for path in picked:
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            continue
        dst = os.path.join(out_dir, os.path.basename(path))
        cv2.imwrite(dst, draw_labels(img, teacher.label(img), teacher.classes))
        written.append(dst)
    return written


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
