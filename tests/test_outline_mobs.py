"""零設定描邊偵測：合成帶黑色描邊的 sprite 驗證。"""
import cv2
import numpy as np
import pytest

from maplebot.vision.outline_mobs import OutlineMobDetector

W, H = 400, 300


def _scene():
    """背景是不含純黑的地形色。"""
    rng = np.random.default_rng(5)
    img = rng.integers(60, 200, (H, W, 3), dtype=np.uint8)
    return img


def _put_sprite(img, cx, cy, w=34, h=30):
    """畫一個有黑色描邊的 sprite。"""
    x1, y1 = cx - w // 2, cy - h // 2
    cv2.rectangle(img, (x1, y1), (x1 + w, y1 + h), (0, 0, 0), 2)      # 黑描邊
    cv2.rectangle(img, (x1 + 3, y1 + 3), (x1 + w - 3, y1 + h - 3),
                  (90, 180, 220), -1)                                  # 內部填色
    return img


@pytest.fixture
def det():
    return OutlineMobDetector(black_level=8, min_area=300, close_kernel=15,
                              player_box=(60, 80))


def test_finds_two_sprites(det):
    img = _scene()
    _put_sprite(img, 80, 90)
    _put_sprite(img, 320, 210)
    mobs = det.detect(img)
    centers = sorted((m.cx, m.cy) for m in mobs)
    assert len(mobs) == 2
    assert abs(centers[0][0] - 80) <= 4 and abs(centers[0][1] - 90) <= 4
    assert abs(centers[1][0] - 320) <= 4 and abs(centers[1][1] - 210) <= 4


def test_ignores_player_at_center(det):
    """玩家自己永遠在畫面中央，不能被當成怪。"""
    img = _scene()
    _put_sprite(img, W // 2, H // 2)          # 就是玩家
    assert det.detect(img) == []


def test_plain_background_finds_nothing(det):
    assert det.detect(_scene()) == []


def test_area_bounds_filter_noise_and_backdrop(det):
    img = _scene()
    cv2.rectangle(img, (10, 10), (16, 16), (0, 0, 0), -1)      # 太小的雜訊
    cv2.rectangle(img, (200, 20), (390, 290), (0, 0, 0), -1)   # 太大的背景黑塊
    small_det = OutlineMobDetector(black_level=8, min_area=300, max_area=8000,
                                   close_kernel=15, player_box=(60, 80))
    assert small_det.detect(img) == []


def test_black_level_tolerance_for_compressed_images():
    """JPEG 會把純黑壓成接近黑，嚴格比對就抓不到。"""
    img = _scene()
    _put_sprite(img, 100, 100)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    jpeg = cv2.imdecode(buf, cv2.IMREAD_COLOR)

    strict = OutlineMobDetector(black_level=0, min_area=300, close_kernel=15,
                                player_box=(60, 80))
    tolerant = OutlineMobDetector(black_level=20, min_area=300, close_kernel=15,
                                  player_box=(60, 80))
    assert len(strict.detect(jpeg)) == 0
    assert len(tolerant.detect(jpeg)) == 1


def test_empty_frame():
    assert OutlineMobDetector().detect(np.zeros((0, 0, 3), dtype=np.uint8)) == []
