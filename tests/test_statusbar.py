"""自動找 HP/MP/EXP 條：要認得出整條（含空的那段），也不能被彩色按鈕騙走。"""
import numpy as np

from maplebot.vision.statusbar import find_status_bars
from maplebot.vision.status import bar_ratio

FILL = {"red": (0, 0, 220), "blue": (220, 60, 0), "yellow": (0, 200, 220)}   # BGR


def _ui(w=1366, h=768, bar_y=700, bar_h=14, width=100, fills=(0.6, 1.0, 0.5),
        gap=10, x0=500):
    """畫一條狀態列：三條 bar 並排，各自有深色外框與灰色空白段。"""
    img = np.full((h, w, 3), 40, dtype=np.uint8)
    boxes = {}
    x = x0
    for (name, color), frac in zip((("hp_bar", "red"), ("mp_bar", "blue"),
                                    ("exp_bar", "yellow")), fills):
        img[bar_y - 1:bar_y + bar_h + 1, x - 1:x + width + 1] = (10, 10, 10)  # 外框
        img[bar_y:bar_y + bar_h, x:x + width] = (150, 150, 150)               # 空段
        img[bar_y:bar_y + bar_h, x:x + int(width * frac)] = FILL[color]
        boxes[name] = (x, bar_y, width, bar_h)
        x += width + gap
    return img, boxes


def test_finds_all_three_bars():
    img, boxes = _ui()
    found = find_status_bars(img)
    assert found is not None
    for name, (bx, by, bw, bh) in boxes.items():
        fx, fy, fw, fh = found[name]
        assert abs(fx - bx) <= 2 and abs(fy - by) <= 2, (name, found[name])
        assert abs(fw - bw) <= 3, (name, fw, bw)


def test_found_roi_reads_the_right_ratio():
    """只框有顏色的那段的話，血剩 60% 會被當成整條，永遠讀成 100%。"""
    img, _ = _ui(fills=(0.6, 1.0, 0.35))
    found = find_status_bars(img)
    assert abs(bar_ratio(img[found["hp_bar"][1]:found["hp_bar"][1] + found["hp_bar"][3],
                             found["hp_bar"][0]:found["hp_bar"][0] + found["hp_bar"][2]],
                         "red") - 0.6) < 0.05


def test_not_fooled_by_coloured_buttons():
    """右下角的購物商場/拍賣/目錄按鈕也是紅藍黃——第一版就是被它們騙走的。

    按鈕接近正方形、彼此 y 也對不齊，靠「又扁又長 + 同一排」擋掉。
    """
    img, boxes = _ui()
    for i, color in enumerate(("red", "blue", "yellow")):    # 三顆方形彩色按鈕
        bx = 1150 + i * 60
        img[690:730, bx:bx + 45] = FILL[color]
    found = find_status_bars(img)
    assert found is not None
    assert abs(found["hp_bar"][0] - boxes["hp_bar"][0]) <= 2, found


def test_no_status_bar_returns_none():
    assert find_status_bars(np.full((768, 1366, 3), 40, dtype=np.uint8)) is None


def test_empty_frame():
    assert find_status_bars(np.zeros((0, 0, 3), dtype=np.uint8)) is None


def test_works_at_another_resolution():
    img, boxes = _ui(w=1920, h=1080, bar_y=1000, bar_h=20, width=140, x0=700)
    found = find_status_bars(img)
    assert found is not None
    assert abs(found["exp_bar"][0] - boxes["exp_bar"][0]) <= 2


# --- 與設定的 ROI 交叉驗證（doctor.check_status_bars）---
# 這是「開場自檢只擋得到 HP 恰好讀成 0%」漏掉的另一半：ROI 平移之後讀到一個
# **合理但錯**的值，誰都看不出來。

class _Cfg:
    def __init__(self, regions):
        self.regions = regions


def test_matching_rois_pass():
    from maplebot.doctor import FAIL, check_status_bars
    img, boxes = _ui()
    checks = check_status_bars(_Cfg(dict(boxes)), img)
    assert checks and not [c for c in checks if c.status == FAIL]


def test_a_shifted_hp_roi_is_caught():
    """框歪的血條必須被擋下來——這正是「莫名其妙就停機」的成因。"""
    from maplebot.doctor import FAIL, check_status_bars
    img, boxes = _ui()
    bad = dict(boxes)
    x, y, w, h = bad["hp_bar"]
    bad["hp_bar"] = (x + 70, y, w, h)        # 往右平移 70px（重疊只剩 ~18%）
    checks = check_status_bars(_Cfg(bad), img)
    bad_checks = [c for c in checks if c.status == FAIL]
    assert len(bad_checks) == 1
    assert "hp_bar" in bad_checks[0].name
    assert "calibrate" in bad_checks[0].fix


def test_a_bar_shifted_off_the_strip_is_caught():
    """整條移到別的地方（換解析度沒重新校正）也要抓到。"""
    from maplebot.doctor import FAIL, check_status_bars
    img, boxes = _ui()
    bad = dict(boxes)
    x, y, w, h = bad["mp_bar"]
    bad["mp_bar"] = (x, y - 40, w, h)
    assert [c for c in check_status_bars(_Cfg(bad), img) if c.status == FAIL]


def test_an_unrecognised_status_strip_only_warns():
    """認不出狀態列不代表使用者設定錯——可能只是客戶端長得不一樣，不能擋跑。"""
    from maplebot.doctor import FAIL, WARN, check_status_bars
    blank = np.full((768, 1366, 3), 40, dtype=np.uint8)
    checks = check_status_bars(_Cfg({"hp_bar": (500, 700, 100, 14)}), blank)
    assert [c for c in checks if c.status == WARN]
    assert not [c for c in checks if c.status == FAIL]


def test_the_threshold_matches_the_measured_symptom(fixture_frame):
    """門檻要跟「讀值真的開始出錯」對得上，不是憑感覺訂的。

    實拍量測（hp_bar 寬 105）：平移 5px 讀值差 4.8%、10px 差 9.5%、20px 差 19%。
    喝藥門檻常設在 50%/30%，10% 的誤差足以讓它提早或延後觸發，所以界線畫在那裡。
    """
    from maplebot.config import load_config
    from maplebot.doctor import FAIL, OK, check_status_bars

    cfg = load_config("config/default.yaml", local_path="/nonexistent")
    x, y, w, h = cfg.regions["hp_bar"]

    def status_at(shift):
        cfg.regions["hp_bar"] = (x + shift, y, w, h)
        return [c for c in check_status_bars(cfg, fixture_frame)
                if "hp_bar" in c.name][0].status

    assert status_at(0) == OK
    assert status_at(5) == OK        # 讀值差 4.8%，還能用
    assert status_at(10) == FAIL     # 讀值差 9.5%，該叫人了
    assert status_at(20) == FAIL


def test_vertical_shift_alone_is_tolerated(fixture_frame):
    """垂直歪掉不影響比例讀值，不該為此擋人。

    bar_ratio 算的是「最右邊有色的欄 / 寬度」——只要還切到色條，上下差幾 px
    讀出來一模一樣（實測平移 2/4/6/8px 都是 1.000）。
    """
    from maplebot.config import load_config
    from maplebot.doctor import FAIL, check_status_bars

    cfg = load_config("config/default.yaml", local_path="/nonexistent")
    x, y, w, h = cfg.regions["hp_bar"]
    cfg.regions["hp_bar"] = (x, y + 3, w, h)
    assert not [c for c in check_status_bars(cfg, fixture_frame)
                if c.status == FAIL]
