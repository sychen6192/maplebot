"""小地圖玩家點的偵測器選擇：顏色 vs 訓練好的模型。

介面刻意跟 minimap.find_player_candidates 一樣（回傳候選清單），所以上層的
軌跡追蹤不用知道底下換了什麼——這跟 vision/mobs.py:make_detector 是同一個模式。
"""
import numpy as np
import pytest

from maplebot.config import AppCfg, ConfigError, VisionCfg
from maplebot.vision.minimap_yolo import (ColorDotDetector,
                                          make_minimap_detector)


def _mm(w=128, h=58):
    mm = np.full((h, w, 3), (60, 55, 50), dtype=np.uint8)
    mm[18:23, 28:33] = (40, 40, 40)
    mm[19:22, 29:32] = (0, 255, 255)
    return mm


def test_colour_is_the_default():
    det = make_minimap_detector(VisionCfg())
    assert isinstance(det, ColorDotDetector)


def test_colour_detector_returns_candidates():
    det = make_minimap_detector(VisionCfg())
    cands = det.candidates(_mm())
    assert len(cands) == 1
    x, y, score = cands[0]
    assert abs(x - 30) <= 1 and abs(y - 20) <= 1
    assert score > 0


def test_empty_minimap_gives_no_candidates():
    det = make_minimap_detector(VisionCfg())
    assert det.candidates(np.zeros((0, 0, 3), dtype=np.uint8)) == []


def test_explain_says_whether_a_template_is_in_use():
    assert "沒有模板檔" in make_minimap_detector(VisionCfg()).explain()


def test_yolo_without_a_model_path_is_rejected_at_config_time():
    """設定錯要在載入設定時就炸掉，不是等到掛機半小時後才發現。"""
    from maplebot.config import load_config
    import tempfile
    import os
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "c.yaml")
        with open(p, "w", encoding="utf-8") as f:
            f.write("regions:\n  minimap: [0,0,10,10]\n"
                    "  hp_bar: [0,0,10,10]\n  mp_bar: [0,0,10,10]\n"
                    "  playfield: [0,0,10,10]\n"
                    "vision:\n  minimap_detector: yolo\n")
        with pytest.raises(ConfigError, match="minimap_model"):
            load_config(p, local_path="/nonexistent")


def test_an_unknown_detector_name_is_rejected():
    from maplebot.config import load_config
    import tempfile
    import os
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "c.yaml")
        with open(p, "w", encoding="utf-8") as f:
            f.write("vision:\n  minimap_detector: magic\n")
        with pytest.raises(ConfigError, match="color / yolo"):
            load_config(p, local_path="/nonexistent")


def test_a_missing_model_file_says_what_to_do():
    """模型路徑寫錯時的訊息要講出下一步，不是只說 not found。"""
    from maplebot.vision.minimap_yolo import MinimapDotDetector
    with pytest.raises(ValueError, match="minimap_detector 改回 color"):
        MinimapDotDetector("no/such/model.onnx")


def test_a_bad_suffix_is_rejected_before_loading():
    from maplebot.vision.minimap_yolo import MinimapDotDetector
    with pytest.raises(ValueError, match="副檔名"):
        MinimapDotDetector("model.weights")


def test_the_perceiver_uses_the_selected_detector():
    """換偵測器不該讓 Perceiver 改一行。"""
    from maplebot.perception import Perceiver

    class _Fake:
        def candidates(self, mm):
            return [(7, 9, 1.0)]

        def explain(self):
            return "fake"

    cfg = AppCfg()
    cfg.regions = {"minimap": (0, 0, 128, 58), "hp_bar": (0, 60, 10, 4),
                   "mp_bar": (20, 60, 10, 4), "playfield": (0, 70, 100, 60)}
    frame = np.zeros((140, 130, 3), dtype=np.uint8)
    frame[0:58, 0:128] = _mm()

    class _Blind:
        def detect(self, img):
            return []

    p = Perceiver(cfg, _Blind())
    p.minimap_detector = _Fake()
    assert p.perceive(frame, 0.0).minimap_xy == (7, 9)
