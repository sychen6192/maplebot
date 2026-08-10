"""自動標註的「老師」：描邊老師（免模板）與模板老師。"""
import cv2
import numpy as np
import pytest

from maplebot.config import AppCfg
from maplebot.teachers import (OutlineTeacher, TemplateTeacher,
                               class_from_template_name, make_teacher)
from maplebot.vision.outline_mobs import OutlineMobDetector

W, H = 790, 520                      # 參考解析度，scale = 1.0
PLAYER_BAR = (400, 240, 30, 4)       # 組隊紅條 -> 角色約在 (412, 280)
PLAYER_XY = (412, 280)
# 鏡頭卡在地圖邊緣時角色會偏離中心（畫面中心是 395, 260）
OFF_CENTRE_BAR = (600, 350, 30, 4)
OFF_CENTRE_XY = (612, 390)
RED = (0, 0, 255)


def _scene(bar=PLAYER_BAR):
    """不含純黑的背景 + 一條組隊紅條（bar=None 就是沒組隊）。"""
    rng = np.random.default_rng(5)
    img = rng.integers(60, 200, (H, W, 3), dtype=np.uint8)
    if bar is not None:
        x, y, bw, bh = bar
        cv2.rectangle(img, (x, y), (x + bw, y + bh), RED, -1)
    return img


def _sprite(img, cx, cy, w=34, h=30, outline=(0, 0, 0)):
    """畫一個有描邊的 sprite。"""
    x1, y1 = cx - w // 2, cy - h // 2
    cv2.rectangle(img, (x1, y1), (x1 + w, y1 + h), outline, 2)
    cv2.rectangle(img, (x1 + 3, y1 + 3), (x1 + w - 3, y1 + h - 3),
                  (90, 180, 220), -1)
    return img


@pytest.fixture
def cfg():
    c = AppCfg()
    c.regions = {"playfield": (0, 80, W, H), "minimap": (10, 90, 100, 60)}
    return c


def _centers(labeled):
    return sorted((m.cx, m.cy) for _, m in labeled)


def test_outline_teacher_labels_mobs_without_any_template(cfg):
    """整個重點：不用截任何模板就能標。"""
    img = _sprite(_sprite(_scene(), 150, 150), 650, 400)
    teacher = OutlineTeacher(cfg)

    labeled = teacher.label(img)

    assert teacher.classes == ["mob"]
    assert [cls for cls, _ in labeled] == [0, 0]
    centers = _centers(labeled)
    assert abs(centers[0][0] - 150) <= 4 and abs(centers[0][1] - 150) <= 4
    assert abs(centers[1][0] - 650) <= 4 and abs(centers[1][1] - 400) <= 4


def test_outline_teacher_does_not_label_the_player(cfg):
    """角色被標成怪 = 每張訓練圖都在教學生打自己，這是最貴的錯。"""
    img = _sprite(_sprite(_scene(), *PLAYER_XY), 650, 400)

    labeled = OutlineTeacher(cfg).label(img)

    assert _centers(labeled) == [(pytest.approx(650, abs=4),
                                  pytest.approx(400, abs=4))]


def test_outline_teacher_finds_the_player_off_centre(cfg):
    """角色不在畫面正中央時要靠組隊紅條量出來。

    照「角色永遠在正中央」挖的話，這張圖會剛好標反：把中央那隻真的怪挖掉，
    再把角色自己標成怪。
    """
    teacher = OutlineTeacher(cfg)
    img = _sprite(_scene(OFF_CENTRE_BAR), W // 2, H // 2)   # 中央有一隻真的怪
    img = _sprite(img, *OFF_CENTRE_XY)                      # 角色在右下

    labeled = teacher.label(img)

    assert teacher.player_found == 1
    assert _centers(labeled) == [(pytest.approx(W // 2, abs=4),
                                  pytest.approx(H // 2, abs=4))]


def test_without_the_party_bar_the_centre_assumption_labels_it_backwards(cfg):
    """上一個測試在測什麼：關掉定位就會標反，證明那條路真的有在走。"""
    cfg.vision.locate_player_bar = False
    img = _sprite(_scene(OFF_CENTRE_BAR), W // 2, H // 2)
    img = _sprite(img, *OFF_CENTRE_XY)

    labeled = OutlineTeacher(cfg).label(img)

    assert _centers(labeled) == [(pytest.approx(OFF_CENTRE_XY[0], abs=4),
                                  pytest.approx(OFF_CENTRE_XY[1], abs=4))]


def test_outline_teacher_counts_frames_where_the_player_was_not_found(cfg):
    teacher = OutlineTeacher(cfg)
    teacher.label(_scene(bar=None))
    assert (teacher.images, teacher.player_found) == (1, 0)
    warning = teacher.explain()
    assert "把角色標成怪" in warning        # 說出後果
    assert "--format png" in warning        # 也給辦法

    teacher.label(_scene())
    assert (teacher.images, teacher.player_found) == (2, 1)


def test_outline_teacher_reset_clears_the_stats(cfg):
    teacher = OutlineTeacher(cfg)
    teacher.label(_scene())
    teacher.reset()
    assert (teacher.images, teacher.player_found) == (0, 0)
    assert "角色定位" not in teacher.explain()


def test_outline_teacher_blanks_ui_overlays(cfg):
    """小地圖面板的標題文字有黑色描邊，會被標成一隻固定存在的「怪」。"""
    cfg.vision.mob_exclude = [(0, 80, 200, 200)]   # client 座標，蓋住 playfield 左上
    img = _sprite(_sprite(_scene(), 100, 100), 650, 400)

    labeled = OutlineTeacher(cfg).label(img)

    assert _centers(labeled) == [(pytest.approx(650, abs=4),
                                  pytest.approx(400, abs=4))]


def test_outline_teacher_does_not_mutate_the_caller_s_image(cfg):
    cfg.vision.mob_exclude = [(0, 80, 200, 200)]
    img = _sprite(_scene(), 650, 400)
    before = img.copy()
    OutlineTeacher(cfg).label(img)
    assert (img == before).all()


def test_black_level_override_recovers_jpeg_crushed_outlines(cfg):
    """JPEG 會把純黑壓成 (3,2,4) 這種值——離線標註要調高門檻才抓得到。"""
    img = _sprite(_scene(), 650, 400, outline=(11, 11, 11))

    assert OutlineTeacher(cfg, black_level=0).label(img) == []
    assert len(OutlineTeacher(cfg, black_level=15).label(img)) == 1


def test_outline_teacher_ignores_a_yolo_detector_setting(cfg):
    """使用者可能已經把 mob_detector 換成 yolo；拿學生當老師只會放大錯誤。"""
    cfg.vision.mob_detector = "yolo"
    assert isinstance(OutlineTeacher(cfg).det, OutlineMobDetector)


def test_class_name_strips_index():
    assert class_from_template_name("snail_01") == "snail"
    assert class_from_template_name("orange_mushroom_12") == "orange_mushroom"
    assert class_from_template_name("slime") == "slime"


@pytest.fixture
def templates(tmp_path):
    d = tmp_path / "mobs"
    d.mkdir()
    rng = np.random.default_rng(42)
    for name in ("snail_01", "snail_02", "mushroom_01"):
        cv2.imwrite(str(d / f"{name}.png"),
                    rng.integers(0, 255, (24, 30, 3), dtype=np.uint8))
    return str(d)


def test_template_teacher_classes_come_from_the_file_names(templates, cfg):
    assert TemplateTeacher(templates, 0.8).classes == ["mushroom", "snail"]
    assert TemplateTeacher(templates, 0.8, single_class=True).classes == ["mob"]


def test_template_teacher_without_templates_points_at_the_outline_teacher(tmp_path, cfg):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="--teacher outline"):
        TemplateTeacher(str(empty), 0.8)


def test_template_teacher_labels_with_the_matching_class(templates, cfg):
    tpl = cv2.imread(f"{templates}/mushroom_01.png", cv2.IMREAD_COLOR)
    img = np.zeros((200, 300, 3), dtype=np.uint8)
    img[100:124, 50:80] = tpl
    teacher = TemplateTeacher(templates, 0.8)

    labeled = teacher.label(img)

    assert len(labeled) == 1
    cls_id, mob = labeled[0]
    assert teacher.classes[cls_id] == "mushroom"
    assert (mob.cx, mob.cy) == (65, 112)


def test_make_teacher_picks_the_implementation(cfg, templates):
    assert isinstance(make_teacher("outline", cfg), OutlineTeacher)
    assert isinstance(make_teacher("template", cfg, templates_dir=templates),
                      TemplateTeacher)


def test_make_teacher_rejects_an_unknown_name(cfg):
    with pytest.raises(ValueError, match="outline / template"):
        make_teacher("magic", cfg)


def test_make_teacher_falls_back_to_the_config_threshold(cfg, templates):
    cfg.vision.mob_match_threshold = 0.55
    assert make_teacher("template", cfg, templates_dir=templates).det.threshold == 0.55
