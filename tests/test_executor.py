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


def test_move_taps_direction(ex):
    executor, backend = ex
    executor.execute(fsm.Move(direction=1, seconds=0.3, target_x=95), NOW)
    assert _taps(backend) == [SCANCODES["right"][0]]


def test_wait_sends_nothing(ex):
    executor, backend = ex
    executor.execute(fsm.Wait("test"), NOW)
    assert backend.history == []
