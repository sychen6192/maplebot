"""決策狀態機：驗證各種狀況下的優先權正確。"""
import pytest

from maplebot.brain import fsm
from maplebot.brain.state import GameState
from maplebot.config import AppCfg, BuffCfg, PotionCfg, Profile
from maplebot.vision.mobs import Mob

CENTER = (400, 260)
NOW = 1000.0


@pytest.fixture
def cfg():
    return AppCfg()


@pytest.fixture
def profile():
    p = Profile()
    p.patrol.waypoints_x = [40, 95]
    p.attack.key = "x"
    p.attack.range_px = 320
    p.attack.vertical_range_px = 90
    p.buffs = [BuffCfg(key="8", every=120, cast_seconds=1.5)]
    p.potions = {
        "hp": PotionCfg(key="pageup", below_ratio=0.5, cooldown=1.0),
        "mp": PotionCfg(key="pagedown", below_ratio=0.3, cooldown=1.0),
    }
    return p


def _state(hp=0.9, mp=0.9, player=(60, 30), others=(), mobs=()):
    return GameState(ts=NOW, hp=hp, mp=mp, player=player,
                     others=list(others), mobs=list(mobs))


def _rt(buffed=True):
    rt = fsm.Runtime()
    if buffed:
        rt.last_buff[0] = NOW - 10  # buff 還很新
    return rt


def _mob(cx, cy=260):
    return Mob(cx=cx, cy=cy, w=30, h=24, score=0.9, name="m")


def test_vision_failure_waits(cfg, profile):
    st = GameState(ts=NOW, hp=None, player=None)
    assert isinstance(fsm.decide(st, cfg, profile, _rt(), NOW, CENTER), fsm.Wait)


def test_critical_hp_panics(cfg, profile):
    action = fsm.decide(_state(hp=0.2), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Panic)


def test_low_hp_drinks_potion_before_anything(cfg, profile):
    st = _state(hp=0.4, mobs=[_mob(420)])
    action = fsm.decide(st, cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.DrinkPotion) and action.kind == "hp"


def test_potion_respects_cooldown(cfg, profile):
    rt = _rt()
    rt.note_potion("hp", NOW - 0.3)  # 剛喝過
    action = fsm.decide(_state(hp=0.4), cfg, profile, rt, NOW, CENTER)
    assert not isinstance(action, fsm.DrinkPotion)


def test_low_mp_drinks_mp_potion(cfg, profile):
    action = fsm.decide(_state(mp=0.1), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.DrinkPotion) and action.kind == "mp"


def test_other_players_pause(cfg, profile):
    st = _state(others=[(10, 10)], mobs=[_mob(420)])
    action = fsm.decide(st, cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Wait)


def test_other_players_ignored_when_disabled(cfg, profile):
    cfg.safety.pause_when_players = False
    st = _state(others=[(10, 10)], mobs=[_mob(420)])
    action = fsm.decide(st, cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Attack)


def test_buff_when_due(cfg, profile):
    rt = fsm.Runtime()  # 從未上過 buff
    action = fsm.decide(_state(), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.CastBuff) and action.key == "8"


def test_attack_nearest_mob_direction(cfg, profile):
    st = _state(mobs=[_mob(700), _mob(300)])  # 300 離中心 400 較近，在左邊
    action = fsm.decide(st, cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Attack)
    assert action.direction == -1


def test_mob_out_of_vertical_range_ignored(cfg, profile):
    st = _state(mobs=[_mob(420, cy=100)])  # 樓上的怪（差 160 > 90）
    action = fsm.decide(st, cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Move)


def test_patrol_moves_toward_waypoint(cfg, profile):
    action = fsm.decide(_state(player=(60, 30)), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Move)
    assert action.direction == -1 and action.target_x == 40  # 60 -> 40 往左


def test_patrol_advances_waypoint_on_arrival(cfg, profile):
    rt = _rt()
    action = fsm.decide(_state(player=(41, 30)), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.Wait)
    assert rt.wp_index == 1  # 切到下一個巡邏點
