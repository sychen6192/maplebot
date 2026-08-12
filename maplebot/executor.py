"""執行層：把決策 Action 轉成鍵盤操作，並更新冷卻記錄與統計。

Panic 不在這裡處理——停機、截圖存證屬於 Runner 的安全職責。
所有動作時間都帶 ±20% 抖動（auto-maple 的 0.8 + 0.4*random 公式），
避免每一下都是完全相同的節奏。dry-run 時延遲壓到最短。
"""
import time
from dataclasses import dataclass, field
from random import random

from .brain import fsm
from .control.input_win import Keyboard

# 估計還要走這麼久以上就持續按住方向鍵；短於此就用短按精準微調
HOLD_MIN_SECONDS = 0.2


@dataclass
class Stats:
    started: float = field(default_factory=time.monotonic)
    ticks: int = 0
    attacks: int = 0
    buffs: int = 0
    potions_hp: int = 0
    potions_mp: int = 0
    escapes: int = 0
    climbs: int = 0
    loots: int = 0

    def summary(self) -> str:
        mins = (time.monotonic() - self.started) / 60
        return (f"運行 {mins:.1f} 分鐘 | tick {self.ticks} | 攻擊 {self.attacks} 次 | "
                f"buff {self.buffs} 次 | HP 藥 {self.potions_hp} | MP 藥 {self.potions_mp} | "
                f"撿物 {self.loots} 次 | 垂直移動 {self.climbs} 次 | 脫困 {self.escapes} 次")


class Executor:
    def __init__(self, kb: Keyboard, rt: fsm.Runtime, stats: Stats, logger,
                 dry_run: bool = False):
        self.kb = kb
        self.rt = rt
        self.stats = stats
        self.log = logger
        self.dry_run = dry_run
        self._held_dir = None      # 目前持續按住的方向鍵（None = 沒按）

    def _dur(self, seconds: float) -> float:
        if self.dry_run:
            return 0.01
        return seconds * (0.8 + 0.4 * random())

    def _hold(self, arrow: str, seconds: float) -> None:
        """遠距離持續按住方向鍵，快到了才短按微調（走位與追擊共用）。"""
        if seconds >= HOLD_MIN_SECONDS:
            if self._held_dir != arrow:
                self.stop_movement()
                self.kb.press(arrow)
                self._held_dir = arrow
            return          # 不放開；由主迴圈控制節奏，每 tick 重新確認方向
        self.stop_movement()
        self.kb.tap(arrow, self._dur(seconds))

    def _move(self, action: "fsm.Move") -> None:
        """遠距離持續按住方向鍵，快到了才短按微調。

        每個 tick 都「按下→放開」會讓角色走走停停：放開時角色減速停住，
        下一 tick 又要重新起步。除了看起來很假，位置更新也被拖慢
        （每次 Move 睡 0.45 秒 = 每秒只剩兩次閉迴路修正）。
        持續按住則是走一直線，主迴圈照樣每 tick 重新判斷要不要繼續。
        """
        arrow = "right" if action.direction > 0 else "left"
        self.log.debug("巡邏：往%s走（目標小地圖 x=%d，估計還要 %.2fs）",
                       "右" if action.direction > 0 else "左",
                       action.target_x, action.seconds)
        self._hold(arrow, action.seconds)

    def stop_movement(self) -> None:
        """放開持續按住的方向鍵。暫停、停機、切換動作前都要呼叫。"""
        if self._held_dir is not None:
            self.kb.release(self._held_dir)
            self._held_dir = None

    def execute(self, action: fsm.Action, now: float) -> None:
        # 除了繼續走路（巡邏／追擊）以外的任何動作，都要先放開方向鍵——
        # 按著方向鍵放技能會讓角色邊走邊打，也會在繩子上掉下來。
        # 走路類的不能在這裡放開：每 tick 放開再按下去，角色會走走停停。
        if not isinstance(action, (fsm.Move, fsm.Chase)):
            self.stop_movement()

        if isinstance(action, fsm.Wait):
            self.log.debug("等待: %s", action.reason)
            time.sleep(self._dur(action.seconds))
            return

        if isinstance(action, fsm.DrinkPotion):
            self.log.info("喝 %s 藥水（%s 鍵）", action.kind.upper(), action.key)
            self.kb.tap(action.key, self._dur(0.08))
            self.rt.note_potion(action.kind, now)
            if action.kind.startswith("hp"):      # hp 與 hp_emergency 都算補血
                self.stats.potions_hp += 1
            else:
                self.stats.potions_mp += 1
            return

        if isinstance(action, fsm.CastBuff):
            self.log.info("施放 buff（%s 鍵）", action.key)
            self.kb.tap(action.key, self._dur(0.08))
            time.sleep(self._dur(action.cast_seconds))
            self.rt.note_buff(action.index, now)
            self.stats.buffs += 1
            return

        if isinstance(action, fsm.Attack):
            if action.aoe:
                self.log.info("攻擊（AoE）：%s 鍵 x%d", action.key, action.repeat)
            else:
                arrow = "right" if action.direction > 0 else "left"
                self.log.info("攻擊：面向%s，%s 鍵 x%d",
                              "右" if action.direction > 0 else "左",
                              action.key, action.repeat)
                self.kb.tap(arrow, self._dur(0.05))
            for _ in range(max(action.repeat, 1)):
                self.kb.tap(action.key, self._dur(action.cast_seconds))
                time.sleep(self._dur(0.1))
            self.rt.note_skill(action.index, now)
            self.stats.attacks += 1
            return

        if isinstance(action, fsm.Move):
            self._move(action)
            return

        if isinstance(action, fsm.Chase):
            arrow = "right" if action.direction > 0 else "left"
            self.log.debug("%s：往%s走",
                           "拉開距離" if action.away else "追擊",
                           "右" if action.direction > 0 else "左")
            self._hold(arrow, action.seconds)
            return

        if isinstance(action, fsm.Probe):
            arrow = "right" if action.direction > 0 else "left"
            self.log.info("自動巡邏校正：往%s試探邊界",
                          "右" if action.direction > 0 else "左")
            self.kb.tap(arrow, self._dur(action.seconds))
            return

        if isinstance(action, fsm.Climb):
            if action.jump_key and action.direction < 0:
                # 跳抓：繩底懸空時站著按上抓不到，按住上、跳一下、繼續按住上
                #（落到繩上那刻就會抓住）。在繩上時 fsm 不會再帶 jump_key。
                self.log.info("跳抓繩子：按住 %s 跳（%s）再繼續往上",
                              action.key, action.jump_key)
                self.kb.press(action.key)
                try:
                    time.sleep(self._dur(0.08))
                    self.kb.tap(action.jump_key, self._dur(0.12))
                    time.sleep(self._dur(max(action.seconds, 0.35)))
                finally:
                    self.kb.release(action.key)
            elif action.jump_key:
                self.log.info("下跳平台：按住 %s 再跳（%s）", action.key, action.jump_key)
                self.kb.press(action.key)
                try:
                    time.sleep(self._dur(0.1))
                    self.kb.tap(action.jump_key, self._dur(0.12))
                    time.sleep(self._dur(0.25))
                finally:
                    self.kb.release(action.key)
            else:
                self.log.info("垂直移動：往%s（%s 鍵 %.2fs）",
                              "上" if action.direction < 0 else "下",
                              action.key, action.seconds)
                self.kb.tap(action.key, self._dur(action.seconds))
            self.stats.climbs += 1
            return

        if isinstance(action, fsm.Loot):
            self.log.info("撿取掉落物（%s 鍵 x%d）", action.key, action.taps)
            for _ in range(action.taps):
                self.kb.tap(action.key, self._dur(0.06))
                time.sleep(self._dur(0.12))
            self.stats.loots += 1
            return

        if isinstance(action, fsm.RunKeys):
            self.log.info("巡邏點動作：%s", " -> ".join(action.keys))
            for key in action.keys:
                self.kb.tap(key, self._dur(0.08))
                time.sleep(self._dur(0.15))
            return

        if isinstance(action, fsm.Escape):
            arrow = "right" if action.direction > 0 else "left"
            self.log.warning("卡住了，往%s跳一下脫困", "右" if action.direction > 0 else "左")
            self.kb.press(arrow)
            try:
                time.sleep(self._dur(0.1))
                self.kb.tap(action.jump_key, self._dur(0.12))
                time.sleep(self._dur(0.25))
            finally:
                self.kb.release(arrow)
            self.stats.escapes += 1
            return

        self.log.warning("未知的動作型別: %r", action)
