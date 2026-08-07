"""決策核心：純函式的優先權狀態機。

decide() 不碰鍵盤、不碰螢幕、不看時鐘（now 由外部傳入），
所以整個打怪邏輯可以用單元測試完整驗證。

優先權（高到低）：
  1. 視覺異常          -> Wait（交給 runner 的 watchdog 計時）
  2. HP 低於危險線      -> Panic（停止程式並截圖）
  3. HP/MP 低於補藥線   -> DrinkPotion
  4. 小地圖出現其他玩家  -> Wait（禮貌暫停，pause_when_players 可關）
  5. Buff 到期          -> CastBuff
  6. 攻擊範圍內有怪      -> Attack（面向最近的怪）
  7. 其他               -> Move 往下一個巡邏點

楓谷的鏡頭永遠跟著角色，所以「角色在 playfield 的位置」直接用
playfield 中心近似，怪物距離就是與中心的距離。
"""
from dataclasses import dataclass, field
from typing import Dict, Tuple, Union

from ..config import AppCfg, Profile
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


@dataclass
class Move:
    direction: int
    seconds: float
    target_x: int


Action = Union[Wait, Panic, DrinkPotion, CastBuff, Attack, Move]


@dataclass
class Runtime:
    """跨 tick 的決策記憶：冷卻時間、巡邏進度。"""
    wp_index: int = 0
    last_buff: Dict[int, float] = field(default_factory=dict)
    last_potion: Dict[str, float] = field(default_factory=dict)
    last_attack: float = 0.0

    def note_buff(self, index: int, now: float) -> None:
        self.last_buff[index] = now

    def note_potion(self, kind: str, now: float) -> None:
        self.last_potion[kind] = now


def _potion_due(rt: Runtime, kind: str, cooldown: float, now: float) -> bool:
    return now - rt.last_potion.get(kind, 0.0) >= cooldown


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
        return Attack(direction, atk.key, atk.cast_seconds, atk.repeat)

    pat = profile.patrol
    target = pat.waypoints_x[rt.wp_index % len(pat.waypoints_x)]
    dist = target - state.player[0]
    if abs(dist) <= pat.tolerance:
        rt.wp_index = (rt.wp_index + 1) % len(pat.waypoints_x)
        return Wait("抵達巡邏點，切換下一個", seconds=0.2)
    seconds = max(min(abs(dist) * pat.step_seconds_per_px, pat.max_step_seconds), 0.08)
    return Move(1 if dist > 0 else -1, seconds, target)
