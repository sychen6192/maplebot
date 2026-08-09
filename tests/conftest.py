import os
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


@pytest.fixture(autouse=True)
def isolated_ui_templates(tmp_path, monkeypatch):
    """把 UI 模板目錄指到空的暫存資料夾。

    預設值指向 data/templates/ui，那是**使用者自己**截的模板（角色名牌、
    小地圖角落）。開發機上一旦有那些檔案，測試就會拿真人的模板去比對合成
    畫面，結果隨誰的機器而異。測試要跑在乾淨的環境裡。
    """
    monkeypatch.setattr("maplebot.config.UI_TEMPLATES_DIR", str(tmp_path / "ui"))


@pytest.fixture(scope="session")
def fixture_frame() -> np.ndarray:
    """MapleSaga 800x600 視窗實拍截圖：HP 100%、MP 100%、EXP 59.89%。"""
    path = os.path.join(FIXTURE_DIR, "mapleaga_800x600.jpg")
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    assert img is not None, f"讀不到測試截圖 {path}"
    return img
