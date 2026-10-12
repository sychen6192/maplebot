"""校正工具：框選小地圖 / HP / MP / EXP / playfield 這幾個 region，輸出 YAML。

用法：
  python tools/calibrate.py --write             # 從遊戲視窗抓一張畫面，寫回 config/local.yaml
  python tools/calibrate.py                     # 只印出 regions 區塊，自己貼
  python tools/calibrate.py --source shot.png   # 用現成截圖校正

每個 region 會跳出框選視窗：拖曳出矩形後按 Enter 確認、按 c 跳過。
"""
import argparse
import os
import sys

import cv2
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.capture import ImageCapture, WindowCapture  # noqa: E402
from maplebot.config import load_config  # noqa: E402
from maplebot.vision.status import bar_ratio  # noqa: E402
from maplebot.vision.statusbar import find_status_bars  # noqa: E402

BAR_COLORS = {"hp_bar": "red", "mp_bar": "blue", "exp_bar": "yellow"}

REGIONS = [
    # 只框地圖畫布：標題列上有固定不動的黃色圖示（自由市場那顆），框進來的話
    # 玩家點偵測會一直鎖在它身上（實測 fixture 整張框 -> 讀成 (20, 14)、不會動）
    ("minimap", "小地圖的地圖畫布本體（不含標題列與圖示；角色黃點要在裡面）"),
    ("hp_bar", "HP 條（只框紅色條本體，不含文字外框）"),
    ("mp_bar", "MP 條（只框藍色條本體）"),
    ("exp_bar", "EXP 條（可跳過）"),
    ("playfield", "角色與怪物活動的那一塊（不含下方狀態列）"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--source", default="", help="用靜態截圖代替遊戲視窗")
    ap.add_argument("--write", nargs="?", const="auto", default="",
                    help="直接寫進 config/local.yaml（不用手動複製貼上）")
    ap.add_argument("--no-auto", action="store_true",
                    help="狀態列也自己框（預設會自動找紅/藍/黃三條）")
    args = ap.parse_args()

    if args.source:
        cap = ImageCapture(args.source)
    else:
        cfg = load_config(args.config)
        cap = WindowCapture(cfg.window_title)

    frame = cap.grab()
    results = {}

    # 三條狀態列是遊戲畫的 UI，顏色固定、又永遠並排在畫面底部——這種東西
    # 沒道理要人拿滑鼠去拉。先自動找，找到就不用框，只剩小地圖與 playfield。
    auto = find_status_bars(frame)
    if auto and not args.no_auto:
        print("已自動找到狀態列（要自己框的話加 --no-auto）：")
        for name, box in auto.items():
            results[name] = [int(v) for v in box]
            ratio = bar_ratio(frame[box[1]:box[1] + box[3], box[0]:box[0] + box[2]],
                              BAR_COLORS[name])
            print(f"  {name}: {results[name]}  讀出來是 {ratio:.0%}"
                  "（跟遊戲裡顯示的對一下，差很多就是找錯了）")
        print()

    todo = [(n, h) for n, h in REGIONS if n not in results]
    print("在每個視窗中拖曳框選後按 Enter/Space 確認；直接按 c 可跳過這個 region\n")
    for name, hint in todo:
        # 提示也印到輸出：控制台會把它收進日誌，而 cv2 視窗標題的中文在某些
        # Windows 上會變亂碼
        print(f"框選 {name}：{hint}")
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
