"""經驗值進度追蹤：換算效率，並偵測「有在跑但沒在賺」。

這是最有價值的健康指標，因為它驗證的是**整條鏈路**：畫面辨識 → 決策 →
按鍵 → 遊戲真的有反應。其他 watchdog 只能看到局部——小地圖點還在、
畫面沒黑，但技能鍵設錯、怪其實打不到、在安全區空揮，這些都只有
「EXP 沒在動」看得出來。

EXP 條會回捲：升級時從 99% 掉回 0%。掉幅超過 level_up_drop 視為升級，
小幅下降則是死亡懲罰（楓谷死亡會扣經驗），兩者分開計算。
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ExpTracker:
    stall_seconds: float = 600.0      # 多久沒進帳視為異常（0=不檢查）
    level_up_drop: float = 0.5        # EXP 掉超過這個比例視為升級而非死亡

    started: Optional[float] = None
    last_exp: Optional[float] = None
    last_gain_at: Optional[float] = None
    levels: int = 0                   # 期間升了幾級
    deaths: int = 0                   # 偵測到的經驗倒退次數
    _first_exp: Optional[float] = None

    def update(self, exp: Optional[float], now: float) -> None:
        if exp is None:                       # 讀不到就跳過，不要污染統計
            return
        if self.started is None:
            self.started = now
            self.last_gain_at = now
            self._first_exp = exp
            self.last_exp = exp
            return

        prev = self.last_exp
        self.last_exp = exp
        if prev is None:
            return

        delta = exp - prev
        if delta > 0:
            self.last_gain_at = now
        elif delta <= -self.level_up_drop:    # 99% -> 0%
            self.levels += 1
            self.last_gain_at = now
        elif delta < 0:                       # 小幅倒退 = 死亡扣經驗
            self.deaths += 1

    def gained(self) -> float:
        """累積進度，單位是「等」（1.35 = 一級又 35%）。"""
        if self._first_exp is None or self.last_exp is None:
            return 0.0
        return self.levels + (self.last_exp - self._first_exp)

    def per_hour(self, now: float) -> Optional[float]:
        if self.started is None or now <= self.started:
            return None
        hours = (now - self.started) / 3600.0
        if hours <= 0:
            return None
        return self.gained() / hours

    def stalled(self, now: float) -> bool:
        """太久沒有任何經驗進帳。"""
        if self.stall_seconds <= 0 or self.last_gain_at is None:
            return False
        return (now - self.last_gain_at) >= self.stall_seconds

    def summary(self, now: float) -> str:
        if self.started is None or self.last_exp is None:
            return "EXP 未讀到"
        rate = self.per_hour(now)
        parts = [f"EXP {self.last_exp:.1%}",
                 f"累積 +{self.gained():.2%} 等"]
        if rate is not None:
            parts.append(f"約 {rate:.2f} 等/小時")
        if self.levels:
            parts.append(f"升級 {self.levels} 次")
        if self.deaths:
            parts.append(f"經驗倒退 {self.deaths} 次")
        return "｜".join(parts)
