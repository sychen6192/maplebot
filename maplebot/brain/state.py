"""每個 tick 的遊戲狀態快照（感知層輸出、決策層輸入）。"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..vision.mobs import Mob


@dataclass
class GameState:
    ts: float
    hp: Optional[float] = None       # 0.0 ~ 1.0，None = 讀不到
    mp: Optional[float] = None
    exp: Optional[float] = None
    player: Optional[Tuple[int, int]] = None   # 小地圖座標
    others: List[Tuple[int, int]] = field(default_factory=list)
    mobs: List[Mob] = field(default_factory=list)  # playfield 座標

    @property
    def vision_ok(self) -> bool:
        return self.hp is not None and self.player is not None
