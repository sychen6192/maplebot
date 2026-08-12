"""YOLO 資料集管線：自動預標註格式、train/val 切分、dataset.yaml。"""
import os

import cv2
import numpy as np
import pytest
import yaml

from maplebot.dataset import (autolabel_dir, class_from_template_name,
                              draw_labels, list_images, prepare_dataset,
                              preview_labels, write_yolo_labels)
from maplebot.vision.mobs import Mob


def _sprite():
    rng = np.random.default_rng(42)
    sprite = rng.integers(0, 255, (24, 30, 3), dtype=np.uint8)
    cv2.circle(sprite, (15, 12), 9, (30, 200, 60), -1)
    cv2.rectangle(sprite, (5, 5), (25, 19), (200, 50, 180), 2)
    return sprite


@pytest.fixture
def raw_dir(tmp_path):
    """6 張影像：4 張有怪（含一張兩隻）、2 張純背景。"""
    sprite = _sprite()
    raw = tmp_path / "raw"
    raw.mkdir()
    rng = np.random.default_rng(7)
    spots = [[(80, 100)], [(200, 50)], [(10, 10), (300, 200)], [(150, 150)], [], []]
    for i, positions in enumerate(spots):
        img = rng.integers(0, 60, (300, 500, 3), dtype=np.uint8)
        for (x, y) in positions:
            img[y:y + 24, x:x + 30] = sprite
        cv2.imwrite(str(raw / f"frame_{i:03d}.jpg"), img,
                    [cv2.IMWRITE_JPEG_QUALITY, 100])
    tpl = tmp_path / "templates"
    tpl.mkdir()
    cv2.imwrite(str(tpl / "snail_01.png"), sprite)
    return str(raw), str(tpl)


def test_class_name_strips_index():
    assert class_from_template_name("snail_01") == "snail"
    assert class_from_template_name("orange_mushroom_12") == "orange_mushroom"
    assert class_from_template_name("slime") == "slime"


def test_autolabel_writes_yolo_format(raw_dir):
    raw, tpl = raw_dir
    res = autolabel_dir(raw, tpl, threshold=0.8)

    assert res.images == 6
    assert res.labeled == 4
    assert res.boxes == 5
    assert res.classes == ["snail"]
    assert len(res.unlabeled_files) == 2

    with open(os.path.join(raw, "classes.txt")) as f:
        assert f.read().strip() == "snail"

    # 第一張：怪貼在 (80,100)，中心 (95,112)，影像 500x300
    with open(os.path.join(raw, "frame_000.txt")) as f:
        cls, cx, cy, w, h = f.read().split()
    assert cls == "0"
    assert float(cx) == pytest.approx(95 / 500, abs=0.01)
    assert float(cy) == pytest.approx(112 / 300, abs=0.01)
    assert float(w) == pytest.approx(30 / 500, abs=0.01)
    assert float(h) == pytest.approx(24 / 300, abs=0.01)

    # 背景影像也要有空標籤檔（負樣本）
    assert os.path.getsize(os.path.join(raw, "frame_004.txt")) == 0


def test_autolabel_reports_progress(raw_dir):
    raw, tpl = raw_dir
    seen = []
    autolabel_dir(raw, tpl, threshold=0.8,
                  progress=lambda i, total, boxes: seen.append((i, total)))
    assert seen[0] == (1, 6)
    assert seen[-1] == (6, 6)
    assert [i for i, _ in seen] == [1, 2, 3, 4, 5, 6]


def test_autolabel_single_class(raw_dir):
    raw, tpl = raw_dir
    res = autolabel_dir(raw, tpl, threshold=0.8, single_class=True)
    assert res.classes == ["mob"]


def test_prepare_dataset_split_and_yaml(raw_dir, tmp_path):
    raw, tpl = raw_dir
    autolabel_dir(raw, tpl, threshold=0.8)
    out = str(tmp_path / "yolo")

    res = prepare_dataset(raw, out, val_fraction=0.34, seed=1)

    assert res.train + res.val == 6
    assert res.val == 2
    assert res.negatives == 2
    for split, n in (("train", res.train), ("val", res.val)):
        imgs = os.listdir(os.path.join(out, "images", split))
        lbls = os.listdir(os.path.join(out, "labels", split))
        assert len(imgs) == n and len(lbls) == n

    with open(res.yaml_path) as f:
        data = yaml.safe_load(f)
    assert data["names"] == {0: "snail"}
    assert data["train"] == "images/train"
    assert os.path.isabs(data["path"])


def test_write_yolo_labels_normalizes_and_writes_classes(tmp_path):
    import cv2
    img = np.zeros((100, 200, 3), dtype=np.uint8)   # 200x100
    p = str(tmp_path / "a.jpg")
    cv2.imwrite(p, img)
    empty = str(tmp_path / "b.jpg")
    cv2.imwrite(empty, img)

    write_yolo_labels(str(tmp_path),
                      {p: [(0, 100, 50, 40, 20)], empty: []},
                      classes=["mob"])

    with open(os.path.splitext(p)[0] + ".txt") as f:
        cls, cx, cy, w, h = f.read().split()
    assert cls == "0"
    assert float(cx) == pytest.approx(0.5) and float(cy) == pytest.approx(0.5)
    assert float(w) == pytest.approx(0.2) and float(h) == pytest.approx(0.2)
    # 沒有框 -> 空檔（背景負樣本）
    assert os.path.getsize(os.path.splitext(empty)[0] + ".txt") == 0
    with open(os.path.join(str(tmp_path), "classes.txt")) as f:
        assert f.read().strip() == "mob"


def test_prepare_dataset_requires_classes(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    with pytest.raises(FileNotFoundError):
        prepare_dataset(str(raw), str(tmp_path / "out"))


# ---- 描邊自動標註（免模板的老師）----

@pytest.fixture
def outline_raw(tmp_path):
    """3 張影像：亮背景上放黑邊 sprite（描邊偵測認得的形狀）。

    UI 那塊固定畫在同一個位置——它就是「不排除的話會被學成怪」的東西。
    """
    raw = tmp_path / "oraw"
    raw.mkdir()
    for i, spots in enumerate([[(300, 300)], [(500, 400), (700, 300)], []]):
        img = np.full((520, 790, 3), 150, dtype=np.uint8)
        for (x, y) in spots:
            cv2.rectangle(img, (x, y), (x + 34, y + 30), (0, 0, 0), 3)   # 黑描邊
            cv2.rectangle(img, (x + 3, y + 3), (x + 31, y + 27), (90, 180, 90), -1)
        cv2.rectangle(img, (10, 10), (44, 40), (0, 0, 0), 3)             # 固定 UI
        cv2.imwrite(str(raw / f"f{i}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 100])
    return str(raw)


def test_outline_autolabel_writes_labels(outline_raw):
    from maplebot.dataset import autolabel_outline_dir
    res = autolabel_outline_dir(outline_raw, black_level=40, min_area=100,
                                min_size=(10, 10), ui_dir="does/not/exist")
    assert res.images == 3
    assert res.boxes >= 3                    # 至少把三隻怪標到
    assert res.classes == ["mob"]
    with open(os.path.join(outline_raw, "classes.txt"), encoding="utf-8") as f:
        assert f.read().strip() == "mob"


def test_outline_autolabel_excludes_ui_regions(outline_raw):
    """UI 每幀都在同一個位置，不排除就會被學成一隻怪。"""
    from maplebot.dataset import autolabel_outline_dir
    before = autolabel_outline_dir(outline_raw, black_level=40, min_area=100,
                                   min_size=(10, 10), ui_dir="does/not/exist")
    after = autolabel_outline_dir(outline_raw, black_level=40, min_area=100,
                                  min_size=(10, 10), ui_dir="does/not/exist",
                                  exclude=[(0, 0, 60, 60)])
    assert after.boxes == before.boxes - 3   # 三張圖各少掉那顆 UI


def test_outline_autolabel_keeps_empty_frames_as_negatives(outline_raw):
    """沒有怪的畫面要寫出空 .txt——那是壓低誤報用的背景負樣本。"""
    from maplebot.dataset import autolabel_outline_dir
    autolabel_outline_dir(outline_raw, black_level=40, min_area=100,
                          min_size=(10, 10), ui_dir="does/not/exist",
                          exclude=[(0, 0, 60, 60)])
    assert os.path.exists(os.path.join(outline_raw, "f2.txt"))
    with open(os.path.join(outline_raw, "f2.txt"), encoding="utf-8") as f:
        assert f.read().strip() == ""


def test_outline_autolabel_boxes_are_normalized(outline_raw):
    from maplebot.dataset import autolabel_outline_dir
    autolabel_outline_dir(outline_raw, black_level=40, min_area=100,
                          min_size=(10, 10), ui_dir="does/not/exist")
    with open(os.path.join(outline_raw, "f0.txt"), encoding="utf-8") as f:
        for line in f:
            cls, *vals = line.split()
            assert cls == "0"
            assert all(0.0 <= float(v) <= 1.0 for v in vals)


def test_outline_autolabel_unions_template_teacher(outline_raw, tmp_path):
    """兩個老師的盲點互補，聯集要比單一個抓得多（重疊的不重複計）。"""
    from maplebot.dataset import autolabel_outline_dir
    # 只有模板認得的怪：沒有黑描邊，描邊老師一定看不到
    tpl = tmp_path / "tpl"
    tpl.mkdir()
    sprite = np.full((26, 26, 3), 200, dtype=np.uint8)
    cv2.circle(sprite, (13, 13), 9, (40, 90, 220), -1)
    cv2.imwrite(str(tpl / "blob_01.png"), sprite)
    for name in sorted(os.listdir(outline_raw)):
        if name.endswith(".jpg"):
            p = os.path.join(outline_raw, name)
            img = cv2.imread(p)
            img[60:86, 600:626] = sprite
            cv2.imwrite(p, img, [cv2.IMWRITE_JPEG_QUALITY, 100])

    only_outline = autolabel_outline_dir(
        outline_raw, black_level=40, min_area=100, min_size=(10, 10),
        ui_dir="none", exclude=[(0, 0, 60, 60)])
    both = autolabel_outline_dir(
        outline_raw, black_level=40, min_area=100, min_size=(10, 10),
        ui_dir="none", exclude=[(0, 0, 60, 60)],
        templates_dir=str(tpl), template_threshold=0.6)
    assert both.boxes > only_outline.boxes


def test_union_does_not_double_count_the_same_mob(outline_raw, tmp_path):
    """同一隻怪被兩個老師都抓到時只能算一次，否則標註會出現重疊的重複框。"""
    from maplebot.dataset import autolabel_outline_dir
    tpl = tmp_path / "tpl2"
    tpl.mkdir()
    img = cv2.imread(os.path.join(outline_raw, "f0.jpg"))
    cv2.imwrite(str(tpl / "same_01.png"), img[300:330, 300:334])   # 就是那隻怪

    outline_only = autolabel_outline_dir(
        outline_raw, black_level=40, min_area=100, min_size=(10, 10),
        ui_dir="none", exclude=[(0, 0, 60, 60)])
    with_tpl = autolabel_outline_dir(
        outline_raw, black_level=40, min_area=100, min_size=(10, 10),
        ui_dir="none", exclude=[(0, 0, 60, 60)],
        templates_dir=str(tpl), template_threshold=0.7)
    # f0 那張只有一隻怪，兩個老師都抓到也只該有一個框
    with open(os.path.join(outline_raw, "f0.txt"), encoding="utf-8") as f:
        assert len([ln for ln in f if ln.strip()]) == 1
    assert with_tpl.boxes >= outline_only.boxes


# ---- 老師介面（autolabel_dir / preview_labels）----

class _FakeTeacher:
    """只認左半邊的假老師，用來驗證 autolabel_dir 真的走 teacher 這條路。"""

    classes = ["blob", "thing"]

    def label(self, img):
        h, w = img.shape[:2]
        return [(1, Mob(cx=w // 4, cy=h // 2, w=20, h=10, score=1.0, name="x"))]

    def reset(self):
        pass


def test_autolabel_uses_the_given_teacher_instead_of_templates(raw_dir):
    """描邊老師走的就是這條路——完全沒有 templates_dir。"""
    raw, _ = raw_dir

    res = autolabel_dir(raw, teacher=_FakeTeacher())

    assert res.classes == ["blob", "thing"]
    assert res.images == 6 and res.labeled == 6 and res.boxes == 6
    with open(os.path.join(raw, "classes.txt")) as f:
        assert f.read().split() == ["blob", "thing"]
    # 影像 500x300，假老師固定標在左四分之一、垂直中央
    with open(os.path.join(raw, "frame_000.txt")) as f:
        cls, cx, cy, w, h = f.read().split()
    assert cls == "1"
    assert float(cx) == pytest.approx(125 / 500, abs=0.01)
    assert float(cy) == pytest.approx(0.5, abs=0.01)


def test_draw_labels_marks_the_boxes_without_touching_the_original():
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    mob = Mob(cx=100, cy=50, w=40, h=20, score=1.0, name="mob")

    out = draw_labels(img, [(0, mob)], classes=["mob"])

    assert out is not img and (img == 0).all()
    assert out[40, 80].tolist() == [0, 255, 255]      # 框的左上角
    assert out[50, 100].tolist() == [0, 0, 0]         # 框內沒被塗掉


def test_preview_samples_across_the_folder_and_stays_out_of_the_dataset(raw_dir):
    """預覽圖若跟訓練圖混在一起，下一輪就會把畫著黃框的圖也拿去練。"""
    raw, _ = raw_dir
    out_dir = os.path.join(raw, "_preview")

    written = preview_labels(raw, _FakeTeacher(), out_dir, count=3)

    assert len(written) == 3
    # 平均取樣：6 張取 3 張 -> 第 0、2、4 張，不是前三張
    assert [os.path.basename(p) for p in written] == [
        "frame_000.jpg", "frame_002.jpg", "frame_004.jpg"]
    assert all(os.path.exists(p) for p in written)
    # 訓練集的檔案清單完全沒變（預覽在子資料夾，list_images 不遞迴）
    assert sorted(os.path.basename(p) for p in list_images(raw)) == \
        [f"frame_{i:03d}.jpg" for i in range(6)]


def test_preview_of_an_empty_folder_is_not_an_error(tmp_path):
    empty = tmp_path / "none"
    empty.mkdir()
    assert preview_labels(str(empty), _FakeTeacher(),
                          str(tmp_path / "out"), count=3) == []
