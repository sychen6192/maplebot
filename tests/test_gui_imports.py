"""CI 沒有顯示環境、也可能沒裝 tkinter：只有 maplebot/gui/app.py 可以 import 它。

用子行程檢查，才不會被同一個 pytest 行程裡別的測試先 import 過的模組騙過。
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_gui_logic_modules_do_not_pull_in_tkinter():
    code = ("import maplebot.gui.controller, maplebot.gui.settings, maplebot.gui.live, "
            "maplebot.gui.setup, maplebot.gui.look, maplebot.selfcheck, "
            "maplebot.yaml_edit, sys; "
            "assert 'tkinter' not in sys.modules, 'tkinter was imported'")
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr


def test_yaml_edit_does_not_pull_in_the_gui_or_config():
    """tools/calibrate.py 靠 yaml_edit 寫 local.yaml；它一 import GUI 套件，工具就又
    依賴控制台了。config 反過來從它拿 _deep_merge，它再 import config 就是循環 import。"""
    code = ("import maplebot.yaml_edit, sys; "
            "bad = sorted(m for m in sys.modules "
            "if m.startswith(('maplebot.gui', 'maplebot.config', 'tkinter'))); "
            "assert not bad, bad")
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
