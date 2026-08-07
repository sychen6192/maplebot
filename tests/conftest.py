import os
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


@pytest.fixture(scope="session")
def fixture_frame() -> np.ndarray:
    """MapleSaga 800x600 視窗實拍截圖：HP 100%、MP 100%、EXP 59.89%。"""
    path = os.path.join(FIXTURE_DIR, "mapleaga_800x600.jpg")
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    assert img is not None, f"讀不到測試截圖 {path}"
    return img
