"""環境自檢：開跑之前先把「一定會失敗」的狀況一次講完。

  python tools/doctor.py
  python tools/doctor.py --profile config/profiles/multilevel.yaml

有任何 ❌ 就回傳 exit code 1，方便寫進批次檔或 CI。
檢查邏輯全在 maplebot/doctor.py（純函式、有測試），這裡只負責印。
"""
import argparse
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot import doctor  # noqa: E402
from maplebot.config import ConfigError, load_config, load_profile  # noqa: E402


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
    args = p.parse_args(argv)

    rep = doctor.Report()
    print("maplebot 環境自檢")

    _section("執行環境")
    for c in [doctor.check_python(), doctor.check_platform()]:
        print("  " + rep.add(c).line())
    for c in doctor.check_modules(_can_import):
        print("  " + rep.add(c).line())
    print("  " + rep.add(doctor.check_writable()).line())

    _section("設定檔")
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print("  " + rep.add(doctor.Check(
            "全域設定", doctor.FAIL, str(e),
            f"修正 {args.config}（YAML 縮排/欄位型別），"
            "或跟版本控管裡的預設檔比對")).line())
        print(f"\n{rep.summary()}——設定檔讀不起來，後面的檢查沒有意義")
        return 1
    print("  " + rep.add(doctor.Check("全域設定", doctor.OK,
                                      " + ".join(cfg.sources))).line())
    for c in doctor.check_regions(cfg):
        print("  " + rep.add(c).line())
    print("  " + rep.add(doctor.check_detector(cfg)).line())
    print("  " + rep.add(doctor.check_game_process(cfg)).line())

    _section(f"Profile（{args.profile}）")
    try:
        profile = load_profile(args.profile)
    except ConfigError as e:
        print("  " + rep.add(doctor.Check("Profile", doctor.FAIL, str(e),
                                          f"修正 {args.profile}")).line())
    else:
        print("  " + rep.add(doctor.Check("Profile", doctor.OK, profile.name)).line())
        for c in doctor.check_profile(profile, cfg.safety.critical_hp_ratio):
            print("  " + rep.add(c).line())

    window_checks = doctor.check_window(cfg)
    if window_checks:
        _section("遊戲視窗")
        for c in window_checks:
            print("  " + rep.add(c).line())

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
