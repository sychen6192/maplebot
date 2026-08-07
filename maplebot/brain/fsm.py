"""決策核心：純函式的優先權狀態機。

decide() 不碰鍵盤、不碰螢幕、不看時鐘（now 由外部傳入），
所以整個打怪邏輯可以用單元測試完整驗證。

優先權（高到低）：
  1. 視覺異常          -> Wait（交給 runner 的 watchdog 計時）
  2. HP 低於危險線      -> Panic（停止程式並截圖；可設定先按回城卷）
  3. HP/MP 低於補藥線   -> DrinkPotion
  4. 小地圖出現其他玩家  -> Wait（禮貌暫停，pause_when_players 可關）
  5. Buff 到期          -> CastBuff
  6. 攻擊範圍內有怪      -> Attack（directional 先轉向 / aoe 原地放）
  7. 巡邏中卡住          -> Escape（跳躍 + 換方向，參考 MapleStoryAutoLevelUp）
  8. 其他               -> Move 往下一個巡邏點；抵達時執行該點的 keys

楓谷的鏡頭永遠跟著角色，所以「角色在 playfield 的位置」直接用
playfield 中心近似，怪物距離就是與中心的距離。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

from ..config import AppCfg, Profile, Waypoint
from .state import GameState


@dataclass
class Wait:
    reason: str
    seconds: float = 0.4


@dataclass
class Panic:
    reason: str


@dataclass
class DrinkPotion:
    kind: str
    key: str


@dataclass
class CastBuff:
    index: int
    key: str
    cast_seconds: float


@dataclass
class Attack:
    direction: int  # -1 左 / 1 右
    key: str
    cast_seconds: float
    repeat: int
    aoe: bool = False


@dataclass
class Move:
    direction: int
    seconds: float
    target_x: int


@dataclass
class RunKeys:
    """抵達巡邏點時要敲的按鍵序列（跳上平台、放置型技能等）。"""
    keys: List[str]


@dataclass
class Escape:
    """卡住脫困：往 direction 跳一下。"""
    direction: int
    jump_key: str


Action = Union[Wait, Panic, DrinkPotion, CastBuff, Attack, Move, RunKeys, Escape]


@dataclass
class Runtime:
    """跨 tick 的決策記憶：冷卻時間、巡邏進度、卡住偵測。"""
    wp_index: int = 0
    last_buff: Dict[int, float] = field(default_factory=dict)
    last_potion: Dict[str, float] = field(default_factory=dict)
    last_attack: float = 0.0
    stuck_pos: Optional[Tuple[int, int]] = None
    stuck_since: float = 0.0
    escape_direction: int = 1

    def note_buff(self, index: int, now: float) -> None:
        self.last_buff[index] = now

    def note_potion(self, kind: str, now: float) -> None:
        self.last_potion[kind] = now


def _potion_due(rt: Runtime, kind: str, cooldown: float, now: float) -> bool:
    return now - rt.last_potion.get(kind, 0.0) >= cooldown


def resolve_waypoint_x(wp: Waypoint, minimap_size: Optional[Tuple[int, int]]) -> int:
    """<=1 的值代表佔小地圖寬度的比例（參考 auto-maple 的相對座標），
    小地圖尺寸改變時巡邏點不用重寫。"""
    if wp.x <= 1.0 and minimap_size:
        return int(round(wp.x * minimap_size[0]))
    return int(round(wp.x))


def _check_stuck(rt: Runtime, player: Tuple[int, int], now: float,
                 stuck_px: int, stuck_seconds: float) -> bool:
    """巡邏移動中位置長時間沒變就視為卡住（地形卡死、被彈回）。"""
    if rt.stuck_pos is None or \
            abs(player[0] - rt.stuck_pos[0]) + abs(player[1] - rt.stuck_pos[1]) > stuck_px:
        rt.stuck_pos = player
        rt.stuck_since = now
        return False
    if now - rt.stuck_since >= stuck_seconds:
        rt.stuck_since = now  # 重新計時，避免連續觸發
        return True
    return False


def decide(state: GameState, cfg: AppCfg, profile: Profile, rt: Runtime,
           now: float, playfield_center: Tuple[int, int]) -> Action:
    if not state.vision_ok:
        return Wait("讀不到畫面狀態（HP 條或小地圖玩家點）")

    assert state.hp is not None and state.player is not None
    center_x, center_y = playfield_center

    if state.hp <= cfg.safety.critical_hp_ratio:
        return Panic(f"HP 剩 {state.hp:.0%}，低於危險線 {cfg.safety.critical_hp_ratio:.0%}")

    hp_pot = profile.potions.get("hp")
    if hp_pot and state.hp < hp_pot.below_ratio and _potion_due(rt, "hp", hp_pot.cooldown, now):
        return DrinkPotion("hp", hp_pot.key)

    mp_pot = profile.potions.get("mp")
    if mp_pot and state.mp is not None and state.mp < mp_pot.below_ratio \
            and _potion_due(rt, "mp", mp_pot.cooldown, now):
        return DrinkPotion("mp", mp_pot.key)

    if cfg.safety.pause_when_players and state.others:
        return Wait(f"小地圖出現 {len(state.others)} 位其他玩家，暫停動作", seconds=1.0)

    for i, buff in enumerate(profile.buffs):
        if buff.key and now - rt.last_buff.get(i, 0.0) >= buff.every:
            return CastBuff(i, buff.key, buff.cast_seconds)

    atk = profile.attack
    in_range = [
        m for m in state.mobs
        if abs(m.cx - center_x) <= atk.range_px
        and abs(m.cy - center_y) <= atk.vertical_range_px
    ]
    if in_range and now - rt.last_attack >= atk.cooldown:
        nearest = min(in_range, key=lambda m: abs(m.cx - center_x))
        direction = 1 if nearest.cx >= center_x else -1
        return Attack(direction, atk.key, atk.cast_seconds, atk.repeat,
                      aoe=(atk.type == "aoe"))

    pat = profile.patrol
    wp = pat.waypoints[rt.wp_index % len(pat.waypoints)]
    target = resolve_waypoint_x(wp, state.minimap_size)
    dist = target - state.player[0]
    if abs(dist) <= pat.tolerance:
        rt.wp_index = (rt.wp_index + 1) % len(pat.waypoints)
        rt.stuck_pos = None
        if wp.keys:
            return RunKeys(list(wp.keys))
        return Wait("抵達巡邏點，切換下一個", seconds=0.2)

    if _check_stuck(rt, state.player, now, pat.stuck_px, pat.stuck_seconds):
        rt.escape_direction *= -1
        return Escape(rt.escape_direction, pat.jump_key)

    seconds = max(min(abs(dist) * pat.step_seconds_per_px, pat.max_step_seconds), 0.08)
    return Move(1 if dist > 0 else -1, seconds, target)
