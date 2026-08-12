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


def _state(hp=0.9, mp=0.9, minimap_xy=(60, 30), other_players=(), mobs=(), minimap_size=(130, 60)):
    return GameState(ts=NOW, hp=hp, mp=mp, minimap_xy=minimap_xy, minimap_size=minimap_size,
                     other_players=list(other_players), mobs=list(mobs))


def _rt(buffed=True):
    rt = fsm.Runtime()
    if buffed:
        rt.last_buff[0] = NOW - 10  # buff 還很新
    return rt


def _mob(cx, cy=260):
    return Mob(cx=cx, cy=cy, w=30, h=24, score=0.9, name="m")


def test_vision_failure_waits(cfg, profile):
    st = GameState(ts=NOW, hp=None, minimap_xy=None)
    assert isinstance(fsm.decide(st, cfg, profile, _rt(), NOW, CENTER), fsm.Wait)


def test_critical_hp_panics_after_confirming(cfg, profile):
    """低血要停機，但得先撐過搶救時間——停機本身不安全（角色會站著被打死）。"""
    rt = _rt()
    for _ in range(cfg.safety.critical_hp_frames - 1):
        assert not isinstance(fsm.decide(_state(hp=0.2), cfg, profile, rt, NOW, CENTER),
                              fsm.Panic)
    # 幀數夠了但時間還沒到：這段時間拿去灌藥搶救
    assert not isinstance(fsm.decide(_state(hp=0.2), cfg, profile, rt, NOW, CENTER),
                          fsm.Panic)
    later = NOW + cfg.safety.critical_hp_seconds
    action = fsm.decide(_state(hp=0.2), cfg, profile, rt, later, CENTER)
    assert isinstance(action, fsm.Panic)
    assert action.return_home is True   # 人可能回不來，該按回城卷


def test_low_hp_is_treated_first_and_only_panics_if_it_does_not_recover(cfg, profile):
    """血拉得回來就繼續打，不該因為「剛才低過」就停機。"""
    rt = _rt()
    fsm.decide(_state(hp=0.2), cfg, profile, rt, NOW, CENTER)
    fsm.decide(_state(hp=0.2), cfg, profile, rt, NOW + 1, CENTER)
    fsm.decide(_state(hp=0.8), cfg, profile, rt, NOW + 2, CENTER)      # 藥效上來了
    # 再次掉到低血時計時要重來，不能沿用上一段的起點直接停機
    action = fsm.decide(_state(hp=0.2), cfg, profile, rt,
                        NOW + 2 + cfg.safety.critical_hp_seconds, CENTER)
    assert not isinstance(action, fsm.Panic)


def test_critical_hp_seconds_zero_keeps_the_old_frame_only_behaviour(cfg, profile):
    """不想要搶救緩衝的人設 0 就回到原本「連續幾幀就停機」。"""
    cfg.safety.critical_hp_seconds = 0.0
    rt = _rt()
    for _ in range(cfg.safety.critical_hp_frames - 1):
        fsm.decide(_state(hp=0.2), cfg, profile, rt, NOW, CENTER)
    assert isinstance(fsm.decide(_state(hp=0.2), cfg, profile, rt, NOW, CENTER),
                      fsm.Panic)


def test_single_bad_hp_reading_does_not_stop_the_bot(cfg, profile):
    """血條被特效蓋住、擷取抖一下都會讀成 0——一幀就停機等於整晚白掛。"""
    rt = _rt()
    assert not isinstance(fsm.decide(_state(hp=0.0), cfg, profile, rt, NOW, CENTER),
                          fsm.Panic)
    # 下一幀讀回正常值就該當作沒事發生
    fsm.decide(_state(hp=0.99), cfg, profile, rt, NOW, CENTER)
    assert rt.low_hp_streak == 0
    assert not isinstance(fsm.decide(_state(hp=0.0), cfg, profile, rt, NOW, CENTER),
                          fsm.Panic)


def test_hp_frames_configurable(cfg, profile):
    cfg.safety.critical_hp_frames = 1        # 想要一幀就停也可以
    cfg.safety.critical_hp_seconds = 0.0     # 連搶救緩衝也不要
    assert isinstance(fsm.decide(_state(hp=0.2), cfg, profile, _rt(), NOW, CENTER),
                      fsm.Panic)


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
    st = _state(other_players=[(10, 10)], mobs=[_mob(420)])
    action = fsm.decide(st, cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Wait)


def test_other_players_ignored_when_disabled(cfg, profile):
    cfg.safety.pause_when_players = False
    st = _state(other_players=[(10, 10)], mobs=[_mob(420)])
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
    action = fsm.decide(_state(minimap_xy=(60, 30)), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Move)
    assert action.direction == -1 and action.target_x == 40  # 60 -> 40 往左


def test_patrol_advances_waypoint_on_arrival(cfg, profile):
    rt = _rt()
    action = fsm.decide(_state(minimap_xy=(41, 30)), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.Wait)
    assert rt.wp_index == 1  # 切到下一個巡邏點


def test_relative_waypoint_resolution(cfg, profile):
    profile.patrol.waypoints = [Waypoint(0.5)]  # 小地圖寬 130 -> x=65
    action = fsm.decide(_state(minimap_xy=(20, 30)), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Move)
    assert action.target_x == 65 and action.direction == 1


def test_waypoint_keys_run_on_arrival(cfg, profile):
    profile.patrol.waypoints = [Waypoint(40, keys=["alt"]), Waypoint(95)]
    rt = _rt()
    action = fsm.decide(_state(minimap_xy=(41, 30)), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.RunKeys)
    assert action.keys == ["alt"]
    assert rt.wp_index == 1  # 已切到下一點，keys 只會跑一次


def test_stuck_triggers_escape(cfg, profile):
    rt = _rt()
    pos = (60, 30)  # 離目標 40 還很遠，位置一直不動
    a1 = fsm.decide(_state(minimap_xy=pos), cfg, profile, rt, NOW, CENTER)
    assert isinstance(a1, fsm.Move)
    a2 = fsm.decide(_state(minimap_xy=pos), cfg, profile, rt, NOW + 2.0, CENTER)
    assert isinstance(a2, fsm.Move)  # 還沒超過 stuck_seconds
    a3 = fsm.decide(_state(minimap_xy=pos), cfg, profile, rt, NOW + 4.5, CENTER)
    assert isinstance(a3, fsm.Escape)
    assert a3.jump_key == "alt"
    a4 = fsm.decide(_state(minimap_xy=pos), cfg, profile, rt, NOW + 4.6, CENTER)
    assert isinstance(a4, fsm.Move)  # 脫困後重新計時，不會連發


def test_moving_player_never_escapes(cfg, profile):
    rt = _rt()
    for i, x in enumerate(range(90, 50, -5)):  # 一路有在動
        action = fsm.decide(_state(minimap_xy=(x, 30)), cfg, profile, rt,
                            NOW + i * 2.0, CENTER)
        assert isinstance(action, fsm.Move)


# ---- MP 門檻與自動撿物（參考「楓之谷達人」的功能表）----


def test_attack_skipped_when_mp_too_low(cfg, profile):
    """MP 不夠時技能放不出來，站著空揮不如繼續巡邏。

    mp=0.35 高於補魔線 0.3（所以不會先去喝水），但低於技能門檻 0.5。
    """
    profile.attack.min_mp = 0.5
    st = _state(mp=0.35, mobs=[_mob(420)])
    action = fsm.decide(st, cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Move)


def test_low_mp_drinks_before_gating_attack(cfg, profile):
    """MP 低到補魔線以下時，喝水優先於「跳過攻擊」。"""
    profile.attack.min_mp = 0.5
    action = fsm.decide(_state(mp=0.1, mobs=[_mob(420)]), cfg, profile,
                        _rt(), NOW, CENTER)
    assert isinstance(action, fsm.DrinkPotion) and action.kind == "mp"


def test_attack_proceeds_when_mp_sufficient(cfg, profile):
    profile.attack.min_mp = 0.2
    st = _state(mp=0.5, mobs=[_mob(420)])
    assert isinstance(fsm.decide(st, cfg, profile, _rt(), NOW, CENTER), fsm.Attack)


def test_mp_gate_ignored_when_mp_unreadable(cfg, profile):
    """MP 讀不到時照常施放——辨識失敗不該讓整隻 bot 停擺。"""
    profile.attack.min_mp = 0.2
    st = _state(mp=None, mobs=[_mob(420)])
    assert isinstance(fsm.decide(st, cfg, profile, _rt(), NOW, CENTER), fsm.Attack)


def test_buff_deferred_when_mp_too_low(cfg, profile):
    profile.buffs[0].min_mp = 0.5
    action = fsm.decide(_state(mp=0.1), cfg, profile, fsm.Runtime(), NOW, CENTER)
    assert not isinstance(action, fsm.CastBuff)


def test_loot_after_combat_when_no_mobs_left(cfg, profile):
    profile.loot.key = "z"
    rt = _rt()
    rt.last_attack = NOW - 1.0          # 剛打完
    action = fsm.decide(_state(), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.Loot)
    assert action.key == "z"


def test_loot_waits_until_mobs_cleared(cfg, profile):
    """範圍內還有怪就先打，撿東西時被圍毆不划算。"""
    profile.loot.key = "z"
    rt = _rt()
    rt.last_attack = NOW - 1.0
    action = fsm.decide(_state(mobs=[_mob(420)]), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.Attack)


def test_no_loot_long_after_combat(cfg, profile):
    """只是路過空地不要一直按撿取鍵。"""
    profile.loot.key = "z"
    profile.loot.after_combat = 6.0
    rt = _rt()
    rt.last_attack = NOW - 60.0
    assert isinstance(fsm.decide(_state(), cfg, profile, rt, NOW, CENTER), fsm.Move)


def test_loot_respects_interval(cfg, profile):
    profile.loot.key = "z"
    rt = _rt()
    rt.last_attack = NOW - 1.0
    first = fsm.decide(_state(), cfg, profile, rt, NOW, CENTER)
    assert isinstance(first, fsm.Loot)
    second = fsm.decide(_state(), cfg, profile, rt, NOW + 0.5, CENTER)
    assert not isinstance(second, fsm.Loot)      # 間隔沒到
    third = fsm.decide(_state(), cfg, profile, rt, NOW + 3.0, CENTER)
    assert isinstance(third, fsm.Loot)


def test_loot_disabled_without_key(cfg, profile):
    rt = _rt()
    rt.last_attack = NOW - 1.0
    assert isinstance(fsm.decide(_state(), cfg, profile, rt, NOW, CENTER), fsm.Move)


# ---- 多技能輪替（profile.skills）----


def _skills(profile, *specs):
    """specs: (key, cooldown, min_mobs, min_mp, range_px) 的序列，依優先權排。"""
    from maplebot.config import AttackCfg
    profile.skills = [
        AttackCfg(key=k, cooldown=cd, min_mobs=mm, min_mp=mp, range_px=rng,
                  vertical_range_px=90)
        for k, cd, mm, mp, rng in specs
    ]
    return profile


def test_prefers_first_ready_skill(cfg, profile):
    _skills(profile, ("v", 30.0, 1, 0.0, 400), ("x", 0.2, 1, 0.0, 320))
    action = fsm.decide(_state(mobs=[_mob(420)]), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Attack)
    assert action.key == "v" and action.index == 0


def test_falls_through_to_next_skill_on_cooldown(cfg, profile):
    _skills(profile, ("v", 30.0, 1, 0.0, 400), ("x", 0.2, 1, 0.0, 320))
    rt = _rt()
    rt.note_skill(0, NOW - 5.0)          # 大絕還在冷卻
    action = fsm.decide(_state(mobs=[_mob(420)]), cfg, profile, rt, NOW, CENTER)
    assert action.key == "x" and action.index == 1


def test_min_mobs_saves_aoe_for_crowds(cfg, profile):
    """30 秒冷卻的大絕別浪費在單隻蝸牛身上。"""
    _skills(profile, ("v", 30.0, 3, 0.0, 400), ("x", 0.2, 1, 0.0, 320))
    lone = fsm.decide(_state(mobs=[_mob(420)]), cfg, profile, _rt(), NOW, CENTER)
    assert lone.key == "x"               # 只有一隻 -> 用主攻

    crowd = _state(mobs=[_mob(380), _mob(420), _mob(460)])
    assert fsm.decide(crowd, cfg, profile, _rt(), NOW, CENTER).key == "v"


def test_skill_skipped_when_its_own_mp_gate_fails(cfg, profile):
    """大絕耗魔多、主攻耗魔少——MP 中等時應該退而求其次而不是完全不打。"""
    _skills(profile, ("v", 1.0, 1, 0.6, 400), ("x", 0.2, 1, 0.0, 320))
    action = fsm.decide(_state(mp=0.4, mobs=[_mob(420)]), cfg, profile,
                        _rt(), NOW, CENTER)
    assert action.key == "x"


def test_all_skills_unavailable_falls_back_to_patrol(cfg, profile):
    _skills(profile, ("v", 30.0, 1, 0.0, 400), ("x", 30.0, 1, 0.0, 320))
    rt = _rt()
    rt.note_skill(0, NOW - 1.0)
    rt.note_skill(1, NOW - 1.0)
    assert isinstance(fsm.decide(_state(mobs=[_mob(420)]), cfg, profile, rt,
                                 NOW, CENTER), fsm.Move)


def test_per_skill_range(cfg, profile):
    """遠程大絕構得到、近戰主攻構不到時，要選構得到的那個。"""
    _skills(profile, ("x", 0.2, 1, 0.0, 100), ("v", 0.2, 1, 0.0, 500))
    action = fsm.decide(_state(mobs=[_mob(750)]), cfg, profile, _rt(), NOW, CENTER)
    assert action.key == "v"             # 離中心 350，只有 range 500 的構得到


def test_cooldowns_are_tracked_per_skill(cfg, profile):
    _skills(profile, ("v", 30.0, 1, 0.0, 400), ("x", 0.2, 1, 0.0, 320))
    rt = _rt()
    rt.note_skill(1, NOW)                # 只有主攻剛放過
    action = fsm.decide(_state(mobs=[_mob(420)]), cfg, profile, rt, NOW, CENTER)
    assert action.key == "v"             # 大絕的冷卻不受影響


def test_single_attack_profile_still_works(cfg, profile):
    """沒設定 skills 的舊 profile 行為完全不變。"""
    assert profile.skills == []
    action = fsm.decide(_state(mobs=[_mob(420)]), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Attack) and action.key == "x" and action.index == 0


def test_loot_waits_for_any_skill_range(cfg, profile):
    """怪只在遠程技能範圍內時也不該先撿東西。"""
    _skills(profile, ("x", 0.2, 1, 0.0, 100), ("v", 30.0, 1, 0.0, 500))
    profile.loot.key = "z"
    rt = _rt()
    rt.note_skill(1, NOW - 1.0)          # 遠程還在冷卻
    rt.last_attack = NOW - 1.0
    action = fsm.decide(_state(mobs=[_mob(750)]), cfg, profile, rt, NOW, CENTER)
    assert not isinstance(action, fsm.Loot)


# ---- 自動巡邏（patrol.waypoints: auto）----


def _auto(profile):
    profile.patrol.auto = True
    profile.patrol.waypoints = []
    return profile


def _drive(cfg, profile, rt, xs):
    """依序餵入一連串小地圖 x，收集每個 tick 的決策。"""
    return [fsm.decide(_state(minimap_xy=(x, 30)), cfg, profile, rt, NOW + i * 0.5, CENTER)
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
    """按鍵沒生效時角色不會動，兩側量到同一個點——重試幾次後要吵出來。"""
    actions = _drive(cfg, _auto(profile), _rt(), [50] * 40)
    panics = [a for a in actions if isinstance(a, fsm.Panic)]
    assert panics, "角色完全不動卻沒有回報校正失敗"
    assert "probe_min_span_px" in panics[0].reason
    assert panics[0].return_home is False     # 設定錯誤不該燒掉一張回城卷


def test_auto_patrol_retries_before_giving_up(cfg, profile):
    """量到不合理的範圍多半是被打怪干擾，重來一次通常就正常了。"""
    prof = _auto(profile)
    prof.patrol.probe_retries = 2
    actions = _drive(cfg, prof, _rt(), [50] * 40)
    first_panic = next(i for i, a in enumerate(actions) if isinstance(a, fsm.Panic))
    # 放棄之前應該重試過好幾輪，而不是第一次量壞就停機
    assert first_panic > 12


def test_combat_gap_does_not_fake_a_wall(cfg, profile):
    """校正途中一定會被打怪插隊。兩次探邊隔了 3 秒不代表撞牆——
    那段時間角色在打怪，本來就不會前進。"""
    rt = _rt()
    prof = _auto(profile)
    st = _state(minimap_xy=(50, 30))

    a1 = fsm.decide(st, cfg, prof, rt, NOW, CENTER)
    assert isinstance(a1, fsm.Probe)
    # 中間打了 3 秒的怪，回來時位置沒變
    a2 = fsm.decide(st, cfg, prof, rt, NOW + 3.0, CENTER)
    assert isinstance(a2, fsm.Probe)
    assert a2.direction == a1.direction      # 還在往同一邊探，沒有誤判撞牆
    assert rt.auto.right is None


def test_wall_detected_when_genuinely_stuck(cfg, profile):
    """真的撞牆（持續探邊但位置不動）還是要判定得出來。"""
    rt = _rt()
    prof = _auto(profile)
    st = _state(minimap_xy=(50, 30))
    for i in range(6):                       # 連續探邊，每 0.4 秒一次
        fsm.decide(st, cfg, prof, rt, NOW + i * 0.4, CENTER)
    assert rt.auto.right == 50               # 這一側量到了


def test_auto_patrol_still_attacks_while_calibrating(cfg, profile):
    st = _state(minimap_xy=(60, 30), mobs=[_mob(420)])
    action = fsm.decide(st, cfg, _auto(profile), _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Attack)


# ---- 多層地圖（waypoint 的 y 軸與垂直移動）----


def test_waypoint_without_y_ignores_height(cfg, profile):
    """單層地圖的舊 profile 行為不變：y 差再多也算抵達。"""
    rt = _rt()
    action = fsm.decide(_state(minimap_xy=(41, 999)), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.Wait) and rt.wp_index == 1


def test_waypoint_tolerance_override(cfg, profile):
    """爬繩點的抓取窗口只有 ±1~2px：waypoint 可以覆寫 x 容差。
    全域 tolerance=4 時 x 差 2 已算到位，但 tolerance: 1 的點要走到差 1 以內。"""
    profile.patrol.waypoints = [Waypoint(40, y=20, tolerance=1)]
    action = fsm.decide(_state(minimap_xy=(42, 50)), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Move)          # 還差 2px：繼續對位
    action = fsm.decide(_state(minimap_xy=(41, 50)), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Climb)         # 差 1px：可以爬了


def test_climbs_up_when_x_aligned_but_y_too_low(cfg, profile):
    """起步的往上爬要帶跳：繩底常懸在半身高，站著按上永遠抓不到。"""
    profile.patrol.waypoints = [Waypoint(40, y=20)]
    rt = _rt()
    action = fsm.decide(_state(minimap_xy=(41, 50)), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.Climb)
    assert action.direction == -1 and action.key == "up"
    assert action.jump_key == profile.patrol.jump_key
    assert rt.climbing is True


def test_climb_up_stops_jumping_once_moving(cfg, profile):
    """已經離開起點高度＝抓著繩子在爬，就不要再跳——在繩上補跳會把角色
    震下來（實測三層繩就是這樣無限循環的）。爬升途中短暫停滯也一樣不跳。"""
    profile.patrol.waypoints = [Waypoint(40, y=20)]
    rt = _rt()
    fsm.decide(_state(minimap_xy=(41, 50)), cfg, profile, rt, NOW, CENTER)
    action = fsm.decide(_state(minimap_xy=(41, 44)), cfg, profile, rt, NOW + 0.5, CENTER)
    assert isinstance(action, fsm.Climb) and action.direction == -1
    assert action.jump_key == ""
    # 爬升途中 y 卡一拍（每步升幅本來就只比 stall_px 高一點）：耐心，不跳
    action = fsm.decide(_state(minimap_xy=(41, 44)), cfg, profile, rt, NOW + 1.0, CENTER)
    assert isinstance(action, fsm.Climb) and action.jump_key == ""


def test_climb_up_jumps_again_if_still_at_start_height(cfg, profile):
    """跳了卻還停在起點高度＝根本沒抓到（或抓了又掉回地板），要再跳。"""
    profile.patrol.waypoints = [Waypoint(40, y=20)]
    rt = _rt()
    fsm.decide(_state(minimap_xy=(41, 50)), cfg, profile, rt, NOW, CENTER)
    action = fsm.decide(_state(minimap_xy=(41, 50)), cfg, profile, rt, NOW + 0.5, CENTER)
    assert isinstance(action, fsm.Climb) and action.direction == -1
    assert action.jump_key == profile.patrol.jump_key


def test_descends_by_rope_by_default(cfg, profile):
    profile.patrol.waypoints = [Waypoint(40, y=60)]
    action = fsm.decide(_state(minimap_xy=(41, 20)), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Climb)
    assert action.direction == 1 and action.key == "down" and action.jump_key == ""


def test_descends_by_jump_when_requested(cfg, profile):
    profile.patrol.waypoints = [Waypoint(40, y=60, descend="jump")]
    action = fsm.decide(_state(minimap_xy=(41, 20)), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Climb) and action.jump_key == "alt"


def test_relative_waypoint_y_resolution(cfg, profile):
    profile.patrol.waypoints = [Waypoint(0.5, y=0.5)]  # 小地圖 130x60 -> (65, 30)
    action = fsm.decide(_state(minimap_xy=(65, 50)), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Climb) and action.direction == -1


def test_arrival_requires_both_axes(cfg, profile):
    profile.patrol.waypoints = [Waypoint(40, y=20), Waypoint(95)]
    rt = _rt()
    action = fsm.decide(_state(minimap_xy=(41, 22)), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.Wait)
    assert rt.wp_index == 1 and rt.climbing is False


def test_climb_stall_escapes_then_gives_up(cfg, profile):
    """y 一直不動 = 沒抓到繩子。先脫困重新對繩，重試用完就放棄這個點。"""
    profile.patrol.waypoints = [Waypoint(40, y=20), Waypoint(95)]
    profile.patrol.climb_retries = 1
    rt = _rt()
    pos = (41, 50)  # x 對準了，y 卻爬不動
    seq = [fsm.decide(_state(minimap_xy=pos), cfg, profile, rt, NOW + i, CENTER)
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
    action = fsm.decide(_state(minimap_xy=(60, 50)), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.Move)   # x 跑掉了 -> 先走回繩子下面
    assert rt.climbing is False
    assert rt.climb_retries == 1          # 但重試次數保留


def test_climbing_suppresses_attack(cfg, profile):
    """在繩子上按方向鍵會掉下來，所以爬升途中不轉向打怪。"""
    profile.patrol.waypoints = [Waypoint(40, y=20)]
    rt = _rt()
    rt.climbing = True
    st = _state(minimap_xy=(41, 50), mobs=[_mob(420)])
    assert isinstance(fsm.decide(st, cfg, profile, rt, NOW, CENTER), fsm.Climb)


def test_climbing_suppresses_buff(cfg, profile):
    profile.patrol.waypoints = [Waypoint(40, y=20)]
    rt = fsm.Runtime()  # buff 從沒上過，平常這時會 CastBuff
    rt.climbing = True
    action = fsm.decide(_state(minimap_xy=(41, 50)), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.Climb)


def test_climbing_still_drinks_potion(cfg, profile):
    """保命不讓路：掉下來也比死了好。"""
    profile.patrol.waypoints = [Waypoint(40, y=20)]
    rt = _rt()
    rt.climbing = True
    action = fsm.decide(_state(hp=0.4, minimap_xy=(41, 50)), cfg, profile, rt, NOW, CENTER)
    assert isinstance(action, fsm.DrinkPotion)


def test_endless_attack_on_something_that_never_dies_yields_to_patrol(cfg, profile):
    """寵物被當成怪最典型的症狀：牠不會死也不會離開攻擊範圍，
    於是每個 tick 都是 Attack，角色一步都不走、經驗也不會動。"""
    rt = _rt()
    st = _state(mobs=[_mob(400)])
    t = NOW
    while t < NOW + cfg.safety.attack_stall_seconds:
        assert isinstance(fsm.decide(st, cfg, profile, rt, t, CENTER), fsm.Attack)
        t += 0.5

    assert isinstance(fsm.decide(st, cfg, profile, rt, t, CENTER), fsm.Move)
    assert rt.attack_breaks == 1

    # 讓路只是暫時的，時間到就恢復攻擊
    t += cfg.safety.attack_break_seconds + 0.1
    assert isinstance(fsm.decide(st, cfg, profile, rt, t, CENTER), fsm.Attack)


def test_attacking_while_the_character_moves_is_never_interrupted(cfg, profile):
    """正常打怪本來就會邊打邊移動，不能被當成卡住。"""
    rt = _rt()
    t = NOW
    for i in range(60):
        st = _state(minimap_xy=(60 + i, 30), mobs=[_mob(400)])
        assert isinstance(fsm.decide(st, cfg, profile, rt, t, CENTER), fsm.Attack)
        t += 0.5
    assert rt.attack_breaks == 0


def test_attack_stall_check_can_be_disabled(cfg, profile):
    cfg.safety.attack_stall_seconds = 0
    rt = _rt()
    st = _state(mobs=[_mob(400)])
    t = NOW
    for _ in range(120):
        assert isinstance(fsm.decide(st, cfg, profile, rt, t, CENTER), fsm.Attack)
        t += 0.5


def test_gap_without_targets_restarts_the_attack_timer(cfg, profile):
    """打一下、怪死了、隔很久又有新怪出現，不該把中間的空檔算成連續攻擊。"""
    rt = _rt()
    t = NOW
    fsm.decide(_state(mobs=[_mob(400)]), cfg, profile, rt, t, CENTER)
    t += cfg.safety.attack_stall_seconds * 2
    fsm.decide(_state(mobs=[]), cfg, profile, rt, t, CENTER)      # 沒怪，計時重來
    assert isinstance(fsm.decide(_state(mobs=[_mob(400)]), cfg, profile, rt, t, CENTER),
                      fsm.Attack)
    assert rt.attack_breaks == 0


def _wide_mob(cx, cy=260, w=30, h=24):
    return Mob(cx=cx, cy=cy, w=w, h=h, score=0.9, name="m")


def test_a_big_mob_at_the_edge_is_attackable(cfg, profile):
    """體型大的怪：中心在攻擊範圍外，身體壓在範圍裡——實際上打得到。

    只比中心點的話會放過牠（「明明貼在臉上卻不打」）。
    """
    profile.attack.range_px = 100
    edge = _wide_mob(cx=CENTER[0] + 130, w=120)      # 中心超出 30px，但半寬 60
    action = fsm.decide(_state(mobs=[edge]), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Attack)


def test_a_mob_fully_outside_is_still_ignored(cfg, profile):
    profile.attack.range_px = 100
    far = _wide_mob(cx=CENTER[0] + 400, w=40)
    action = fsm.decide(_state(mobs=[far]), cfg, profile, _rt(), NOW, CENTER)
    assert not isinstance(action, fsm.Attack)


def test_vertical_range_also_counts_the_body(cfg, profile):
    profile.attack.vertical_range_px = 40
    tall = _wide_mob(cx=CENTER[0], cy=CENTER[1] + 60, h=80)
    action = fsm.decide(_state(mobs=[tall]), cfg, profile, _rt(), NOW, CENTER)
    assert isinstance(action, fsm.Attack)


def test_a_mob_on_another_platform_is_still_ignored(cfg, profile):
    profile.attack.vertical_range_px = 40
    upstairs = _wide_mob(cx=CENTER[0], cy=CENTER[1] - 200, h=30)
    action = fsm.decide(_state(mobs=[upstairs]), cfg, profile, _rt(), NOW, CENTER)
    assert not isinstance(action, fsm.Attack)


def test_attack_range_scales_with_the_window(cfg, profile):
    """range_px 是畫面像素：同一個 profile 換個視窗大小，攻擊範圍不該跟著變。

    1920 視窗填 320 只等於 800x600 時代的 133px——短劍剛好還算合理，
    但那是巧合。實際世界距離必須一致。
    """
    profile.attack.range_px = 320
    mob_at = 300      # 基準畫面下在範圍內
    small = _state(mobs=[Mob(cx=mob_at, cy=260, w=10, h=10, score=1, name="m")])
    assert isinstance(fsm.decide(small, cfg, profile, _rt(), NOW, (395, 260)),
                      fsm.Attack)

    # 同一個世界位置，在 2.4 倍寬的畫面上像素距離也是 2.4 倍
    big_center = (int(395 * 2.4), 260)
    far = _state(mobs=[Mob(cx=int(big_center[0] + (mob_at - 395) * 2.4), cy=260,
                           w=10, h=10, score=1, name="m")])
    assert isinstance(fsm.decide(far, cfg, profile, _rt(), NOW, big_center),
                      fsm.Attack)


def test_out_of_range_stays_out_of_range_at_any_window_size(cfg, profile):
    profile.attack.range_px = 100
    for center_x, scale in ((395, 1.0), (948, 2.4)):
        beyond = int(center_x + 200 * scale)
        st = _state(mobs=[Mob(cx=beyond, cy=260, w=10, h=10, score=1, name="m")])
        action = fsm.decide(st, cfg, profile, _rt(), NOW, (center_x, 260))
        assert not isinstance(action, fsm.Attack), f"scale {scale}"


def test_auto_scale_can_be_turned_off(cfg, profile):
    """關掉之後 range_px 就是這個視窗的實際像素。"""
    profile.attack_auto_scale = False
    profile.attack.range_px = 320
    far = _state(mobs=[Mob(cx=948 + 400, cy=260, w=10, h=10, score=1, name="m")])
    assert not isinstance(fsm.decide(far, cfg, profile, _rt(), NOW, (948, 260)),
                          fsm.Attack)


def test_attack_scale_helper():
    assert fsm.attack_scale(790) == pytest.approx(1.0)
    assert fsm.attack_scale(1580) == pytest.approx(2.0)
    assert fsm.attack_scale(1580, enabled=False) == 1.0
    assert fsm.attack_scale(0) == 1.0


def test_attack_range_follows_the_character_not_the_screen(cfg, profile):
    """鏡頭有跟隨延遲，角色常常不在畫面正中央。

    範圍框沒跟著角色走的話，一邊構得到卻不打、另一邊打不到卻猛揮。
    """
    profile.attack.range_px = 100
    st = _state(mobs=[Mob(cx=CENTER[0] + 250, cy=CENTER[1], w=20, h=20,
                          score=1, name="m")])
    assert not isinstance(fsm.decide(st, cfg, profile, _rt(), NOW, CENTER), fsm.Attack)

    st.screen_xy = (CENTER[0] + 200, CENTER[1])   # 角色其實偏右 200px
    assert isinstance(fsm.decide(st, cfg, profile, _rt(), NOW, CENTER), fsm.Attack)


def test_the_range_is_symmetric_around_the_character(cfg, profile):
    """角色偏右時，牠左邊的怪也要照樣打得到——範圍是繞著角色，不是繞著畫面。"""
    profile.attack.range_px = 100
    player = (CENTER[0] + 200, CENTER[1])
    for side in (-1, 1):
        st = _state(mobs=[Mob(cx=player[0] + side * 80, cy=player[1],
                              w=20, h=20, score=1, name="m")])
        st.screen_xy = player
        assert isinstance(fsm.decide(st, cfg, profile, _rt(), NOW, CENTER),
                          fsm.Attack), side


def test_falls_back_to_the_screen_centre_without_a_party_bar(cfg, profile):
    st = _state(mobs=[_mob(CENTER[0] + 10)])
    assert st.screen_xy is None
    assert isinstance(fsm.decide(st, cfg, profile, _rt(), NOW, CENTER), fsm.Attack)


def _floor_profile():
    """兩個巡邏點都綁在地面層（y=90），站太高就往下跳。"""
    p = Profile()
    p.patrol.waypoints = [Waypoint(x=12, y=90, descend="jump"),
                          Waypoint(x=130, y=90, descend="jump")]
    p.patrol.y_tolerance = 3
    return p


def test_drops_down_without_waiting_to_line_up_x():
    """站上路邊的小平台後，x 通常也走不動——等對準 x 才處理等於永遠不處理。

    脫困跳躍會把角色送上小木屋屋頂，站在上面離地面的怪 250px，只能空揮。
    """
    cfg, p = AppCfg(), _floor_profile()
    rt = fsm.Runtime()
    state = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(104, 80))   # 高一層、x 差很遠
    action = fsm.decide(state, cfg, p, rt, 0.0, (400, 300))
    assert isinstance(action, fsm.Climb), action
    assert action.direction == 1               # 往下
    assert action.jump_key                     # 下跳平台


def test_still_lines_up_x_before_climbing_a_rope():
    """往上爬一定要先站到繩子前面，這條不能被上面那個規則吃掉。"""
    cfg = AppCfg()
    p = Profile()
    p.patrol.waypoints = [Waypoint(x=68, y=20)]
    rt = fsm.Runtime()
    state = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(30, 90))
    assert isinstance(fsm.decide(state, cfg, p, rt, 0.0, (400, 300)), fsm.Move)


def test_rope_descent_still_needs_x_first():
    """descend: rope 是抓著繩子下降，站在別的地方按下鍵沒有用。"""
    cfg = AppCfg()
    p = Profile()
    p.patrol.waypoints = [Waypoint(x=68, y=90, descend="rope")]
    rt = fsm.Runtime()
    state = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(104, 80))
    assert isinstance(fsm.decide(state, cfg, p, rt, 0.0, (400, 300)), fsm.Move)


def test_on_the_patrol_floor_it_just_walks():
    cfg, p = AppCfg(), _floor_profile()
    rt = fsm.Runtime()
    state = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(104, 90))
    assert isinstance(fsm.decide(state, cfg, p, rt, 0.0, (400, 300)), fsm.Move)


def _chase_profile(chase_px=250):
    p = Profile()
    p.patrol.waypoints = [Waypoint(x=12), Waypoint(x=130)]
    p.attack.range_px = 55
    p.attack.vertical_range_px = 35
    p.chase_px = chase_px
    return p


def _mob_at(cx, cy, w=40, h=40):
    return Mob(cx=cx, cy=cy, w=w, h=h, score=1.0, name="m")


def test_walks_towards_a_mob_it_cannot_reach_yet():
    """看得到怪卻構不到時，原本會照巡邏路線走掉——畫面上站著五隻一隻都沒打。"""
    cfg, p = AppCfg(), _chase_profile()
    state = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(70, 90),
                      mobs=[_mob_at(600, 300)])          # 中心右邊 200px，打不到
    action = fsm.decide(state, cfg, p, fsm.Runtime(), NOW, (400, 300))
    assert isinstance(action, fsm.Chase), action
    assert action.direction == 1


def test_chase_prefers_the_nearest_mob():
    cfg, p = AppCfg(), _chase_profile()
    state = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(70, 90),
                      mobs=[_mob_at(600, 300), _mob_at(280, 300)])
    action = fsm.decide(state, cfg, p, fsm.Runtime(), NOW, (400, 300))
    assert isinstance(action, fsm.Chase) and action.direction == -1


def test_does_not_chase_mobs_on_another_floor():
    """樓上樓下的怪追過去也打不到，只會被拉離巡邏路線。"""
    cfg, p = AppCfg(), _chase_profile()
    state = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(70, 90),
                      mobs=[_mob_at(600, 60)])           # 水平構得到、但高很多
    assert isinstance(fsm.decide(state, cfg, p, fsm.Runtime(), NOW, (400, 300)),
                      fsm.Move)


def test_does_not_chase_beyond_the_limit():
    cfg, p = AppCfg(), _chase_profile(chase_px=100)
    state = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(70, 90),
                      mobs=[_mob_at(760, 300)])
    assert isinstance(fsm.decide(state, cfg, p, fsm.Runtime(), NOW, (400, 300)),
                      fsm.Move)


def test_chase_off_by_default_keeps_the_old_behaviour():
    cfg, p = AppCfg(), _chase_profile(chase_px=0)
    state = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(70, 90),
                      mobs=[_mob_at(600, 300)])
    assert isinstance(fsm.decide(state, cfg, p, fsm.Runtime(), NOW, (400, 300)),
                      fsm.Move)


def test_chase_commitment_survives_detection_flicker():
    """信心邊緣的怪一幀有一幀沒有：沒有承諾期的話，追擊和巡邏會每 tick
    互搶方向（往左、往右、往左…），角色原地抖動，實測 150 秒只打了 2 刀。"""
    cfg, p = AppCfg(), _chase_profile()
    rt = fsm.Runtime()
    st = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(70, 90),
                   mobs=[_mob_at(280, 300)])             # 左邊的怪 -> 往左追
    first = fsm.decide(st, cfg, p, rt, NOW, (400, 300))
    assert isinstance(first, fsm.Chase) and first.direction == -1

    # 下一幀怪閃沒了：承諾期內要照原方向繼續走，不能回頭照巡邏路線走掉
    gone = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(70, 90), mobs=[])
    action = fsm.decide(gone, cfg, p, rt, NOW + 0.13, (400, 300))
    assert isinstance(action, fsm.Chase) and action.direction == -1

    # 換一幀變成右邊有怪（偵測抖動/多隻交錯）：承諾期內方向也不變
    swapped = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(70, 90),
                        mobs=[_mob_at(600, 300)])
    action = fsm.decide(swapped, cfg, p, rt, NOW + 0.26, (400, 300))
    assert isinstance(action, fsm.Chase) and action.direction == -1


def test_chase_stops_at_route_right_edge():
    """追怪不能追出路線端點太多：訓練場 I 右邊 x≈245 是自動傳送門，
    實測追擊一路衝過門把自己傳去隔壁圖。"""
    cfg, p = AppCfg(), _chase_profile()          # 巡邏點 12..130
    state = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(140, 90),
                      mobs=[_mob_at(600, 300)])  # 右邊有怪，但已在邊界上
    action = fsm.decide(state, cfg, p, fsm.Runtime(), NOW, (400, 300))
    assert isinstance(action, fsm.Move)


def test_chase_stops_at_route_left_edge():
    cfg, p = AppCfg(), _chase_profile()
    state = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(2, 90),
                      mobs=[_mob_at(100, 300)])  # 怪在畫面左側
    action = fsm.decide(state, cfg, p, fsm.Runtime(), NOW, (400, 300))
    assert isinstance(action, fsm.Move)


def test_chase_inside_bounds_still_works():
    cfg, p = AppCfg(), _chase_profile()
    state = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(120, 90),
                      mobs=[_mob_at(600, 300)])
    action = fsm.decide(state, cfg, p, fsm.Runtime(), NOW, (400, 300))
    assert isinstance(action, fsm.Chase) and action.direction == 1


def test_chase_commitment_expires_back_to_patrol():
    cfg, p = AppCfg(), _chase_profile()
    rt = fsm.Runtime()
    st = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(70, 90),
                   mobs=[_mob_at(280, 300)])
    fsm.decide(st, cfg, p, rt, NOW, (400, 300))
    gone = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(70, 90), mobs=[])
    action = fsm.decide(gone, cfg, p, rt,
                        NOW + fsm.CHASE_COMMIT_SECONDS + 0.1, (400, 300))
    assert isinstance(action, fsm.Move)


def test_attacking_still_wins_over_chasing():
    cfg, p = AppCfg(), _chase_profile()
    state = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(70, 90),
                      mobs=[_mob_at(430, 300), _mob_at(600, 300)])
    assert isinstance(fsm.decide(state, cfg, p, fsm.Runtime(), NOW, (400, 300)),
                      fsm.Attack)


def _potion_profile():
    p = Profile()
    p.patrol.waypoints = [Waypoint(x=12)]
    p.potions = {
        "hp": PotionCfg(key="s", below_ratio=0.7, cooldown=0.5),
        "hp_emergency": PotionCfg(key="d", below_ratio=0.35, cooldown=1.0),
    }
    return p


def test_emergency_potion_takes_over_when_hp_is_really_low():
    """一般補血一瓶只回一點點——實測 530 血一瓶回 44，被圍住時每秒一瓶
    追不上掉血速度，就會一路掉到危險線停機。血更低時要改按大瓶。"""
    cfg, p = AppCfg(), _potion_profile()
    state = GameState(ts=0.0, hp=0.30, mp=1.0, minimap_xy=(70, 90))
    action = fsm.decide(state, cfg, p, fsm.Runtime(), NOW, (400, 300))
    assert isinstance(action, fsm.DrinkPotion)
    assert action.key == "d"


def test_normal_potion_used_when_hp_is_only_a_bit_low():
    cfg, p = AppCfg(), _potion_profile()
    state = GameState(ts=0.0, hp=0.55, mp=1.0, minimap_xy=(70, 90))
    action = fsm.decide(state, cfg, p, fsm.Runtime(), NOW, (400, 300))
    assert isinstance(action, fsm.DrinkPotion) and action.key == "s"


def test_the_two_potions_have_separate_cooldowns():
    """大瓶剛喝過不代表小瓶也要等——各自算冷卻，才不會兩邊互相卡住。"""
    cfg, p = AppCfg(), _potion_profile()
    rt = fsm.Runtime()
    rt.note_potion("hp_emergency", NOW)
    state = GameState(ts=0.0, hp=0.30, mp=1.0, minimap_xy=(70, 90))
    action = fsm.decide(state, cfg, p, rt, NOW + 0.6, (400, 300))
    assert isinstance(action, fsm.DrinkPotion) and action.key == "s"


def test_no_emergency_potion_configured_keeps_the_old_behaviour():
    cfg = AppCfg()
    p = _potion_profile()
    del p.potions["hp_emergency"]
    state = GameState(ts=0.0, hp=0.30, mp=1.0, minimap_xy=(70, 90))
    action = fsm.decide(state, cfg, p, fsm.Runtime(), NOW, (400, 300))
    assert isinstance(action, fsm.DrinkPotion) and action.key == "s"


def test_ranged_class_backs_off_when_a_mob_gets_too_close():
    """遠程職業站樁輸出，怪貼到臉上只會挨打——先退開，下個 tick 就打得到了。"""
    cfg, p = AppCfg(), _chase_profile()
    p.attack.range_px = 150          # 遠程：射程長
    p.keep_away_px = 60
    state = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(70, 90),
                      mobs=[_mob_at(430, 300)])          # 中心右邊 30px，太近
    action = fsm.decide(state, cfg, p, fsm.Runtime(), NOW, (400, 300))
    assert isinstance(action, fsm.Chase) and action.away
    assert action.direction == -1                        # 往反方向退


def test_ranged_class_attacks_once_it_has_room():
    cfg, p = AppCfg(), _chase_profile()
    p.attack.range_px = 150
    p.keep_away_px = 60
    state = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(70, 90),
                      mobs=[_mob_at(520, 300)])          # 120px 遠，夠開
    assert isinstance(fsm.decide(state, cfg, p, fsm.Runtime(), NOW, (400, 300)),
                      fsm.Attack)


def test_melee_class_never_backs_off():
    """近戰貼臉本來就是它要的，keep_away_px 設 0 關掉。"""
    cfg, p = AppCfg(), _chase_profile()
    p.keep_away_px = 0
    state = GameState(ts=0.0, hp=1.0, mp=1.0, minimap_xy=(70, 90),
                      mobs=[_mob_at(410, 300)])
    assert isinstance(fsm.decide(state, cfg, p, fsm.Runtime(), NOW, (400, 300)),
                      fsm.Attack)
