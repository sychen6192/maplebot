"""擷取層：空白畫面判定（決定 auto 模式退不退回螢幕擷取）與 ROI 裁切。"""
import numpy as np
import pytest

from maplebot.capture import CaptureError, ImageCapture, _crop, looks_blank


def test_black_frame_is_blank():
    assert looks_blank(np.zeros((200, 300, 3), dtype=np.uint8)) is True


def test_none_is_blank():
    assert looks_blank(None) is True


def test_game_frame_is_not_blank():
    rng = np.random.default_rng(4)
    assert looks_blank(rng.integers(20, 200, (200, 300, 3), dtype=np.uint8)) is False


def test_mostly_black_with_small_hud_still_blank():
    frame = np.zeros((200, 300, 3), dtype=np.uint8)
    frame[:2, :] = 200          # 1% 的亮部
    assert looks_blank(frame) is True


def test_crop_region():
    frame = np.arange(100 * 200 * 3, dtype=np.uint8).reshape(100, 200, 3)
    out = _crop(frame, (10, 20, 30, 40))
    assert out.shape == (40, 30, 3)
    assert np.array_equal(out, frame[20:60, 10:40])


def test_crop_out_of_bounds_raises():
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    with pytest.raises(CaptureError):
        _crop(frame, (150, 0, 100, 10))


def test_image_capture_full_and_region(tmp_path):
    import cv2
    rng = np.random.default_rng(2)
    img = rng.integers(0, 255, (80, 120, 3), dtype=np.uint8)
    path = str(tmp_path / "shot.png")
    cv2.imwrite(path, img)

    cap = ImageCapture(path)
    assert cap.size == (120, 80)
    assert cap.grab().shape == (80, 120, 3)
    assert cap.grab((10, 5, 20, 15)).shape == (15, 20, 3)
