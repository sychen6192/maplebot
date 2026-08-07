"""模板匹配怪物偵測：把模板貼進背景，驗證找得到、位置正確、NMS 去重。"""
import os

import cv2
import numpy as np
import pytest

from maplebot.vision.mobs import TemplateMobDetector, _nms


def _make_mob_sprite():
    rng = np.random.default_rng(42)
    sprite = rng.integers(0, 255, (24, 30, 3), dtype=np.uint8)
    cv2.circle(sprite, (15, 12), 9, (30, 200, 60), -1)
    cv2.rectangle(sprite, (5, 5), (25, 19), (200, 50, 180), 2)
    return sprite


@pytest.fixture
def detector(tmp_path):
    sprite = _make_mob_sprite()
    d = tmp_path / "mobs"
    d.mkdir()
    cv2.imwrite(str(d / "testmob_01.png"), sprite)
    return TemplateMobDetector(str(d), threshold=0.8), sprite


def test_detect_two_mobs(detector):
    det, sprite = detector
    rng = np.random.default_rng(7)
    field = rng.integers(0, 60, (300, 500, 3), dtype=np.uint8)
    field[100:124, 80:110] = sprite
    field[200:224, 400:430] = sprite
    mobs = det.detect(field)
    centers = sorted((m.cx, m.cy) for m in mobs)
    assert len(mobs) == 2
    assert centers[0] == (95, 112)
    assert centers[1] == (415, 212)


def test_detect_flipped_mob(detector):
    det, sprite = detector
    rng = np.random.default_rng(9)
    field = rng.integers(0, 60, (200, 300, 3), dtype=np.uint8)
    field[50:74, 120:150] = cv2.flip(sprite, 1)  # 面向另一邊的怪
    mobs = det.detect(field)
    assert len(mobs) == 1
    assert mobs[0].cx == 135


def test_empty_templates_dir(tmp_path):
    det = TemplateMobDetector(str(tmp_path), threshold=0.8)
    field = np.zeros((100, 100, 3), dtype=np.uint8)
    assert det.detect(field) == []


def test_nms_merges_overlaps():
    boxes = [(10, 10, 30, 24), (12, 11, 30, 24), (200, 50, 30, 24)]
    scores = [0.9, 0.85, 0.8]
    keep = _nms(boxes, scores)
    assert len(keep) == 2
    assert 0 in keep and 2 in keep
