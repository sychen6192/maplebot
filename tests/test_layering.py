"""分層靠 import 守住：決策層不能把 OpenCV 拉進來，即時迴圈不能把離線訓練程式拉進來。

ADR 0002 要 decide() 是純函式，import 這一層也一樣。以前 brain/state.py 從
vision/mobs.py 拿 Mob，mobs.py 頂層就 import cv2，所以 `import maplebot.brain.fsm`
會把 cv2 和 numpy 一起載進來。現在 Mob 放在不碰 cv2、numpy 的 vision/types.py。

用子行程檢查，才不會被同一個 pytest 行程裡別的測試先 import 過的模組騙過。
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_decision_layer_does_not_pull_in_opencv():
    code = ("import maplebot.brain.fsm, sys; "
            "leaked = [m for m in ('cv2', 'numpy') if m in sys.modules]; "
            "assert not leaked, f'brain.fsm pulled in {leaked}'")
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr


def test_live_loop_does_not_pull_in_training_code():
    # maplebot/training/ 只給 GPU 機上的標註、訓練工具用。即時迴圈一旦 import 它，
    # 改標註流程就可能改到 bot 行為，目錄分出來的意義就沒了
    code = ("import maplebot.runner, sys; "
            "leaked = sorted(m for m in sys.modules if m.startswith('maplebot.training')); "
            "assert not leaked, f'runner pulled in {leaked}'")
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr


def test_mobs_reexports_are_the_definitions():
    # 舊的 `from maplebot.vision.mobs import Mob` 還有很多地方在用。轉出去的必須是
    # 同一個類別，不能是另外定義的同名複本：dataclass 的 == 先比類別，偵測器產生的
    # Mob 跟測試從 mobs 拿來建的 Mob 欄位全同也會判成不相等
    from maplebot.vision import mobs, template_mobs, types

    assert mobs.Mob is types.Mob
    assert mobs.MobDetector is types.MobDetector
    assert mobs.TemplateMobDetector is template_mobs.TemplateMobDetector
