"""督導點名：runner 把「原地打太久」的目標送去問督導層，並套用回來的答案。

答案要 1~3 秒才回來，這段時間世界已經變了，所以釘住的都是「什麼時候不能動作」：
  * 答案是 monster、或沒有答案（督導層壞了）-> 照打，12 秒保護兜底
  * 答案屬於上一串攻擊（怪早死了、角色移動過）-> 丟掉，不能打斷新的一串
"""
import logging

import cv2
import numpy as np
import pytest
import yaml

from maplebot.brain import fsm
from maplebot.capture import ImageCapture
from maplebot.config import load_config, load_profile
from maplebot.control.input_win import Keyboard, NullBackend
from maplebot.runner import Runner
from maplebot.vision.mobs import make_detector

NOW = 5000.0


@pytest.fixture
def runner(tmp_path):
    img = np.zeros((300, 300, 3), dtype=np.uint8)
    img[60:68, 10:60] = (0, 0, 230)
    shot = tmp_path / "shot.png"
    cv2.imwrite(str(shot), img)
    cfg_path = tmp_path / "default.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "window": {"title": "X"},
        "regions": {"minimap": [10, 10, 120, 40], "hp_bar": [10, 60, 50, 8],
                    "playfield": [0, 80, 300, 200]},
        "report": {"enabled": False},
        "advisor": {"enabled": True},
    }), encoding="utf-8")
    prof_path = tmp_path / "map.yaml"
    prof_path.write_text(yaml.safe_dump(
        {"name": "t", "patrol": {"waypoints": [30, 90]}, "attack": {"key": "x"}}),
        encoding="utf-8")
    cfg = load_config(str(cfg_path), local_path="")
    profile = load_profile(str(prof_path))
    return Runner(cfg, profile, ImageCapture(str(shot)), Keyboard(NullBackend()),
                  make_detector(cfg.vision, profile.templates_dir),
                  logging.getLogger("maplebot.test.target"), dry_run=True)


def _attacking(r, since=NOW - 5.0):
    """模擬 decide 已經在原地打了一陣子：attack_pos 有值、這串從 since 開始。"""
    r.rt.attack_pos = (60, 30)
    r.rt.attack_since = since
    return fsm.Attack(1, "x", 0.45, 1, target=(100, 40, 30, 24))


def test_not_a_monster_for_this_streak_yields_to_patrol(runner):
    _attacking(runner)
    runner._target_verdict = (runner.rt.attack_since, {"kind": "player", "note": "名牌"})
    runner._apply_target_verdict(NOW)
    assert runner.rt.attack_break_until == NOW + runner.cfg.safety.attack_break_seconds
    assert runner.rt.attack_pos is None
    assert runner.stats.target_breaks == 1
    # 12 秒保護那句不能再印一次：這次讓路是督導點名的
    assert runner._attack_breaks == runner.rt.attack_breaks


@pytest.mark.parametrize("kind, loot", [("item", True), ("player", False), ("pet", False)])
def test_only_an_item_verdict_asks_for_a_pickup(runner, kind, loot):
    """掉落物要撿起來才會消失；玩家、寵物撿不走，讓路就好。"""
    _attacking(runner)
    runner._target_verdict = (runner.rt.attack_since, {"kind": kind, "note": "x"})
    runner._apply_target_verdict(NOW)
    assert runner.rt.loot_now is loot


@pytest.mark.parametrize("verdict", [{"kind": "monster", "note": "藍寶"}, None, {}])
def test_monster_or_no_answer_keeps_attacking(runner, verdict):
    _attacking(runner)
    runner._target_verdict = (runner.rt.attack_since, verdict)
    runner._apply_target_verdict(NOW)
    assert runner.rt.attack_break_until == 0.0
    assert runner.stats.target_breaks == 0


def test_an_answer_about_an_older_streak_is_dropped(runner):
    """問的時候在打掛機玩家，答案回來時已經換成在打一隻真的怪了。"""
    _attacking(runner, since=NOW - 1.0)
    runner._target_verdict = (NOW - 9.0, {"kind": "player", "note": "上一串的"})
    runner._apply_target_verdict(NOW)
    assert runner.rt.attack_break_until == 0.0


def test_an_answer_after_the_streak_ended_is_dropped(runner):
    _attacking(runner)
    streak = runner.rt.attack_since
    runner.rt.attack_pos = None          # 怪死了，這串已經結束
    runner._target_verdict = (streak, {"kind": "player", "note": "x"})
    runner._apply_target_verdict(NOW)
    assert runner.rt.attack_break_until == 0.0


def test_a_stalled_attack_is_sent_once_with_screen_coordinates(runner, monkeypatch):
    asked = []
    monkeypatch.setattr(runner.advisor, "check_target_async",
                        lambda frame, box, cb: asked.append(box) or True)
    frame = np.zeros((300, 300, 3), np.uint8)
    action = _attacking(runner)
    runner._request_target_check(action, frame, NOW)
    runner._request_target_check(action, frame, NOW + 1)     # 同一串不再問
    assert asked == [(100, 40 + 80, 30, 24)]                  # 加上 playfield 的原點


def test_still_stuck_after_a_monster_answer_asks_again(runner, monkeypatch):
    """最難的框單次只對 7/10：答了「是怪」卻還是原地打不死，過 after 秒再問一次。"""
    asked = []
    monkeypatch.setattr(runner.advisor, "check_target_async",
                        lambda frame, box, cb: asked.append(box) or True)
    frame = np.zeros((300, 300, 3), np.uint8)
    action = _attacking(runner)
    after = runner.cfg.advisor.target_check_after
    runner._request_target_check(action, frame, NOW)
    runner._request_target_check(action, frame, NOW + after - 0.1)   # 還沒到
    runner._request_target_check(action, frame, NOW + after)         # 同一串，再問
    assert len(asked) == 2


def test_dry_potions_alert_once_until_hp_recovers(runner, monkeypatch):
    """藥水用完要馬上叫人，但不能每一幀都叫；補了藥之後再用完要能再叫一次。"""
    import maplebot.runner as runner_mod
    saved, pings = [], []
    monkeypatch.setattr(runner_mod, "save_anomaly", lambda frame, why, log: saved.append(why))
    monkeypatch.setattr(runner.alerts, "ping", lambda kind, msg: pings.append(msg))
    frame = np.zeros((300, 300, 3), np.uint8)
    runner.rt.hp_potions_dry = True
    runner._notice_potions_dry(frame)
    runner._notice_potions_dry(frame)
    assert len(saved) == 1 and len(pings) == 1
    runner.rt.hp_potions_dry = False        # 補了藥
    runner._notice_potions_dry(frame)
    runner.rt.hp_potions_dry = True         # 又用完了
    runner._notice_potions_dry(frame)
    assert len(pings) == 2


def test_a_short_attack_is_not_sent(runner, monkeypatch):
    asked = []
    monkeypatch.setattr(runner.advisor, "check_target_async",
                        lambda frame, box, cb: asked.append(box) or True)
    action = _attacking(runner, since=NOW - 1.0)
    runner._request_target_check(action, np.zeros((300, 300, 3), np.uint8), NOW)
    assert asked == []


def test_a_busy_supervisor_is_asked_again_next_tick(runner, monkeypatch):
    """上一個詢問還沒回來時送不出去——不能把這串記成「問過了」。"""
    replies = iter([False, True])
    asked = []

    def fake(frame, box, cb):
        ok = next(replies)
        if ok:
            asked.append(box)
        return ok

    monkeypatch.setattr(runner.advisor, "check_target_async", fake)
    frame = np.zeros((300, 300, 3), np.uint8)
    action = _attacking(runner)
    runner._request_target_check(action, frame, NOW)
    runner._request_target_check(action, frame, NOW + 0.5)
    assert len(asked) == 1
