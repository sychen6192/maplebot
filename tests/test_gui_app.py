"""控制台畫面層的冒煙測試：真的把 tkinter 視窗建起來，點幾個關鍵流程。

邏輯都在 controller / live / setup / settings 裡另外測過；這裡只確認畫面層
把它們接對了——欄位填錯會標紅、範本不會被改到、執行中表單會鎖住、關得掉。
CI 沒有顯示器，建不了視窗就整個跳過（有 Xvfb 的話會跑）。
"""
import shutil
import time
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "mapleaga_800x600.jpg"


def _has_display() -> bool:
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    root.destroy()
    return True


pytestmark = pytest.mark.skipif(not _has_display(), reason="沒有顯示器，建不了 tk 視窗")


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    from maplebot.capture import ImageCapture
    from maplebot.gui import app as appmod
    from maplebot.gui.controller import Controller

    shutil.copytree(REPO / "config", tmp_path / "config",
                    ignore=shutil.ignore_patterns("gui_state.json", "*.bak", "local.yaml"))
    monkeypatch.chdir(tmp_path)          # 環境檢查會在工作目錄下寫 logs/
    # 對話框會卡住測試：一律回答「否／取消」，並記下問了什麼
    asked = []
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(appmod.messagebox, name,
                            lambda *a, _n=name, **k: asked.append((_n, a)))
    for name in ("askyesno", "askokcancel", "askyesnocancel"):
        monkeypatch.setattr(appmod.messagebox, name,
                            lambda *a, _n=name, **k: asked.append((_n, a)) or False)
    apps = []

    def make(profile="example.yaml"):
        ctl = Controller(str(tmp_path / "config" / "default.yaml"),
                         str(tmp_path / "config" / "profiles" / profile),
                         capture_factory=lambda cfg: ImageCapture(str(FIXTURE)))
        a = appmod.App(ctl)
        a.asked = asked
        apps.append(a)
        pump(a)
        return a

    yield make
    for a in apps:
        try:
            a.ctl.stop()
            deadline = time.monotonic() + 5
            while a.ctl.running and time.monotonic() < deadline:
                time.sleep(0.05)
            a.destroy()
        except tk.TclError:
            pass


def pump(app, seconds=0.3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.update()
        time.sleep(0.02)


def test_the_window_builds_with_the_setup_checklist_first(make_app):
    app = make_app()
    first = app.nb.tab(app.nb.tabs()[0], "text")
    assert first.startswith("準備")
    assert "範例" in app.title()
    assert app.dirty_label.cget("text") == "已儲存"
    assert app.save_btn.instate(["disabled"])


def test_a_bad_number_is_marked_and_named(make_app):
    app = make_app()
    app.vars["attack_range"].set("abc")
    pump(app)
    assert app.inputs["attack_range"].instate(["invalid"])
    assert "水平距離" in app.problem_text.cget("text")
    assert app.nb.tab(app.tab_frames["打怪"], "text").endswith("×")
    assert app._save() is False                  # 有錯就不寫檔


def test_editing_a_template_never_touches_it(make_app, tmp_path):
    app = make_app("example.yaml")
    path = tmp_path / "config" / "profiles" / "example.yaml"
    before = path.read_bytes()
    app.vars["attack_key"].set("x")
    pump(app)
    assert "1 項未儲存" in app.dirty_label.cget("text")
    assert app._save() is False                  # 問要不要另存時回答了「否」
    assert any(name == "askyesno" for name, _a in app.asked)
    assert path.read_bytes() == before


def test_settings_lock_while_a_dry_run_is_going(make_app):
    app = make_app("弓箭手訓練場一.yaml")
    app.dry_var.set(True)
    app._mode_changed()
    app._start()
    deadline = time.monotonic() + 5
    while not app.ctl.running and time.monotonic() < deadline:
        pump(app, 0.1)
    pump(app, 0.5)
    assert app.ctl.running
    assert app.inputs["attack_range"].instate(["disabled"])
    assert app.lock_strip.winfo_ismapped()
    assert app.primary_btn.cget("text") == "‖ 暫停"
    app.ctl.stop()
    deadline = time.monotonic() + 5
    while app.ctl.running and time.monotonic() < deadline:
        pump(app, 0.1)
    pump(app, 0.4)
    assert not app.inputs["attack_range"].instate(["disabled"])


def test_close_with_nothing_pending_just_closes(make_app):
    app = make_app()
    app._close()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            app.update()
        except tk.TclError:
            break                                # 視窗已經 destroy
        time.sleep(0.02)
    with pytest.raises(tk.TclError):
        app.winfo_exists()


def _click_key_field(app, field):
    from maplebot.gui import settings
    app.nb.select(app.tab_frames[settings.tab_of(field)])   # 只有看得到的欄位拿得到焦點
    pump(app, 0.1)
    w = app.inputs[field]
    w.event_generate("<Button-1>")
    # Xvfb 沒有視窗管理員，程式拿不到鍵盤焦點，focus_set() 不會生效；
    # 真的桌面上點一下就有焦點了。這裡直接強制給
    w.focus_force()
    pump(app, 0.1)
    return w


def test_walking_away_from_a_key_field_never_saves_the_prompt(make_app, tmp_path):
    """點了按鍵欄位又點去別格：提示字不能被當成按鍵名稱存進設定檔。"""
    app = make_app("弓箭手訓練場一.yaml")
    path = tmp_path / "config" / "profiles" / "弓箭手訓練場一.yaml"
    before = path.read_bytes()
    _click_key_field(app, "jump_key")
    assert app.vars["jump_key"].get() == "按一個鍵…"
    app.inputs["attack_range"].focus_force()
    pump(app, 0.2)
    assert app.vars["jump_key"].get() != "按一個鍵…"
    assert app._capture is None
    assert app._save() is True                   # 沒有修改，什麼都不寫
    assert path.read_bytes() == before
    # 下一格照樣能擷取
    w = _click_key_field(app, "loot_key")
    w.event_generate("<KeyPress>", keysym="x")
    pump(app)
    assert app.vars["loot_key"].get() == "x"


def test_space_and_enter_can_be_bound(make_app):
    app = make_app("弓箭手訓練場一.yaml")
    for keysym, name in (("space", "space"), ("Return", "enter")):
        w = _click_key_field(app, "loot_key")
        w.event_generate("<KeyPress>", keysym=keysym)
        pump(app)
        assert app.vars["loot_key"].get() == name


def test_cancelling_stop_resumes_the_run(make_app, monkeypatch):
    """停止對話框開著時 bot 先暫停；按取消要繼續跑，不能停在暫停。"""
    from maplebot.gui import app as appmod
    app = make_app("弓箭手訓練場一.yaml")
    app.dry_var.set(True)
    app._mode_changed()
    app._start()
    deadline = time.monotonic() + 5
    while not app.ctl.running and time.monotonic() < deadline:
        pump(app, 0.1)
    seen = {}
    monkeypatch.setattr(appmod.messagebox, "askokcancel",
                        lambda *a, **k: seen.setdefault("paused", app.ctl.status().paused
                                                        or app.ctl.runner.safety.paused)
                        and False)
    app._stop()
    assert seen["paused"] is True
    assert app.ctl.running
    assert not app.ctl.runner.safety.paused
