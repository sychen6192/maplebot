"""擷取層：空白畫面判定（決定 auto 模式退不退回螢幕擷取）與 ROI 裁切。"""
import numpy as np
import pytest

from maplebot.capture import CaptureError, ImageCapture, _crop, looks_blank


def test_black_frame_is_blank():
    assert looks_blank(np.zeros((200, 300, 3), dtype=np.uint8)) is True


def test_none_is_blank():
    assert looks_blank(None) is True


def test_game_frame_is_not_blank():
    rng = np.random.default_rng(4)
    assert looks_blank(rng.integers(20, 200, (200, 300, 3), dtype=np.uint8)) is False


def test_mostly_black_with_small_hud_still_blank():
    frame = np.zeros((200, 300, 3), dtype=np.uint8)
    frame[:2, :] = 200          # 1% 的亮部
    assert looks_blank(frame) is True


def test_crop_region():
    frame = np.arange(100 * 200 * 3, dtype=np.uint8).reshape(100, 200, 3)
    out = _crop(frame, (10, 20, 30, 40))
    assert out.shape == (40, 30, 3)
    assert np.array_equal(out, frame[20:60, 10:40])


def test_crop_out_of_bounds_raises():
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    with pytest.raises(CaptureError):
        _crop(frame, (150, 0, 100, 10))


def test_image_capture_full_and_region(tmp_path):
    import cv2
    rng = np.random.default_rng(2)
    img = rng.integers(0, 255, (80, 120, 3), dtype=np.uint8)
    path = str(tmp_path / "shot.png")
    cv2.imwrite(path, img)

    cap = ImageCapture(path)
    assert cap.size == (120, 80)
    assert cap.grab().shape == (80, 120, 3)
    assert cap.grab((10, 5, 20, 15)).shape == (15, 20, 3)


def test_missing_window_error_lists_what_is_open(monkeypatch):
    """標題有中英文兩種版本時，「自己去看標題列」很難照做——直接列給他看。"""
    from maplebot import capture as cap
    monkeypatch.setattr(cap, "list_windows",
                        lambda: [(1, "新楓之谷"), (2, "Discord"), (3, "  ")])
    hint = cap._open_windows_hint()
    assert "新楓之谷" in hint and "Discord" in hint
    assert "  " not in hint.replace("目前開著的視窗", "")


def test_window_hint_is_empty_off_windows(monkeypatch):
    from maplebot import capture as cap
    monkeypatch.setattr(cap, "list_windows", list)
    assert cap._open_windows_hint() == ""


def test_window_hint_truncates_a_long_list(monkeypatch):
    from maplebot import capture as cap
    monkeypatch.setattr(cap, "list_windows",
                        lambda: [(i, f"win{i}") for i in range(40)])
    hint = cap._open_windows_hint()
    assert "另有 28 個" in hint


def _FakeWindowCapture(method, logger=None):
    """繞過 __init__（那需要 mss 與真的遊戲視窗），只裝出 grab() 需要的狀態。"""
    from maplebot.capture import WindowCapture
    from maplebot.window import GameWindow

    cap = object.__new__(WindowCapture)
    cap._win = GameWindow(hwnd=1, title="t", origin=(0, 0), size=(4, 4))
    cap.method = method
    cap.fell_back = False
    cap._log = logger
    return cap

def test_printwindow_retries_a_transient_failure(monkeypatch):
    """PrintWindow 偶爾會回失敗（實測：另一支程式同時抓同一個視窗）。

    一幀沒抓到就拋例外，掛了一整晚的 bot 會因為這種一瞬間的事整個收工。
    """
    from maplebot import capture as cap_mod

    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    calls = []

    def flaky(_win):
        calls.append(1)
        return None if len(calls) == 1 else frame

    cap = _FakeWindowCapture("printwindow")
    monkeypatch.setattr(cap_mod, "grab_client", flaky)
    monkeypatch.setattr(cap_mod.time, "sleep", lambda _s: None)
    assert cap.grab().shape == (4, 4, 3)
    assert len(calls) == 2


def test_printwindow_falls_back_to_screen_when_it_breaks(monkeypatch):
    """PrintWindow 在某些客戶端會跑一跑就整個失效（實測連續 40 次全失敗，
    視窗還開著）。那時候拋例外等於讓掛了一整晚的 bot 收工，改成退回螢幕擷取。"""
    from maplebot import capture as cap_mod

    warned = []

    class _Log:
        def warning(self, msg, *a):
            warned.append(msg)

    cap = _FakeWindowCapture("printwindow", logger=_Log())
    screen = np.full((4, 4, 3), 7, dtype=np.uint8)
    monkeypatch.setattr(cap_mod, "grab_client", lambda _w: None)
    monkeypatch.setattr(cap_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(type(cap), "_grab_screen", lambda _self: screen)

    assert cap.grab()[0, 0, 0] == 7
    assert cap.method == "screen"
    assert warned and "螢幕擷取" in warned[0]
    assert cap.grab()[0, 0, 0] == 7          # 之後直接走螢幕擷取
    assert len(warned) == 1                  # 只吵一次
