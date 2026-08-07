"""區分「官方預訓練權重名稱」與「本機路徑」。

serve_yolo 在載入 ultralytics 前會擋掉不存在的權重路徑，但官方名稱
（yolo11n.pt 等）沒有本機檔案是正常的——會由 ultralytics 自動下載。
"""
import os

import pytest

from maplebot.vision.yolo_mobs import is_pretrained_name


@pytest.mark.parametrize("name", [
    "yolo11n.pt", "yolo11s.pt", "yolo11m.pt", "yolov8n.pt", "YOLO11N.PT",
])
def test_official_names_are_downloadable(name):
    assert is_pretrained_name(name) is True


@pytest.mark.parametrize("name", [
    "runs/mobs/mobs/weights/best.pt",          # 相對路徑
    "/home/u/runs/best.pt",                    # 絕對路徑
    "best.pt",                                 # 自訓權重的裸檔名
    "yolo11n.onnx",                            # 非 .pt
    "",
])
def test_paths_and_others_are_not(name):
    assert is_pretrained_name(name) is False


def test_windows_path_not_treated_as_name():
    assert is_pretrained_name(f"weights{os.sep}yolo11n.pt") is False
