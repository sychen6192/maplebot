"""辨識自檢：snapshot() 在實拍截圖上的讀值、兩張圖與 JSON，以及給人看的判讀。

判讀（verdicts）是 GUI 自檢視窗唯一的內容來源，所以這裡斷言的不只是狀態，
還有「錯的時候指向哪個按鈕」——讀不到 HP 卻只給一句「怪怪的」沒有用。
"""
import json
import os
import subprocess
import sys

import cv2
import pytest

from maplebot.capture import ImageCapture
from maplebot.config import load_config, load_profile
from maplebot.diagnostics.selfcheck import VisionReport, fit, preview_path, snapshot, verdicts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "mapleaga_800x600.jpg")
DEFAULT_CFG = os.path.join(ROOT, "config", "default.yaml")
EXAMPLE_PROFILE = os.path.join(ROOT, "config", "profiles", "example.yaml")


@pytest.fixture
def fixture_report(tmp_path):
    cfg = load_config(DEFAULT_CFG, local_path="/nonexistent")
    # default.yaml 明寫了 ui_templates_dir，conftest 的隔離蓋不到；
    # 開發機上有自己截的名牌模板時結果會因人而異
    cfg.vision.ui_templates_dir = str(tmp_path / "ui")
    profile = load_profile(EXAMPLE_PROFILE)
    out = tmp_path / "logs" / "check.png"
    return snapshot(cfg, profile, ImageCapture(FIXTURE), str(out)), out


# ---- snapshot ----

def test_snapshot_reads_the_fixture_like_calibration_says(fixture_report):
    """數值同 CLAUDE.md：fixture 的 HP/MP/EXP 是 1.0 / 1.0 / 0.591304。"""
    r, _ = fixture_report
    assert r.hp == pytest.approx(1.0)
    assert r.mp == pytest.approx(1.0)
    assert r.exp == pytest.approx(0.591304, abs=1e-5)
    assert r.size == (806, 629)
    assert r.minimap_xy is not None
    assert r.mobs == len(r.mob_boxes)
    assert r.action
    assert r.nametag_template is False
    # 預設 region 是對這張圖校正的，交叉驗證不該報錯
    assert r.bar_checks and all(row[0] != "fail" for row in r.bar_checks)


def test_snapshot_writes_full_image_and_preview_inside_the_box(fixture_report):
    r, out = fixture_report
    assert r.image == os.path.abspath(str(out))
    assert r.preview == os.path.abspath(preview_path(str(out)))
    assert r.preview.endswith("check_preview.png")

    full = cv2.imread(r.image)
    assert full.shape[:2] == (629, 806)      # 原尺寸，座標讀值才對得起來

    small = cv2.imread(r.preview)
    h, w = small.shape[:2]
    assert w <= 640 and h <= 400
    assert w == 640 or h == 400               # 有填滿其中一邊，不是縮過頭
    assert w / h == pytest.approx(806 / 629, rel=0.01)


def test_report_json_round_trips(fixture_report):
    r, _ = fixture_report
    text = r.to_json()
    assert text.isascii()                    # Windows 上不管用什麼編碼讀都不會壞
    back = VisionReport.from_json(text)
    assert back == r
    assert isinstance(back.minimap_xy, tuple)
    assert isinstance(back.size, tuple)


def test_from_json_tolerates_old_or_newer_reports():
    """logs/ 裡留著別的版本寫的 check.json 時，結果視窗照樣開得起來。"""
    r = VisionReport.from_json(json.dumps({"hp": 0.5, "someday": 1}))
    assert r.hp == 0.5 and r.mp is None and r.bar_checks == []


def test_fixture_verdicts_have_no_failure(fixture_report):
    r, _ = fixture_report
    rows = verdicts(r)
    assert all(status != "fail" for status, _, _ in rows)
    assert rows[0][1].startswith("HP 100%・MP 100%・EXP 59%")


def test_debug_view_writes_the_same_report(tmp_path):
    """GUI 走的是子行程 debug_view --snapshot --report；那條路要真的寫出 JSON。

    跑在 config/default.yaml 上，但開發機的 config/local.yaml 會蓋掉 region，
    所以這裡只驗流程，不驗讀值（讀值由上面的測試負責）。
    """
    out, rep = tmp_path / "check.png", tmp_path / "check.json"
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "debug_view.py"),
         "--source", FIXTURE, "--snapshot", str(out), "--report", str(rep)],
        cwd=ROOT, capture_output=True, timeout=180)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    r = VisionReport.from_json(rep.read_text(encoding="ascii"))
    assert r.size == (806, 629)
    assert os.path.exists(r.image) and os.path.exists(r.preview)


def test_selfcheck_does_not_pull_in_tkinter():
    """GUI 以外的模組要在沒有顯示環境的 CI 上 import 得起來。"""
    code = ("import sys, maplebot.diagnostics.selfcheck; "
            "assert 'tkinter' not in sys.modules, 'tkinter imported'")
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                          capture_output=True, timeout=120)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")


# ---- verdicts ----

def _good(**kw):
    base = dict(hp=0.8, mp=0.6, exp=0.3, minimap_xy=(40, 20), screen_xy=(395, 260),
                mobs=2, nametag_template=True)
    base.update(kw)
    return VisionReport(**base)


def _row(rows, needle):
    hits = [row for row in rows if needle in row[1]]
    assert hits, f"沒有含「{needle}」的判讀：{rows}"
    return hits[0]


def test_healthy_report_is_all_ok():
    rows = verdicts(_good())
    assert [s for s, _, _ in rows] == ["ok"] * len(rows)
    assert _row(rows, "HP 80%・MP 60%・EXP 30%")[2] is None
    assert _row(rows, "(40, 20)")


@pytest.mark.parametrize("hp, needle", [(None, "讀不到 HP"), (0.0, "HP 讀成 0%")])
def test_bad_hp_fails_and_points_at_calibration(hp, needle):
    status, _, action = _row(verdicts(_good(hp=hp)), needle)
    assert (status, action) == ("fail", "calibrate")


def test_missing_mp_only_warns():
    status, text, action = _row(verdicts(_good(mp=None)), "讀不到 MP")
    assert (status, action) == ("warn", None)
    assert "喝 MP 藥" in text


def test_lost_minimap_dot_fails_and_points_at_calibration():
    status, _, action = _row(verdicts(_good(minimap_xy=None)), "找不到角色黃點")
    assert (status, action) == ("fail", "calibrate")


def test_other_players_warn_that_the_bot_will_pause():
    status, text, _ = _row(verdicts(_good(others=2)), "其他玩家")
    assert status == "warn" and "2 位" in text and "暫停" in text


def test_no_nametag_template_warns_and_offers_grabbing_one():
    rows = verdicts(_good(screen_xy=None, nametag_template=False))
    status, text, action = _row(rows, "還沒截角色名牌")
    assert (status, action) == ("warn", "nametag")
    assert "畫面正中央" in text


def test_nametag_template_that_does_not_match_suggests_regrabbing():
    status, _, action = _row(verdicts(_good(screen_xy=None)), "比不中名牌")
    assert (status, action) == ("warn", "nametag")


def test_caller_can_override_whether_a_nametag_exists():
    rows = verdicts(_good(screen_xy=None, nametag_template=True), nametag_exists=False)
    assert _row(rows, "還沒截角色名牌")


def test_character_fixed_by_party_bar_is_not_credited_to_the_nametag():
    """沒有名牌模板還量得到角色定位，那是組隊紅條給的，不能說「找到名牌」。"""
    rows = verdicts(_good(nametag_template=False))
    assert _row(rows, "組隊紅條")[0] == "ok"
    assert not [row for row in rows if "找到角色名牌" in row[1]]


def test_no_mobs_warns_and_points_at_the_vision_tab():
    status, text, action = _row(verdicts(_good(mobs=0)), "沒框到任何怪物")
    assert (status, action) == ("warn", "tab:vision")
    assert "描邊黑度" in text and "16~32" in text


def test_followers_are_reported_only_when_present():
    assert not [r for r in verdicts(_good()) if "跟隨物" in r[1]]
    assert _row(verdicts(_good(followers=1)), "1 個跟隨物")[0] == "ok"


def test_status_bar_checks_pass_through_with_a_calibrate_button_when_wrong():
    rows = verdicts(_good(bar_checks=[
        ("ok", "hp_bar 位置", "與自動偵測相符（讀值偏差約 3%）", ""),
        ("fail", "mp_bar 位置", "垂直完全錯開", "執行 python tools/calibrate.py --write"),
        ("warn", "血條 ROI 交叉驗證", "畫面上自動找不到狀態列", "用 debug_view 看一眼"),
    ]))
    assert _row(rows, "hp_bar 位置：與自動偵測相符") == \
        ("ok", "hp_bar 位置：與自動偵測相符（讀值偏差約 3%）", None)
    assert _row(rows, "mp_bar")[0::2] == ("fail", "calibrate")
    assert _row(rows, "自動找不到狀態列")[0::2] == ("warn", None)


def test_verdict_text_carries_no_emoji():
    """符號由畫面依 status 加（√ ！ ×）。doctor.MARK 那組 emoji 字型沒有就畫不出來，
    ⚠️ 還多帶一個 U+FE0F，文字裡一個都不該出現。"""
    rows = verdicts(_good(hp=None, mp=None, minimap_xy=None, screen_xy=None,
                          nametag_template=False, mobs=0, others=1, followers=1))
    for _, text, _ in rows:
        assert all(ord(ch) <= 0xFFFF and ord(ch) != 0xFE0F for ch in text), text
        assert not set(text) & set("✅❌⚠")


# ---- fit ----

def test_fit_keeps_aspect_inside_the_box():
    assert fit(800, 600, 640, 400) == (533, 400)
    assert fit(1920, 1080, 640, 400) == (640, 360)


def test_fit_never_upscales():
    assert fit(320, 200, 640, 400) == (320, 200)
    assert fit(640, 400, 640, 400) == (640, 400)
