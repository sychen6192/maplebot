"""GroundingDINO 老師的純邏輯（prompt 組裝、輸出解析、NPC 剔除）。

模型本身要下載權重，這裡只驗證不需要網路的轉換與過濾邏輯。
"""
import importlib.util
import os

import pytest

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tools", "label_gdino.py")


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("label_gdino", TOOL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_prompt_format_lowercase_period_terminated(mod):
    assert mod.build_text_prompt(["Monster"]) == "monster."
    assert mod.build_text_prompt(["monster", "Blue Snail"]) == "monster. blue snail."
    assert mod.build_text_prompt(["monster."]) == "monster."      # 不重複加句點
    assert mod.build_text_prompt([" mob ", ""]) == "mob."


def test_extract_boxes_reads_text_labels(mod):
    result = {"boxes": [[10.0, 20.0, 40.0, 44.0]], "scores": [0.83],
              "text_labels": ["monster"]}
    assert mod.extract_boxes(result) == [(10, 20, 40, 44, pytest.approx(0.83), "monster")]


def test_extract_boxes_falls_back_to_labels_key(mod):
    """舊版 transformers 用 labels，新版用 text_labels。"""
    result = {"boxes": [[0, 0, 10, 10]], "scores": [0.5], "labels": ["NPC"]}
    assert mod.extract_boxes(result)[0][5] == "npc"


def test_extract_boxes_empty(mod):
    assert mod.extract_boxes({"boxes": [], "scores": []}) == []
    assert mod.extract_boxes({}) == []


def test_filter_drops_rejected_labels(mod):
    boxes = [
        (0, 0, 30, 30, 0.9, "monster"),
        (100, 0, 130, 40, 0.8, "npc"),
        (200, 0, 230, 40, 0.7, "person"),
    ]
    kept = mod.filter_boxes(boxes, ["monster"], ["npc", "person"])
    assert [b[5] for b in kept] == ["monster"]


def test_filter_drops_monster_overlapping_an_npc(mod):
    """同一個 NPC 同時被判成 monster 時，以剔除為準（寧可漏標）。"""
    boxes = [
        (100, 100, 140, 150, 0.6, "monster"),   # 幾乎等同下面那個 npc
        (102, 101, 141, 149, 0.7, "npc"),
        (300, 100, 330, 130, 0.8, "monster"),   # 沒重疊，留著
    ]
    kept = mod.filter_boxes(boxes, ["monster"], ["npc"], iou_drop=0.4)
    assert len(kept) == 1
    assert kept[0][0] == 300


def test_filter_without_reject_keeps_everything(mod):
    boxes = [(0, 0, 10, 10, 0.5, "monster"), (20, 20, 30, 30, 0.5, "whatever")]
    assert len(mod.filter_boxes(boxes, ["monster"], [])) == 2


def test_filter_requires_keep_label_when_rejecting(mod):
    """有指定正例時，標籤對不上的一律不要（避免把樹當怪）。"""
    boxes = [(0, 0, 10, 10, 0.5, "tree")]
    assert mod.filter_boxes(boxes, ["monster"], ["npc"]) == []


def test_to_yolo_dets_converts_to_center(mod):
    boxes = [(10, 20, 40, 44, 0.83, "monster")]
    assert mod.to_yolo_dets(boxes) == [(0, 25, 32, 30, 24, pytest.approx(0.83))]


def test_iou_helper(mod):
    assert mod._iou((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)
    assert mod._iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert mod._iou((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(1 / 3)
