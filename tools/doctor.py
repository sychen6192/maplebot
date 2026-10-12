"""環境自檢：開跑之前先把「一定會失敗」的狀況一次講完。

  python tools/doctor.py
  python tools/doctor.py --profile config/profiles/multilevel.yaml
  python tools/doctor.py --source shot.png    # 順便用一張截圖驗血條 ROI

有任何 ❌ 就回傳 exit code 1，方便寫進批次檔或 CI。
檢查邏輯全在 maplebot/diagnostics/doctor.py（純函式、有測試），這裡只負責印。
"""
import argparse
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.diagnostics import doctor  # noqa: E402
from maplebot.log import console_safe  # noqa: E402

console_safe()   # cp950 終端機印 ✅/❌ 不炸掉


def _can_import(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        # ImportError 以外的例外也算不可用（例如 cv2 缺系統函式庫時會丟 OSError）
        return False


def _section(title: str) -> None:
    print(f"\n── {title} " + "─" * max(4, 46 - len(title)))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="maplebot 環境自檢")
    p.add_argument("--config", default="config/default.yaml")
    p.add_argument("--profile", default="config/profiles/example.yaml")
    p.add_argument("--source", default="",
                   help="一張遊戲截圖：拿來跟設定的血條 ROI 對答案。"
                        "留空就跳過（那項檢查需要真的畫面）")
    args = p.parse_args(argv)

    print("maplebot 環境自檢")
    sections = doctor.run_all(args.config, args.profile, _can_import,
                              source=args.source)
    rep = doctor.Report()
    for title, checks in sections:
        _section(title)
        for c in checks:
            print("  " + rep.add(c).line())
    if doctor.config_failed(sections):
        print(f"\n{rep.summary()}——設定檔讀不起來，後面的檢查沒有意義")
        return 1

    print()
    print("═" * 52)
    print(rep.summary())
    if rep.failed:
        print("\n先把 ❌ 修掉再開始掛機——上面每一項都附了要動哪裡。")
        return 1
    if rep.count(doctor.WARN):
        print("\n沒有致命問題，可以開始。⚠️ 的部分是「少了某個功能」而不是壞掉。")
    else:
        print("\n一切就緒。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
