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
    minimap_xy: Optional[Tuple[int, int]] = None   # 小地圖座標
    # 角色定位：角色在 playfield 上的位置，依序取名牌 -> 組隊紅條。
    # None = 兩個都沒量到，決策層退回「角色在畫面正中央」的假設
    screen_xy: Optional[Tuple[int, int]] = None
    minimap_size: Optional[Tuple[int, int]] = None  # 小地圖 ROI 寬高（相對座標換算用）
    other_players: List[Tuple[int, int]] = field(default_factory=list)
    mobs: List[Mob] = field(default_factory=list)  # playfield 座標
    # 死亡復活對話框「確定」鈕的 playfield 座標。只在 HP≈0 時才偵測，
    # None = 沒死。Runner 看到它就點下去復活（見 vision/revive.py）
    revive_button: Optional[Tuple[int, int]] = None
    # 這一幀的位置到底是「真的量到」還是「沿用上一幀」。1.0 = 剛量到，
    # 往下遞減代表軌跡正在靠慣性撐著（見 vision/track.py）。
    # 存在的理由：巡邏容忍沿用一兩幀，但「角色在畫面上哪裡」會被拿去挖掉
    # 自己、對準攻擊範圍——沿用來的值愈舊愈不該當成事實。
    minimap_conf: float = 0.0
    screen_conf: float = 0.0
    # 這批怪的框是幾秒前的畫面算出來的。0 = 就是這一幀（主迴圈自己偵測）。
    # 偵測搬到背景執行緒時會 > 0——「有兩隻怪」跟「半秒前有兩隻怪」
    # 不是同一件事，下游要分得出來。
    mobs_age: float = 0.0
    # 這張地圖的小地圖會不會跟著角色捲動。會的話 minimap_xy 是**地圖座標**
    # 而不是小地圖內的座標——差別在於它可以超出小地圖寬度（見 issue #7）。
    minimap_scrolling: bool = False

    @property
    def vision_ok(self) -> bool:
        return self.hp is not None and self.minimap_xy is not None
