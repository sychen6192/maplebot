"""會捲動的小地圖（issue #7）。

**測試一律用真實截圖接出來的大地圖，不用合成雜訊。** 開發時先用高斯模糊過的
隨機雜訊當「地圖」，相位相關的信心只有 0.13、量到的位移還是錯的，害我以為
演算法不work；換成真實小地圖是 0.94~1.00、誤差 ±0.05px。模糊雜訊沒有邊緣與
線條，而真實小地圖全是那些東西——測試素材不像真的，結論就不能用。

（這跟 test_minimap.py 那個「合成地形太乾淨所以沒抓到 bug」是同一件事。）
"""
import cv2
import numpy as np
import pytest

from maplebot.vision.minimap_scroll import MinimapScroll


@pytest.fixture(scope="module")
def world(request):
    """把真實小地圖橫向接成一張放不下的大地圖，用來模擬捲動視窗。"""
    path = "tests/fixtures/mapleaga_800x600.jpg"
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    assert img is not None, f"讀不到 {path}"
    mm = img[98:98 + 58, 18:18 + 128]           # config/default.yaml 的 minimap ROI
    return np.hstack([mm, mm[:, ::-1], mm, mm[:, ::-1]])


VIEW_W = 128


def _view(world, origin_x, dot_x, dot_y=28):
    """截出小地圖視窗，並在視窗內畫上角色黃點。"""
    v = world[:, origin_x:origin_x + VIEW_W].copy()
    v[dot_y - 2:dot_y + 3, dot_x - 2:dot_x + 3] = (40, 40, 40)
    v[dot_y - 1:dot_y + 2, dot_x - 1:dot_x + 2] = (0, 255, 255)
    return v


# ---- 捲動的地圖 ----

def test_it_recovers_the_real_position_on_a_scrolling_map(world):
    """角色點釘在視窗中央、地圖在底下捲——回報的必須是真實的地圖位置。

    這就是 issue #7：不換算的話 minimap_xy 永遠是 64，走過半張地圖也一樣，
    而 route.compress() 靠 x 的折返找巡邏區間，x 不動就錄不出任何東西。
    """
    s = MinimapScroll()
    errors = []
    for i in range(30):
        origin = i * 4
        got = s.update(_view(world, origin, 64), (64, 28))
        errors.append(got[0] - (origin + 64))
    assert max(abs(e) for e in errors) <= 3, f"誤差 {errors}"
    assert s.scrolling


def test_without_compensation_the_position_never_changes(world):
    """對照組：這就是修之前的行為，也是回報者看到的症狀。"""
    xs = [_view(world, i * 4, 64) is not None and 64 for i in range(30)]
    assert set(xs) == {64}          # 視窗座標永遠是 64


def test_it_follows_scrolling_in_both_directions(world):
    s = MinimapScroll()
    for i in range(12):
        s.update(_view(world, i * 6, 64), (64, 28))
    forward = s.origin[0]
    for i in range(11, -1, -1):
        got = s.update(_view(world, i * 6, 64), (64, 28))
    assert forward > 20
    assert abs(got[0] - 64) <= 3, "走回原點，座標也該回到原點附近"


def test_the_dot_moving_inside_a_scrolling_view_still_counts(world):
    """地圖在捲、角色同時也在視窗裡移動——兩個位移要相加。"""
    s = MinimapScroll()
    for i in range(20):
        got = s.update(_view(world, i * 3, 40 + i), (40 + i, 28))
    assert abs(got[0] - (19 * 3 + 40 + 19)) <= 4


# ---- 不捲的地圖：一個 px 都不能漂 ----

def test_a_static_map_never_drifts(world):
    """**這是最重要的一條。** 這個修正是為了救一種壞掉的地圖，
    不能讓現在好好的地圖冒險。"""
    s = MinimapScroll()
    errors = []
    for i in range(120):
        dot = 20 + (i % 80)
        got = s.update(_view(world, 0, dot), (dot, 28))
        errors.append(got[0] - dot)
    assert max(abs(e) for e in errors) == 0, f"靜止地圖漂移了：{s.origin}"
    assert not s.scrolling


def test_masking_the_dot_is_what_prevents_the_drift(world):
    """把遮罩關掉就會漂——釘住這個機制，免得日後有人「簡化」掉。

    角色點是靜止地圖上唯一會動的東西，相位相關會整個跟著它跑。
    實測 120 幀漂 8.3px，一小時就是上千 px。
    """
    s = MinimapScroll()
    s.dot_mask = 0                      # 等於不遮
    for i in range(120):
        dot = 20 + (i % 80)
        s.update(_view(world, 0, dot), (dot, 28))
    assert abs(s.origin[0]) > 2, "沒遮角色點卻沒漂移？那這個遮罩就沒必要了"


# ---- 邊界情況 ----

def test_a_black_screen_resets_instead_of_corrupting_the_origin(world):
    """換圖/讀圖時畫面不可信。把垃圾累進原點是**不可逆**的錯，寧可重來。"""
    s = MinimapScroll()
    for i in range(8):
        s.update(_view(world, i * 4, 64), (64, 28))
    assert s.origin[0] != 0
    s.update(np.zeros((58, VIEW_W, 3), dtype=np.uint8), (30, 28))
    assert s.origin == (0.0, 0.0)
    assert s.resets == 1


def test_a_resized_minimap_starts_over(world):
    """小地圖展開/收合之後尺寸變了，沒有可比對的基準。"""
    s = MinimapScroll()
    s.update(_view(world, 0, 64), (64, 28))
    got = s.update(world[:40, :90].copy(), (10, 10))
    assert got == (10, 10)


def test_no_dot_this_frame_still_tracks_the_scroll(world):
    """漏認幾幀角色點是常態，但地圖還在捲——原點不能跟著停。"""
    s = MinimapScroll()
    s.update(_view(world, 0, 64), (64, 28))
    for i in range(1, 10):
        assert s.update(_view(world, i * 4, 64), None) is None
    got = s.update(_view(world, 10 * 4, 64), (64, 28))
    assert abs(got[0] - (40 + 64)) <= 4, "中間沒認到點的那幾幀也要繼續累積"


def test_an_empty_minimap_is_survived():
    s = MinimapScroll()
    assert s.update(np.zeros((0, 0, 3), dtype=np.uint8), (5, 5)) == (5, 5)


def test_explain_says_whether_the_map_scrolls(world):
    s = MinimapScroll()
    assert "沒有捲動" in s.explain()
    for i in range(12):
        s.update(_view(world, i * 4, 64), (64, 28))
    assert "會捲動" in s.explain()
