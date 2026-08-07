"""執行層：把決策 Action 轉成鍵盤操作，並更新冷卻記錄與統計。

Panic 不在這裡處理——停機、截圖存證屬於 Runner 的安全職責。
dry-run 時所有動作延遲都壓到最短，只留下按鍵紀錄與日誌。
"""
import time
from dataclasses import dataclass, field

from .brain import fsm
from .control.input_win import Keyboard


@dataclass
class Stats:
    started: float = field(default_factory=time.monotonic)
    ticks: int = 0
    attacks: int = 0
    buffs: int = 0
    potions_hp: int = 0
    potions_mp: int = 0

    def summary(self) -> str:
        mins = (time.monotonic() - self.started) / 60
        return (f"運行 {mins:.1f} 分鐘 | tick {self.ticks} | 攻擊 {self.attacks} 次 | "
                f"buff {self.buffs} 次 | HP 藥 {self.potions_hp} | MP 藥 {self.potions_mp}")


class Executor:
    def __init__(self, kb: Keyboard, rt: fsm.Runtime, stats: Stats, logger,
                 dry_run: bool = False):
        self.kb = kb
        self.rt = rt
        self.stats = stats
        self.log = logger
        self.dry_run = dry_run

    def _dur(self, seconds: float) -> float:
        return 0.01 if self.dry_run else seconds

    def execute(self, action: fsm.Action, now: float) -> None:
        if isinstance(action, fsm.Wait):
            self.log.debug("等待: %s", action.reason)
            time.sleep(self._dur(action.seconds))
            return

        if isinstance(action, fsm.DrinkPotion):
            self.log.info("喝 %s 藥水（%s 鍵）", action.kind.upper(), action.key)
            self.kb.tap(action.key, self._dur(0.08))
            self.rt.note_potion(action.kind, now)
            if action.kind == "hp":
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
            arrow = "right" if action.direction > 0 else "left"
            self.log.info("攻擊：面向%s，%s 鍵 x%d",
                          "右" if action.direction > 0 else "左",
                          action.key, action.repeat)
            self.kb.tap(arrow, self._dur(0.05))
            for _ in range(max(action.repeat, 1)):
                self.kb.tap(action.key, self._dur(action.cast_seconds))
                time.sleep(self._dur(0.1))
            self.rt.last_attack = now
            self.stats.attacks += 1
            return

        if isinstance(action, fsm.Move):
            arrow = "right" if action.direction > 0 else "left"
            self.log.debug("巡邏：往%s走 %.2fs（目標小地圖 x=%d）",
                           "右" if action.direction > 0 else "左",
                           action.seconds, action.target_x)
            self.kb.tap(arrow, self._dur(action.seconds))
            return

        self.log.warning("未知的動作型別: %r", action)
