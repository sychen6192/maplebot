"""tools/calibrate.py --write：校正結果寫進 local.yaml，手寫的註解要留著。

以前走 merge_into（safe_dump 整份重寫），重校正一次註解就全沒了，也不留備份。
框選視窗要有顯示環境（CI 的 opencv-headless 沒有），所以這裡直接測寫檔那一步
write_local；從框選到寫檔的整條流程見 test_gui_controller.py 的
test_calibrate_writes_into_local_yaml。
"""
import importlib.util
import os

import pytest
import yaml

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tools", "calibrate.py")

# 已經校正過、手寫了註解的 local.yaml：calibrate 會寫的 key 全部都在
COMMENTED = """\
# 這台電腦的校正結果
window:
  title: 新楓之谷：經典版   # 標題要跟視窗完全一樣
  calibrated_for: [800, 600]   # 校正當下的視窗大小
regions:
  # 小地圖只框畫布：框到標題列的黃色圖示，玩家點會一直鎖在它身上
  minimap: [10, 30, 160, 90]   # 實測 fixture
  hp_bar: [220, 572, 100, 10]
  mp_bar: [330, 572, 100, 10]  # 只框藍色條本體
  exp_bar: [440, 590, 300, 6]
  playfield: [0, 0, 790, 520]
safety:
  critical_hp_ratio: 0.3   # 低於 30% 直接停機
"""

# 視窗從 800x600 換成 1024x768 之後重校正，calibrate 送出的 patch 長這樣
REGIONS = {"minimap": [12, 32, 200, 110], "hp_bar": [280, 730, 130, 12],
           "mp_bar": [420, 730, 130, 12], "exp_bar": [560, 752, 380, 8],
           "playfield": [0, 0, 1010, 670]}
PATCH = {"window": {"calibrated_for": [1024, 768]}, "regions": REGIONS}


@pytest.fixture(scope="module")
def calibrate():
    spec = importlib.util.spec_from_file_location("calibrate_cli", TOOL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _comments(text: str):
    return [line.split("#", 1)[1].strip() for line in text.splitlines() if "#" in line]


def test_recalibrating_keeps_every_comment(tmp_path, calibrate):
    path = tmp_path / "local.yaml"
    path.write_text(COMMENTED, encoding="utf-8")

    bak = calibrate.write_local(str(path), PATCH)

    text = path.read_text(encoding="utf-8")
    assert _comments(text) == _comments(COMMENTED)
    data = yaml.safe_load(text)
    assert data["window"] == {"title": "新楓之谷：經典版", "calibrated_for": [1024, 768]}
    assert data["regions"] == REGIONS
    assert data["safety"] == {"critical_hp_ratio": 0.3}
    # 一行註解都沒掉，就沒有理由留備份
    assert bak is None
    assert not os.path.exists(str(path) + ".bak")


@pytest.mark.parametrize("original", [
    # 上次跳過了 exp_bar，這次框了
    COMMENTED.replace("  exp_bar: [440, 590, 300, 6]\n", ""),
    # 手寫了別的設定、還沒校正過
    "# 這台電腦的個人設定\nsafety:\n  critical_hp_ratio: 0.3   # 低於 30% 直接停機\n",
], ids=["one-region-missing", "never-calibrated"])
def test_keys_missing_from_the_file_are_still_written_after_a_backup(
        tmp_path, calibrate, original):
    path = tmp_path / "local.yaml"
    path.write_text(original, encoding="utf-8")

    bak = calibrate.write_local(str(path), PATCH)

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["window"]["calibrated_for"] == [1024, 768]
    assert data["regions"] == REGIONS
    assert data["safety"] == {"critical_hp_ratio": 0.3}
    # 檔裡沒有的 key 只能整份重寫、註解保不住：重寫前的原檔要留在 .bak
    assert bak == str(path) + ".bak"
    with open(bak, encoding="utf-8") as f:
        assert f.read() == original


def test_an_existing_backup_is_never_overwritten(tmp_path, calibrate):
    """.bak 已經在的話那份才是還有註解的原檔；蓋掉就只剩已經洗過的版本。"""
    path = tmp_path / "local.yaml"
    path.write_text("# 已經被整份重寫過一次\nsafety:\n  critical_hp_ratio: 0.3\n",
                    encoding="utf-8")
    old = tmp_path / "local.yaml.bak"
    old.write_text(COMMENTED, encoding="utf-8")

    bak = calibrate.write_local(str(path), PATCH)

    assert bak == str(old)
    assert old.read_text(encoding="utf-8") == COMMENTED
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["regions"] == REGIONS


def test_first_calibration_creates_the_file_without_a_backup(tmp_path, calibrate):
    path = tmp_path / "config" / "local.yaml"

    assert calibrate.write_local(str(path), PATCH) is None

    assert yaml.safe_load(path.read_text(encoding="utf-8")) == PATCH
    assert not os.path.exists(str(path) + ".bak")
