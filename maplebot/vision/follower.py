"""濾掉「跟著角色跑」的東西——主要是寵物。

寵物也有黑色描邊，描邊偵測分不出牠跟怪物。但有一個很乾淨的判別方式：

楓谷的鏡頭永遠跟著角色，所以**角色移動時，世界上的怪會在畫面上滑動**
（你往右走，怪就相對往左移），**寵物卻一直待在角色旁邊、畫面位置幾乎不變**。

所以只在「角色確實移動了一段距離」時計分：畫面位置沒跟著滑動的，就是跟隨物。
角色站著不動時無法判別，這時沿用先前的判定結果，不重新計分。

比較的基準是**上次計分時的位置（錨點）**而不是前一幀：8 fps 下角色一個 tick
只走一點點，逐幀比對根本量不出差異。累積走夠遠了才計一次分。
"""
from dataclasses import dataclass
from typing import List, Tuple

from .mobs import Mob


@dataclass
class _Track:
    x: int                 # 這一幀的位置（用來跟下一幀配對）
    y: int
    ax: int                # 錨點：上次計分時的位置
    ay: int
    hits: int = 0          # 累積幾次「角色移動了但它沒跟著滑動」
    follower: bool = False
    seen: bool = False     # 這一幀有沒有配對到
    misses: int = 0        # 連續幾幀沒配對到
    scored: bool = False   # 錨點是否經歷過完整一輪（新出現的不能馬上計分）


class FollowerFilter:
    def __init__(self, drift_px: int = 40, hits_needed: int = 3,
                 match_px: int = 90, max_tracks: int = 40, max_misses: int = 5):
        self.drift_px = drift_px       # 畫面位移小於此值 = 沒跟著滑動
        self.hits_needed = hits_needed
        self.match_px = match_px       # 兩幀之間視為同一個目標的距離
        self.max_tracks = max_tracks
        self.max_misses = max_misses   # 閃爍漏偵測幾幀還留著，不用重新累積
        self._tracks: List[_Track] = []

    def reset(self) -> None:
        self._tracks.clear()

    def _match(self, mob: Mob):
        best, best_d = None, self.match_px
        for t in self._tracks:
            if t.seen:
                continue
            d = abs(t.x - mob.cx) + abs(t.y - mob.cy)
            if d < best_d:
                best, best_d = t, d
        return best

    def filter(self, mobs: List[Mob], player_moved: bool) -> Tuple[List[Mob], List[Mob]]:
        """回傳 (要打的, 判定為跟隨物的)。

        player_moved = 角色自上次計分後已經移動夠遠，這一幀可以計分。
        """
        for t in self._tracks:
            t.seen = False

        pairs = []
        for mob in mobs:
            track = self._match(mob)
            if track is None:
                track = _Track(mob.cx, mob.cy, mob.cx, mob.cy)
                self._tracks.append(track)
            track.x, track.y, track.seen, track.misses = mob.cx, mob.cy, True, 0
            pairs.append((mob, track))

        if player_moved:
            for t in self._tracks:
                if not t.seen:
                    continue          # 這一幀沒看到，錨點留著等它回來
                if t.scored:
                    if abs(t.x - t.ax) + abs(t.y - t.ay) <= self.drift_px:
                        t.hits += 1
                        if t.hits >= self.hits_needed:
                            t.follower = True
                    else:
                        t.hits = 0
                        t.follower = False
                t.ax, t.ay, t.scored = t.x, t.y, True

        kept: List[Mob] = []
        followers: List[Mob] = []
        for mob, track in pairs:
            (followers if track.follower else kept).append(mob)

        alive = []
        for t in self._tracks:
            if t.seen:
                alive.append(t)
                continue
            t.misses += 1
            if t.misses <= self.max_misses:
                alive.append(t)
        # 超量時先丟掉沒在畫面上的（多半是已經離開的殘留）
        alive.sort(key=lambda t: (not t.seen, t.misses))
        self._tracks = alive[:self.max_tracks]
        return kept, followers
