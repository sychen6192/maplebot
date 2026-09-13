"""把「每幀獨立猜」的候選點串成一條有連續性的軌跡。

**為什麼需要**：這個專案的偵測器都是單幀純函式——一幀之內猜不對就是錯。
對準到不需要第二個軸的偵測器（例如訓練好的模型）那樣沒問題，但顏色門檻
不是那種偵測器：它每一幀都在一堆長得差不多的候選裡賭一次，而賭錯的代價
不是「這一幀不動」，是**安靜地回報一個完全錯的位置**，然後巡邏整晚走錯地方。

實測的例子（`tests/fixtures/mapleaga_800x600.jpg`）：自由市場的黃地板被合併
擋掉之後，角色若剛好站在地板上，它自己的點也會被一起吞掉——`find_player`
於是回報**次大的殘存候選**，也就是小地圖標題文字的碎片 (53, 29)。座標從
(60, 46) 一口氣跳到 (53, 29)，而下游沒有任何東西看得出那是假的。

時間這個軸看得出來。角色在小地圖上是連續移動的：8 fps 下一個 tick 走不了
幾格，而假候選會直接跳過半張地圖。所以：

  * 位移在合理範圍內的候選優先（不是面積最大的優先）
  * 全部都跳太遠 -> 沿用上一個位置（coast），不要改口
  * 連續 coast 超過 max_coast 幀才承認跟丟，回 None（交給 watchdog 暫停報警）
  * 位移持續超大 -> 那不是雜訊是換圖，整個重來

「跟丟」與「跳到別的地方」的差別很重要：跟丟會叫人（`lost_player_timeout`
暫停 + 警報），跳錯會安靜地錯一整晚。這個類別存在的意義就是把後者轉成前者。
"""
from typing import List, Optional, Sequence, Tuple

Candidate = Tuple[int, int, float]      # (x, y, score)


class PointTracker:
    """單一目標點的軌跡追蹤。

    max_jump 是「一個 tick 之內可信的最大位移」，單位是呼叫端座標系的像素
    ——小地圖座標就用小地圖像素，playfield 座標就依 790 基準縮放後傳進來
    （ADR 0003）。給小地圖的預設值 12 是這樣來的：128px 寬的小地圖對應整張
    地圖，角色 8 fps 下一個 tick 走不到十分之一張地圖。
    """

    def __init__(self, max_jump: float = 12.0, max_coast: int = 3):
        self.max_jump = float(max_jump)
        self.max_coast = max(int(max_coast), 0)
        self.last: Optional[Tuple[int, int]] = None
        self.coasting = 0          # 連續幾幀沒有可信候選
        self.jumps = 0             # 擋掉幾次跳點（給 runner 回報用）

    def reset(self) -> None:
        self.last = None
        self.coasting = 0

    @property
    def confidence(self) -> float:
        """0.0 ~ 1.0：1.0 = 這一幀真的量到，往下遞減代表正在沿用舊值。

        決策層用得上這個差別：巡邏可以容忍 coast 一兩幀，但「角色位置」
        被拿去挖掉自己、對準攻擊範圍時，沿用來的值愈舊愈不該相信。
        """
        if self.last is None:
            return 0.0
        if self.max_coast == 0:
            return 1.0 if self.coasting == 0 else 0.0
        return max(0.0, 1.0 - self.coasting / (self.max_coast + 1.0))

    def update(self, candidates: Sequence[Candidate]) -> Optional[Tuple[int, int]]:
        """吃這一幀的所有候選，回報目前相信的位置。"""
        cands: List[Candidate] = list(candidates)

        if self.last is None:
            # 還沒有軌跡可以比對，只能信分數最高的（等同舊行為）
            if not cands:
                return None
            best = max(cands, key=lambda c: c[2])
            self.last, self.coasting = (best[0], best[1]), 0
            return self.last

        lx, ly = self.last
        near = [c for c in cands
                if abs(c[0] - lx) <= self.max_jump and abs(c[1] - ly) <= self.max_jump]
        if near:
            # 附近有候選：取最近的，不是分數最高的。分數高低在這裡沒有意義
            # ——同色地形的碎塊本來就可能比真正的點「更像一個點」
            best = min(near, key=lambda c: abs(c[0] - lx) + abs(c[1] - ly))
            self.last, self.coasting = (best[0], best[1]), 0
            return self.last

        # 沒有任何候選落在可信範圍內
        if cands:
            self.jumps += 1
        self.coasting += 1
        if self.coasting > self.max_coast:
            self.reset()
            return None
        return self.last       # 先沿用，別改口
