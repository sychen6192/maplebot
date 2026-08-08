"""按鍵診斷：確認模擬按鍵到底有沒有送進遊戲。

「log 顯示按了鍵、但遊戲毫無反應」是最常見也最難自己判斷的問題，
原因通常是這三個之一，這支工具會一次全部檢查：

  1. 忘了拿掉 --dry-run（那本來就不會送鍵）
  2. 遊戲用系統管理員執行、Python 沒有 -> Windows UIPI 擋掉輸入
  3. 遊戲視窗不在前景 -> 按鍵跑到別的視窗去了

用法（在遊戲機上，先把遊戲開起來）：
  python tools/test_keys.py            # 預設測方向鍵右
  python tools/test_keys.py --key x --times 3
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.config import load_config  # noqa: E402
from maplebot.control.input_win import IS_WINDOWS, Keyboard  # noqa: E402
from maplebot.window import find_game_window  # noqa: E402


def _is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    import ctypes
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _foreground_title() -> str:
    if not IS_WINDOWS:
        return ""
    import ctypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--key", default="right", help="要測試的按鍵")
    ap.add_argument("--times", type=int, default=5)
    ap.add_argument("--delay", type=float, default=5.0, help="幾秒後開始（給你切到遊戲）")
    args = ap.parse_args()

    if not IS_WINDOWS:
        print("這支工具只能在 Windows 上跑")
        return 2

    cfg = load_config(args.config)
    print(f"設定檔: {' + '.join(cfg.sources)}")
    print("=== 環境檢查 ===")
    print(f"Python 以系統管理員執行: {'是' if _is_admin() else '否'}")

    win = find_game_window(cfg.window_title)
    if win is None:
        print(f"✗ 找不到標題含「{cfg.window_title}」的視窗——先開遊戲，"
              "並確認 config 的 window.title 正確")
        return 1
    print(f"✓ 找到遊戲視窗: {win.title!r}（client 區 {win.size[0]}x{win.size[1]}）")

    print(f"\n{args.delay:.0f} 秒後開始送鍵，請現在切到遊戲視窗並讓角色站在空地…")
    for i in range(int(args.delay), 0, -1):
        print(f"\r  {i}…", end="", flush=True)
        time.sleep(1)
    print()

    fg = _foreground_title()
    fg_ok = cfg.window_title.lower() in fg.lower()
    print(f"目前前景視窗: {fg!r} {'✓' if fg_ok else '✗ 不是遊戲視窗！'}")

    kb = Keyboard()
    print(f"\n送出 {args.key} x{args.times}…")
    for _ in range(args.times):
        kb.tap(args.key, 0.25)
        time.sleep(0.25)
    kb.release_all()

    print("\n=== 結果 ===")
    print(f"送出事件數: {kb.sent}｜失敗: {kb.failures}")
    if kb.failures:
        err = kb.last_error()
        print(f"✗ SendInput 被拒絕（錯誤碼 {err}）")
        if err == 5:
            print("  原因：遊戲以系統管理員執行，而這個終端機沒有。")
            print("  解法：關掉終端機，用「以系統管理員身分執行」重開 PowerShell，再跑一次。")
        else:
            print("  請把這個錯誤碼回報。")
        return 1

    print("✓ SendInput 全部成功送出（作業系統層面沒被擋）")
    if not fg_ok:
        print("⚠ 但前景視窗不是遊戲——按鍵送到別的視窗去了。"
              "跑正式程式時記得先點一下遊戲視窗。")
        return 1
    print("\n角色有動嗎？")
    print("  有  -> 按鍵沒問題。遊戲沒反應的話問題在別處"
          "（先確認沒開 --dry-run，再用 debug_view 看怪物偵測）")
    print("  沒有 -> 作業系統有送出但遊戲沒收：確認遊戲是視窗模式、"
          "該按鍵在遊戲內確實有綁定、角色沒有處於無法移動的狀態")
    return 0


if __name__ == "__main__":
    sys.exit(main())
