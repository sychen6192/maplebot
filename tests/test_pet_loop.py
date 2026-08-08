"""重現「一直攻擊寵物、永遠不巡邏」的死結，驗證兩道機制一起把它解開。

實際回報的症狀是每個 tick 都輸出 Attack、角色一步不動、經驗完全沒進帳。
成因是寵物被描邊偵測當成怪：牠不會死、也不會離開攻擊範圍。

死結在於兩道機制彼此需要對方：
  - 跟隨物過濾要「角色有移動」才判別得出寵物
  - 但只要一直攻擊，角色就永遠不會移動
所以 FSM 的「打很久卻沒移動就強制去巡邏」是解開它的那一手。
這裡把感知→決策→移動的迴圈整個跑一遍，確認真的會解開。
"""
import cv2
import numpy as np
import pytest

from maplebot.brain import fsm
from maplebot.brain.state import GameState  # noqa: F401  (型別參考)
from maplebot.config import AppCfg, Profile, Waypoint
from maplebot.perception import Perceiver
from maplebot.vision.mobs import Mob

FPS = 8.0
MINIMAP = (10, 10, 120, 40)
PLAYFIELD = (0, 80, 300, 200)


class _PetDetector:
    """寵物永遠待在角色右邊同一個「畫面」位置——不管角色走到哪。"""

    def detect(self, playfield):
        return [Mob(cx=190, cy=100, w=24, h=24, score=1.0, name="m")]


_BG = cv2.GaussianBlur(
    np.random.default_rng(5).integers(0, 255, (200, 300, 3), dtype=np.uint8), (5, 5), 0)
PAN = 40           # 小地圖走一格 = 畫面捲 40px（跟隨物過濾要靠鏡頭捲動才判得出來）


def _frame(player_x: int) -> np.ndarray:
    img = np.zeros((300, 300, 3), dtype=np.uint8)
    img[10:50, 10:130] = (150, 190, 205)                     # 小地圖底
    x = 10 + int(np.clip(player_x, 2, 117))
    img[28:31, x:x + 3] = (0, 255, 255)                      # 玩家黃點
    img[60:68, 10:60] = (0, 0, 230)                          # HP 滿
    img[80:280, 0:300] = np.roll(_BG, -player_x * PAN, axis=1)   # 鏡頭跟著角色捲
    return img


@pytest.fixture
def cfg():
    c = AppCfg()
    c.regions = {"minimap": MINIMAP, "hp_bar": (10, 60, 50, 8), "playfield": PLAYFIELD}
    c.vision.filter_followers = True      # 預設關閉，這裡就是要測它
    c.vision.player_move_px = 1
    return c


@pytest.fixture
def profile():
    p = Profile()
    p.patrol.waypoints = [Waypoint(5), Waypoint(110)]
    p.attack.key = "x"
    p.attack.range_px = 320
    p.attack.vertical_range_px = 90
    return p


def _simulate(cfg, profile, ticks: int):
    """跑主迴圈的骨架：感知 -> 決策 -> Move 才讓角色真的移動。"""
    perceiver = Perceiver(cfg, _PetDetector())
    rt = fsm.Runtime()
    center = (PLAYFIELD[2] // 2, PLAYFIELD[3] // 2)
    player_x, now = 60, 1000.0
    log = []
    for _ in range(ticks):
        state = perceiver.perceive(_frame(player_x), now)
        action = fsm.decide(state, cfg, profile, rt, now, center)
        if isinstance(action, fsm.Move):
            player_x += action.direction        # 一個 tick 走一格
        log.append(action)
        now += 1.0 / FPS
    return log, rt, perceiver


def test_pet_deadlock_breaks_and_the_pet_gets_excluded(cfg, profile):
    log, rt, perceiver = _simulate(cfg, profile, ticks=160)

    # 死結解開了：強制讓路過，而且最後已經在正常巡邏
    assert rt.attack_breaks >= 1
    assert any(isinstance(a, fsm.Move) for a in log)

    # 寵物被認出來了，最後幾十個 tick 完全不再攻擊它
    assert len(perceiver.last_followers) == 1
    tail = log[-40:]
    assert not any(isinstance(a, fsm.Attack) for a in tail)
    assert all(isinstance(a, (fsm.Move, fsm.Wait, fsm.Escape)) for a in tail)


def test_the_deadlock_is_real_without_the_fixes(cfg, profile):
    """沒有這兩道機制的話，同樣的情境就是使用者回報的那個樣子。"""
    cfg.vision.filter_followers = False
    cfg.safety.attack_stall_seconds = 0
    log, _, _ = _simulate(cfg, profile, ticks=160)
    assert all(isinstance(a, fsm.Attack) for a in log)
