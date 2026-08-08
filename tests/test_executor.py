"""執行層：驗證各 Action 產生的按鍵序列、冷卻記錄與統計。"""
import logging

import pytest

from maplebot.brain import fsm
from maplebot.control.input_win import Keyboard, NullBackend, SCANCODES
from maplebot.executor import Executor, Stats

NOW = 500.0


@pytest.fixture
def ex():
    backend = NullBackend()
    kb = Keyboard(backend)
    executor = Executor(kb, fsm.Runtime(), Stats(),
                        logging.getLogger("test"), dry_run=True)
    return executor, backend


def _taps(backend):
    """把 (down, up) 配對還原成被敲過的 scancode 序列。"""
    return [scan for kind, scan in backend.history if kind == "down"]


def test_drink_potion(ex):
    executor, backend = ex
    executor.execute(fsm.DrinkPotion("hp", "pageup"), NOW)
    assert _taps(backend) == [SCANCODES["pageup"][0]]
    assert executor.rt.last_potion["hp"] == NOW
    assert executor.stats.potions_hp == 1


def test_cast_buff(ex):
    executor, backend = ex
    executor.execute(fsm.CastBuff(0, "8", cast_seconds=0.5), NOW)
    assert _taps(backend) == [SCANCODES["8"][0]]
    assert executor.rt.last_buff[0] == NOW
    assert executor.stats.buffs == 1


def test_attack_faces_then_hits_twice(ex):
    executor, backend = ex
    executor.execute(fsm.Attack(direction=-1, key="x", cast_seconds=0.3, repeat=2), NOW)
    assert _taps(backend) == [SCANCODES["left"][0],
                              SCANCODES["x"][0], SCANCODES["x"][0]]
    assert executor.rt.last_attack == NOW
    assert executor.stats.attacks == 1


def test_aoe_attack_skips_facing(ex):
    executor, backend = ex
    executor.execute(fsm.Attack(direction=1, key="x", cast_seconds=0.3,
                                repeat=1, aoe=True), NOW)
    assert _taps(backend) == [SCANCODES["x"][0]]  # 沒有方向鍵


def test_move_taps_direction(ex):
    executor, backend = ex
    executor.execute(fsm.Move(direction=1, seconds=0.3, target_x=95), NOW)
    assert _taps(backend) == [SCANCODES["right"][0]]


def test_run_keys_taps_in_order(ex):
    executor, backend = ex
    executor.execute(fsm.RunKeys(keys=["alt", "x"]), NOW)
    assert _taps(backend) == [SCANCODES["alt"][0], SCANCODES["x"][0]]


def test_escape_jumps_while_holding_direction(ex):
    executor, backend = ex
    executor.execute(fsm.Escape(direction=1, jump_key="alt"), NOW)
    assert backend.history == [
        ("down", SCANCODES["right"][0]),
        ("down", SCANCODES["alt"][0]),
        ("up", SCANCODES["alt"][0]),
        ("up", SCANCODES["right"][0]),
    ]
    assert executor.stats.escapes == 1


def test_probe_taps_direction(ex):
    executor, backend = ex
    executor.execute(fsm.Probe(direction=-1, seconds=0.3), NOW)
    assert _taps(backend) == [SCANCODES["left"][0]]


def test_climb_up_holds_up_key(ex):
    executor, backend = ex
    executor.execute(fsm.Climb(direction=-1, key="up", seconds=0.4), NOW)
    assert _taps(backend) == [SCANCODES["up"][0]]
    assert executor.stats.climbs == 1


def test_climb_jump_down_jumps_while_holding_down(ex):
    executor, backend = ex
    executor.execute(fsm.Climb(direction=1, key="down", seconds=0.4, jump_key="alt"), NOW)
    assert backend.history == [
        ("down", SCANCODES["down"][0]),
        ("down", SCANCODES["alt"][0]),
        ("up", SCANCODES["alt"][0]),
        ("up", SCANCODES["down"][0]),
    ]
    assert executor.stats.climbs == 1


def test_loot_taps_pickup_key(ex):
    executor, backend = ex
    executor.execute(fsm.Loot(key="z", taps=3), NOW)
    assert _taps(backend) == [SCANCODES["z"][0]] * 3
    assert executor.stats.loots == 1


def test_wait_sends_nothing(ex):
    executor, backend = ex
    executor.execute(fsm.Wait("test"), NOW)
    assert backend.history == []
