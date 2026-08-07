"""GroundingDINO 老師的純邏輯部分（prompt 組裝、輸出解析）。

模型本身要下載權重，這裡只驗證不需要網路的轉換邏輯。
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
    # GroundingDINO 要求小寫、每詞句點結尾
    assert mod.build_text_prompt(["Monster"]) == "monster."
    assert mod.build_text_prompt(["monster", "Blue Snail"]) == "monster. blue snail."
    assert mod.build_text_prompt(["monster."]) == "monster."      # 不重複加句點
    assert mod.build_text_prompt([" mob ", ""]) == "mob."


def test_parse_results_converts_xyxy_to_center(mod):
    result = {"boxes": [[10.0, 20.0, 40.0, 44.0]], "scores": [0.83]}
    dets = mod.parse_results(result)
    assert dets == [(0, 25, 32, 30, 24, pytest.approx(0.83))]


def test_parse_results_empty_and_missing_keys(mod):
    assert mod.parse_results({"boxes": [], "scores": []}) == []
    assert mod.parse_results({}) == []


def test_parse_results_multiple_boxes_keep_order(mod):
    result = {"boxes": [[0, 0, 10, 10], [100, 50, 140, 90]], "scores": [0.9, 0.4]}
    dets = mod.parse_results(result)
    assert [(d[1], d[2]) for d in dets] == [(5, 5), (120, 70)]
