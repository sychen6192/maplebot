"""事件警報：嗶聲（當下）+ 帳本（事後）。

嗶聲參考 auto-maple 的 notifier，改用 winsound 免音檔：danger 類事件
（Panic、黑屏、watchdog）長響提醒人回來看，info 類（其他玩家出現）短響一聲。
非 Windows 或關閉時靜音。

帳本是後來補的，理由很實際：掛了三小時回來，嗶聲早就響完了，log 是一萬行
流水帳，人真正想知道的是「這段時間出過幾次事、都是什麼事」。所以每次響鈴
都記一筆，收工時直接進報告。
"""
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

IS_WINDOWS = sys.platform == "win32"

# (頻率 Hz, 長度 ms) 序列
PATTERNS: Dict[str, List[Tuple[int, int]]] = {
    "panic": [(1400, 350), (900, 350), (1400, 350), (900, 350), (1400, 500)],
    "warn": [(1000, 250), (700, 350)],
    "ding": [(1200, 150)],
}

MAX_RECORDS = 500      # 帳本上限。掛整晚也不會漲到有感，滿了丟最舊的


@dataclass
class AlertRecord:
    at: float           # time.time()，報告要顯示牆上時鐘
    kind: str           # panic | warn | ding
    message: str = ""

    def as_dict(self) -> dict:
        return {"at": self.at,
                "time": time.strftime("%H:%M:%S", time.localtime(self.at)),
                "kind": self.kind, "message": self.message}


class Alerts:
    def __init__(self, enabled: bool = True):
        # 嗶聲關掉不代表不記帳：靜音掛機的人更需要事後看得到發生過什麼
        self.enabled = enabled and IS_WINDOWS
        self.records: List[AlertRecord] = []
        self.dropped = 0

    def ping(self, kind: str, message: str = "", at: Optional[float] = None) -> None:
        self.records.append(AlertRecord(at=time.time() if at is None else at,
                                        kind=kind, message=message))
        if len(self.records) > MAX_RECORDS:
            self.dropped += len(self.records) - MAX_RECORDS
            del self.records[:-MAX_RECORDS]
        if not self.enabled or kind not in PATTERNS:
            return
        threading.Thread(target=self._play, args=(PATTERNS[kind],),
                         daemon=True, name=f"alert-{kind}").start()

    def counts(self) -> Dict[str, int]:
        return dict(Counter(r.kind for r in self.records))

    def recent(self, n: int = 10) -> List[AlertRecord]:
        return self.records[-n:]

    def summary(self) -> str:
        if not self.records:
            return "期間沒有任何警報"
        counts = self.counts()
        order = ["panic", "warn", "ding"]
        parts = [f"{k} {counts[k]}" for k in order if k in counts]
        parts += [f"{k} {v}" for k, v in sorted(counts.items()) if k not in order]
        return "警報 " + "、".join(parts)

    @staticmethod
    def _play(pattern: List[Tuple[int, int]]) -> None:
        import winsound
        try:
            for freq, ms in pattern:
                winsound.Beep(freq, ms)
        except RuntimeError:
            pass
