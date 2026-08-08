"""決策狀態機：驗證各種狀況下的優先權正確。"""
import pytest

from maplebot.brain import fsm
from maplebot.brain.state import GameState
from maplebot.config import AppCfg, BuffCfg, PotionCfg, Profile, Waypoint
from maplebot.vision.mobs import Mob

CENTER = (400, 260)
NOW = 1000.0


@pytest.fixture
def cfg():
    return AppCfg()


@pytest.fixture
def profile():
    p = Profile()
    p.patrol.waypoints = [Waypoint(40), Waypoint(95)]
    p.attack.key = "x"
    p.attack.range_px = 320
    p.attack.vertical_range_px = 90
    p.buffs = [BuffCfg(key="8", every=120, cast_seconds=1.5)]
    p.potions = {
        "hp": PotionCfg(key="pageup", below_ratio=0.5, cooldown=1.0),
        "mp": PotionCfg(key="pagedown", below_ratio=0.3, cooldown=1.0),
    }
    return p


def _state(hp=0.9, mp=0.9, player=(60, 30), others=(), mobs=(), minimap_size=(130, 60)):
    return GameState(ts=NOW, hp=hp, mp=mp, player=player, minimap_size=minimap_size,
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
    assert action.return_home is True   # 人可能回不來，該按回城卷


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
    assert action.aoe is False


def test_aoe_attack_flag(cfg, profile):
    profile.attack.type = "aoe"
    action = fsm.decide(_state(mobs=[_mob(420)]), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Attack) and action.aoe is True


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


def test_relative_waypoint_resolution(cfg, profile):
    profile.patrol.waypoints = [Waypoint(0.5)]  # 小地圖寬 130 -> x=65
    action = fsm.decide(_state(player=(20, 30)), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Move)
    assert action.target_x == 65 and action.direction == 1


def test_waypoint_keys_run_on_arrival(cfg, profile):
    profile.patrol.waypoints = [Waypoint(40, keys=["alt"]), Waypoint(95)]
    rt = _rt()
    action = fsm.decide(_state(player=(41, 30)), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.RunKeys)
    assert action.keys == ["alt"]
    assert rt.wp_index == 1  # 已切到下一點，keys 只會跑一次


def test_stuck_triggers_escape(cfg, profile):
    rt = _rt()
    pos = (60, 30)  # 離目標 40 還很遠，位置一直不動
    a1 = fsm.decide(_state(player=pos), cfg, profile, rt, NOW, CENTER)
    assert isinstance(a1, fsm.Move)
    a2 = fsm.decide(_state(player=pos), cfg, profile, rt, NOW + 2.0, CENTER)
    assert isinstance(a2, fsm.Move)  # 還沒超過 stuck_seconds
    a3 = fsm.decide(_state(player=pos), cfg, profile, rt, NOW + 4.5, CENTER)
    assert isinstance(a3, fsm.Escape)
    assert a3.jump_key == "alt"
    a4 = fsm.decide(_state(player=pos), cfg, profile, rt, NOW + 4.6, CENTER)
    assert isinstance(a4, fsm.Move)  # 脫困後重新計時，不會連發


def test_moving_player_never_escapes(cfg, profile):
    rt = _rt()
    for i, x in enumerate(range(90, 50, -5)):  # 一路有在動
        action = fsm.decide(_state(player=(x, 30)), cfg, profile, rt,
                            NOW + i * 2.0, CENTER)
        assert isinstance(action, fsm.Move)


# ---- 自動巡邏（patrol.waypoints: auto）----


def _auto(profile):
    profile.patrol.auto = True
    profile.patrol.waypoints = []
    return profile


def _drive(cfg, profile, rt, xs):
    """依序餵入一連串小地圖 x，收集每個 tick 的決策。"""
    return [fsm.decide(_state(player=(x, 30)), cfg, profile, rt, NOW + i * 0.5, CENTER)
            for i, x in enumerate(xs)]


def test_auto_patrol_probes_both_walls_then_patrols(cfg, profile):
    rt = _rt()
    xs = [60, 70, 90, 90, 90, 90,   # 往右走，撞牆後連續 3 次沒動 -> 記下右界
          90, 80, 20, 20, 20, 20]   # 換方向往左，同樣撞牆 -> 記下左界
    actions = _drive(cfg, _auto(profile), rt, xs)
    assert all(isinstance(a, fsm.Probe) for a in actions[:11])
    assert actions[0].direction == 1
    assert actions[5].direction == -1                       # 右邊到底了就換方向
    assert [w.x for w in rt.auto.waypoints] == [26, 84]     # 左右各內縮 6px
    assert isinstance(actions[11], fsm.Move) and actions[11].target_x == 26


def test_auto_patrol_panics_when_range_too_small(cfg, profile):
    """按鍵沒生效時角色不會動，兩側量到同一個點——要吵出來，不能默默站樁。"""
    actions = _drive(cfg, _auto(profile), _rt(),
                     [50, 50, 50, 50, 50, 45, 45, 45, 45])
    assert isinstance(actions[-1], fsm.Panic)
    assert "probe_min_span_px" in actions[-1].reason
    assert actions[-1].return_home is False   # 設定錯誤不該燒掉一張回城卷


def test_auto_patrol_still_attacks_while_calibrating(cfg, profile):
    st = _state(player=(60, 30), mobs=[_mob(420)])
    action = fsm.decide(st, cfg, _auto(profile), _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Attack)


# ---- 多層地圖（waypoint 的 y 軸與垂直移動）----


def test_waypoint_without_y_ignores_height(cfg, profile):
    """單層地圖的舊 profile 行為不變：y 差再多也算抵達。"""
    rt = _rt()
    action = fsm.decide(_state(player=(41, 999)), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.Wait) and rt.wp_index == 1


def test_climbs_up_when_x_aligned_but_y_too_low(cfg, profile):
    profile.patrol.waypoints = [Waypoint(40, y=20)]
    rt = _rt()
    action = fsm.decide(_state(player=(41, 50)), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.Climb)
    assert action.direction == -1 and action.key == "up" and action.jump_key == ""
    assert rt.climbing is True


def test_descends_by_rope_by_default(cfg, profile):
    profile.patrol.waypoints = [Waypoint(40, y=60)]
    action = fsm.decide(_state(player=(41, 20)), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Climb)
    assert action.direction == 1 and action.key == "down" and action.jump_key == ""


def test_descends_by_jump_when_requested(cfg, profile):
    profile.patrol.waypoints = [Waypoint(40, y=60, descend="jump")]
    action = fsm.decide(_state(player=(41, 20)), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Climb) and action.jump_key == "alt"


def test_relative_waypoint_y_resolution(cfg, profile):
    profile.patrol.waypoints = [Waypoint(0.5, y=0.5)]  # 小地圖 130x60 -> (65, 30)
    action = fsm.decide(_state(player=(65, 50)), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Climb) and action.direction == -1


def test_arrival_requires_both_axes(cfg, profile):
    profile.patrol.waypoints = [Waypoint(40, y=20), Waypoint(95)]
    rt = _rt()
    action = fsm.decide(_state(player=(41, 22)), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.Wait)
    assert rt.wp_index == 1 and rt.climbing is False


def test_climb_stall_escapes_then_gives_up(cfg, profile):
    """y 一直不動 = 沒抓到繩子。先脫困重新對繩，重試用完就放棄這個點。"""
    profile.patrol.waypoints = [Waypoint(40, y=20), Waypoint(95)]
    profile.patrol.climb_retries = 1
    rt = _rt()
    pos = (41, 50)  # x 對準了，y 卻爬不動
    seq = [fsm.decide(_state(player=pos), cfg, profile, rt, NOW + i, CENTER)
           for i in range(8)]
    assert [type(a).__name__ for a in seq] == [
        "Climb", "Climb", "Climb", "Escape",   # 第 1 次重試
        "Climb", "Climb", "Climb", "Wait",     # 重試用完 -> 放棄
    ]
    assert rt.wp_index == 1
    assert rt.climb_retries == 0  # 換點後重新計數


def test_climb_retries_survive_realignment(cfg, profile):
    """脫困會把位置撞歪；走回去對位時不能把重試次數歸零，否則永遠放棄不了。"""
    profile.patrol.waypoints = [Waypoint(40, y=20), Waypoint(95)]
    rt = _rt()
    rt.climbing = True
    rt.climb_retries = 1
    action = fsm.decide(_state(player=(60, 50)), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.Move)   # x 跑掉了 -> 先走回繩子下面
    assert rt.climbing is False
    assert rt.climb_retries == 1          # 但重試次數保留


def test_climbing_suppresses_attack(cfg, profile):
    """在繩子上按方向鍵會掉下來，所以爬升途中不轉向打怪。"""
    profile.patrol.waypoints = [Waypoint(40, y=20)]
    rt = _rt()
    rt.climbing = True
    st = _state(player=(41, 50), mobs=[_mob(420)])
    assert isinstance(fsm.decide(st, cfg, profile, rt, NOW, CENTER), fsm.Climb)


def test_climbing_suppresses_buff(cfg, profile):
    profile.patrol.waypoints = [Waypoint(40, y=20)]
    rt = fsm.Runtime()  # buff 從沒上過，平常這時會 CastBuff
    rt.climbing = True
    action = fsm.decide(_state(player=(41, 50)), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.Climb)


def test_climbing_still_drinks_potion(cfg, profile):
    """保命不讓路：掉下來也比死了好。"""
    profile.patrol.waypoints = [Waypoint(40, y=20)]
    rt = _rt()
    rt.climbing = True
    action = fsm.decide(_state(hp=0.4, player=(41, 50)), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.DrinkPotion)
