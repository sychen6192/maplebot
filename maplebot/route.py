"""把「你自己走一遍」錄成巡邏路線。

手寫 waypoints 要先去量座標，多層地圖還要一個一個試繩子在哪；錄製就是
把這件事反過來：正常玩一趟，程式盯著小地圖記下你走過的位置和按過的鍵，
結束後壓成 profile 裡的 patrol 區塊。

壓縮規則刻意保守——錄到的原始軌跡有幾百個點，但真正有意義的只有三種：

  * **轉折點**：x 走到底折返的地方，就是巡邏區間的端點
  * **樓層變化**：小地圖 y 跳一階，代表爬了繩子或下了平台
  * **停下來按的鍵**：站定按的技能/跳躍會掛到剛抵達的那個點上

方向鍵不記進 keys——那是「怎麼走到這裡」，執行時由巡邏邏輯自己決定，
硬記下來反而會在被怪打歪之後照著錯的節奏亂按。
"""
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

MOVE_KEYS = frozenset({"left", "right", "up", "down"})


@dataclass
class Sample:
    """錄製時每個 tick 的一筆觀測。"""
    t: float
    x: Optional[int]                       # 小地圖座標，None = 這幀沒認到玩家
    y: Optional[int] = None
    keys: Tuple[str, ...] = ()             # 這幀按著的鍵（含方向鍵）


@dataclass
class RoutePoint:
    x: int
    y: Optional[int] = None
    keys: List[str] = field(default_factory=list)
    descend: str = ""                      # "jump" = 下跳平台

    def to_dict(self) -> dict:
        d: dict = {"x": self.x}
        if self.y is not None:
            d["y"] = self.y
        if self.descend:
            d["descend"] = self.descend
        if self.keys:
            d["keys"] = list(self.keys)
        return d


def compress(samples: Sequence[Sample], tolerance: int = 4, y_tolerance: int = 3,
             min_span: int = 6, jump_key: str = "") -> List[RoutePoint]:
    """原始軌跡 -> 巡邏點。多層地圖才會帶 y。"""
    pts = [s for s in samples if s.x is not None]
    if not pts:
        return []

    multi_level = _has_levels(pts, y_tolerance)
    out: List[RoutePoint] = []
    pending: List[str] = []                # 還沒掛到點上的按鍵

    def emit(x: int, y: Optional[int], descend: str = "") -> None:
        keys = _dedupe(pending)
        pending.clear()
        if not descend and not keys and _already_have(out, x, y, tolerance):
            return          # 繞第二圈回到同一個地方，不用再記一次
        out.append(RoutePoint(x=x, y=y, keys=keys, descend=descend))

    level = pts[0].y
    direction = 0                          # 0 = 還沒開始走
    peak = prev_x = pts[0].x               # peak = 這一段走到最遠的位置
    jump_held = False

    for s in pts:
        for k in s.keys:
            if k == jump_key:
                jump_held = True
            elif k not in MOVE_KEYS:
                pending.append(k)

        if multi_level and s.y is not None and level is not None \
                and abs(s.y - level) > y_tolerance:
            # 爬繩/下平台：離開舊樓層的位置和落地的位置都要記，
            # 執行時才知道要先走到繩子前面
            emit(prev_x, level)
            emit(s.x, s.y, "jump" if (s.y > level and jump_held) else "")
            level, direction, peak, jump_held = s.y, 0, s.x, False
            prev_x = s.x
            continue
        prev_x = s.x

        if direction == 0:
            if s.x != peak:
                direction = 1 if s.x > peak else -1
                peak = s.x
        elif (s.x - peak) * direction > 0:
            peak = s.x                     # 還在往同一個方向前進
        elif abs(s.x - peak) >= min_span:
            # 已經往回走了一段才算折返；被怪打退一兩格不算
            emit(peak, level if multi_level else None)
            direction = -direction
            peak = s.x

    emit(peak, level if multi_level else None)
    if pending and out:                    # 收尾還有沒掛出去的鍵
        out[-1].keys.extend(_dedupe(pending))
    return out


def _has_levels(pts: Sequence[Sample], y_tolerance: int) -> bool:
    ys = [p.y for p in pts if p.y is not None]
    return bool(ys) and max(ys) - min(ys) > y_tolerance


def _already_have(out: List[RoutePoint], x: int, y: Optional[int], tolerance: int) -> bool:
    return any(p.y == y and abs(p.x - x) <= tolerance for p in out)


def _dedupe(keys: Sequence[str]) -> List[str]:
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def to_yaml_block(points: Sequence[RoutePoint], indent: str = "  ") -> str:
    """輸出可以直接貼進 profile 的 patrol.waypoints 區塊。"""
    if not points:
        return "patrol:\n%swaypoints: []   # 沒錄到有效座標\n" % indent
    simple = all(p.y is None and not p.keys and not p.descend for p in points)
    lines = ["patrol:"]
    if simple:
        lines.append(f"{indent}waypoints: [{', '.join(str(p.x) for p in points)}]")
    else:
        lines.append(f"{indent}waypoints:")
        for p in points:
            body = ", ".join(f"{k}: {v!r}" if k in ("descend",) else f"{k}: {v}"
                             for k, v in p.to_dict().items())
            lines.append(f"{indent}{indent}- {{{body}}}")
    return "\n".join(lines) + "\n"
