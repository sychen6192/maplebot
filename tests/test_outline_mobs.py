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


def _sized_scene(w, h, scale):
    """同一個場景在不同視窗大小下：怪的像素尺寸等比例放大。"""
    rng = np.random.default_rng(5)
    img = rng.integers(60, 200, (h, w, 3), dtype=np.uint8)
    mw, mh = int(20 * scale), int(18 * scale)      # 用最小的怪測，最容易漏
    for cx, cy in [(int(w * 0.2), int(h * 0.3)), (int(w * 0.8), int(h * 0.7))]:
        x1, y1 = cx - mw // 2, cy - mh // 2
        cv2.rectangle(img, (x1, y1), (x1 + mw, y1 + mh), (0, 0, 0),
                      max(2, int(2 * scale)))
        cv2.rectangle(img, (x1 + 3, y1 + 3), (x1 + mw - 3, y1 + mh - 3),
                      (90, 180, 220), -1)
    return img


@pytest.mark.parametrize("w,h,scale", [
    (790, 520, 1.0),        # 800x600 視窗
    (1280, 720, 1.6),
    (2554, 1430, 3.2),      # 大視窗
])
def test_same_defaults_work_at_any_resolution(w, h, scale):
    """描邊團塊面積跟解析度相關，門檻要跟著縮放，否則同一組設定只在
    某一種視窗大小下有效。"""
    assert len(OutlineMobDetector().detect(_sized_scene(w, h, scale))) == 2


def test_without_auto_scale_defaults_miss_small_window():
    """關掉縮放時，為大畫面調的門檻會把小畫面的怪全部當雜訊丟掉。"""
    det = OutlineMobDetector(auto_scale=False, min_area=800)
    assert det.detect(_sized_scene(790, 520, 1.0)) == []
    assert len(det.detect(_sized_scene(2554, 1430, 3.2))) == 2


def test_scaled_thresholds_grow_with_width():
    det = OutlineMobDetector(min_area=300, max_area=20000, close_kernel=20)
    min_a, max_a, kernel, player, min_sz = det._scaled(2554)
    assert min_a > 300 and max_a > 20000
    assert kernel > 20 and player[0] > 100 and min_sz[0] > 18
    assert det._scaled(790)[0] == 300          # 參考寬度時不變


def test_empty_frame():
    assert OutlineMobDetector().detect(np.zeros((0, 0, 3), dtype=np.uint8)) == []
