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
    # 角色定位：角色在 playfield 上的位置，依序取名牌 -> 組隊紅條。
    # None = 兩個都沒量到，決策層退回「角色在畫面正中央」的假設
    player_screen: Optional[Tuple[int, int]] = None
    minimap_size: Optional[Tuple[int, int]] = None  # 小地圖 ROI 寬高（相對座標換算用）
    others: List[Tuple[int, int]] = field(default_factory=list)
    mobs: List[Mob] = field(default_factory=list)  # playfield 座標
    # 死亡復活對話框「確定」鈕的 playfield 座標。只在 HP≈0 時才偵測，
    # None = 沒死。Runner 看到它就點下去復活（見 vision/revive.py）
    revive_button: Optional[Tuple[int, int]] = None

    @property
    def vision_ok(self) -> bool:
        return self.hp is not None and self.player is not None
