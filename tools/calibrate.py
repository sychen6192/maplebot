"""校正工具：框選小地圖 / HP / MP / EXP / playfield 區域，輸出 YAML。

用法：
  python tools/calibrate.py                     # 從遊戲視窗抓一張畫面
  python tools/calibrate.py --source shot.png   # 用現成截圖校正

每個區域會跳出框選視窗：拖曳出矩形後按 Enter 確認、按 c 跳過。
結束後把輸出的 regions 區塊貼回 config/default.yaml。
"""
import argparse
import os
import sys

import cv2
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.capture import ImageCapture, WindowCapture  # noqa: E402
from maplebot.config import load_config  # noqa: E402

REGIONS = [
    ("minimap", "小地圖（含玩家黃點的地圖範圍）"),
    ("hp_bar", "HP 條（只框紅色條本體，不含文字外框）"),
    ("mp_bar", "MP 條（只框藍色條本體）"),
    ("exp_bar", "EXP 條（可跳過）"),
    ("playfield", "遊戲主畫面（不含下方 UI 列）"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--source", default="", help="用靜態截圖代替遊戲視窗")
    ap.add_argument("--write", nargs="?", const="auto", default="",
                    help="直接寫進 config/local.yaml（不用手動複製貼上）")
    args = ap.parse_args()

    if args.source:
        cap = ImageCapture(args.source)
    else:
        cfg = load_config(args.config)
        cap = WindowCapture(cfg.window_title)

    frame = cap.grab()
    print("在每個視窗中拖曳框選後按 Enter/Space 確認；直接按 c 可跳過該區域\n")
    results = {}
    for name, hint in REGIONS:
        title = f"{name}: {hint}"
        x, y, w, h = cv2.selectROI(title, frame, showCrosshair=True)
        cv2.destroyWindow(title)
        if w > 0 and h > 0:
            results[name] = [int(x), int(y), int(w), int(h)]
            print(f"  {name}: [{x}, {y}, {w}, {h}]")
        else:
            print(f"  {name}: 跳過")

    # 記下校正當下的視窗大小：之後視窗尺寸變了，主程式會直接告訴你要重校正，
    # 而不是把 HP 讀成 0% 然後判定瀕死停機
    patch = {"window": {"calibrated_for": [cap.size[0], cap.size[1]]},
             "regions": results}
    if args.write:
        from maplebot.config import resolve_local_path
        from maplebot.gui.settings import merge_into
        path = resolve_local_path(args.config) if args.write == "auto" else args.write
        merge_into(path, patch)
        print(f"\n已寫入 {path}（原有的其他設定都保留）")
        return 0

    print("\n=== 把下面貼進 config/local.yaml（建議）或 default.yaml ===\n")
    print(yaml.safe_dump(patch, allow_unicode=True, sort_keys=False).rstrip())
    print("\n（下次可以加 --write 直接寫檔，不用複製貼上）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
