"""YOLO 資料集管線：自動預標註格式、train/val 切分、dataset.yaml。"""
import os

import cv2
import numpy as np
import pytest
import yaml

from maplebot.dataset import (autolabel_dir, class_from_template_name,
                              prepare_dataset)


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


def test_prepare_dataset_requires_classes(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    with pytest.raises(FileNotFoundError):
        prepare_dataset(str(raw), str(tmp_path / "out"))
