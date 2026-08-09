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


def _scene_with_big_mobs(width, spots=(0.10, 0.28, 0.80), mob_w=44, mob_h=34):
    """同一個場景畫在 width 寬的畫面上：怪的相對大小與位置都一樣。

    上面的 _sized_scene 用的是最小的怪（只驗 min_area 那一側），大隻的怪
    才會踩到 max_area——面積門檻縮放錯誤就是在這裡漏掉的。
    怪都避開畫面正中央：那裡是角色自己，本來就會被挖掉。
    """
    s = width / 790
    h = int(520 * s)
    img = np.full((h, width, 3), 120, dtype=np.uint8)
    bw, bh = int(mob_w * s), int(mob_h * s)
    for frac in spots:
        x, y = int(width * frac), int(300 * s)
        # 實填：形態學閉合本來就是要把斷續描邊連成一整塊，所以真實的
        # 連通元件面積接近 sprite 的長x寬，會隨解析度平方成長
        cv2.rectangle(img, (x, y), (x + bw, y + bh), (0, 0, 0), -1)
    return img


def test_same_scene_is_detected_at_any_window_size():
    """同一組預設值，在 790 和 1900 寬的畫面上要抓到一樣多隻。

    面積門檻只按線性倍率縮放的話，大視窗裡的怪會超過 max_area 被整個丟掉——
    這就是「1920 視窗抓到的怪比想像中少」的原因。
    """
    det = OutlineMobDetector(black_level=8)
    small = len(det.detect(_scene_with_big_mobs(790)))
    big = len(det.detect(_scene_with_big_mobs(1900)))
    assert small == 3, small
    assert big == small, f"790px 抓到 {small} 隻，1900px 只抓到 {big} 隻"


def test_big_mobs_survive_a_big_window():
    """大隻的怪最容易踩到 max_area。"""
    det = OutlineMobDetector(black_level=8)
    assert len(det.detect(_scene_with_big_mobs(
        1900, spots=(0.10, 0.80), mob_w=110, mob_h=90))) == 2


def test_area_thresholds_scale_with_the_square():
    det = OutlineMobDetector(min_area=300, max_area=20000)
    min_a, max_a, kernel, box, _ = det._scaled(790 * 2)
    assert min_a == 300 * 4 and max_a == 20000 * 4      # 面積：平方
    assert kernel == det.close_kernel * 2               # 長度：線性
    assert box == (det.player_box[0] * 2, det.player_box[1] * 2)


def test_explain_says_why_blobs_were_dropped():
    det = OutlineMobDetector(black_level=8, min_area=100000)   # 門檻高到全滅
    det.detect(_scene_with_big_mobs(790))
    text = det.explain()
    assert "太小" in text
    assert "outline_min_area" in text          # 要講出該調哪個旋鈕


def test_explain_flags_an_empty_mask():
    det = OutlineMobDetector(black_level=0)
    det.detect(np.full((200, 400, 3), 120, dtype=np.uint8))
    assert "outline_black_level" in det.explain()


def test_explain_before_any_detection():
    assert OutlineMobDetector().explain() == "尚未偵測"
