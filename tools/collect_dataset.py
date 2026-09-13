"""YOLO 資料集蒐集：定時把 playfield 畫面存起來，供標註訓練用。

會自動跳過與上一張幾乎相同的幀（角色站著不動時），避免資料集
充滿重複樣本。存到 datasets/raw/ 後接 tools/autolabel.py。

**預設存 PNG（無損）**：描邊老師靠的就是 sprite 的純黑描邊，而 JPEG 會把
純黑壓成 (3,2,4) 這種值——實測一張圖的純黑像素會從 476 個掉到 306 個，
背景有雜訊時更慘（只剩 7 個）。組隊紅條也會被壓到碎成好幾塊，導致離線
標註時量不到角色位置。代價是檔案大約 5~10 倍，300 張約 200MB。
硬碟很緊才用 --format jpg，並搭配 tools/autolabel.py --scan-black 調門檻。

用法：
  python tools/collect_dataset.py --interval 2 --count 300
  python tools/collect_dataset.py --format jpg      # 省硬碟，標註品質較差
  python tools/collect_dataset.py --region minimap --count 400 --interval 1

**--region minimap** 是給小地圖玩家點模型用的（對應商業版的 CC\yellow\）。
存到 datasets/raw_minimap/，接 tools/autolabel.py --teacher minimap_dot。
小地圖很小，蒐 300~500 張、多走幾張地圖就夠；重點是**要包含同色地形的地圖**
（自由市場那種黃地板），那正是顏色偵測會壞、需要模型的畫面。
"""
import argparse
import os
import sys
import time
from datetime import datetime

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.capture import WindowCapture  # noqa: E402
from maplebot.config import load_config  # noqa: E402

OUT_DIR = os.path.join("datasets", "raw")
MINIMAP_OUT_DIR = os.path.join("datasets", "raw_minimap")


def _fingerprint(frame: np.ndarray) -> np.ndarray:
    """用來判斷「這張跟上一張幾乎一樣」的縮圖。

    小地圖本來就只有 128x58，放大到 160x96 等於插值出假細節，去重會失準
    （角色走了一格也看不出來）。所以縮圖不要比原圖大。
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    return cv2.resize(gray, (min(160, w), min(96, h))).astype(np.int16)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--interval", type=float, default=2.0, help="幾秒抓一次")
    ap.add_argument("--count", type=int, default=300, help="總共要存幾張")
    ap.add_argument("--min-diff", type=float, default=2.0,
                    help="與上一張的平均像素差低於此值就跳過（0 = 不去重）")
    ap.add_argument("--format", choices=["png", "jpg"], default="png",
                    help="png=無損（預設，描邊標註需要）；jpg=省硬碟但會破壞純黑")
    ap.add_argument("--region", default="playfield",
                    choices=["playfield", "minimap"],
                    help="要蒐集哪一塊。minimap 是給小地圖玩家點模型用的，"
                         "存到 datasets/raw_minimap/")
    args = ap.parse_args()

    cfg = load_config(args.config)
    cap = WindowCapture(cfg.window_title)
    region = cfg.region(args.region)
    out_dir = MINIMAP_OUT_DIR if args.region == "minimap" else OUT_DIR
    os.makedirs(out_dir, exist_ok=True)

    params = ([] if args.format == "png"
              else [cv2.IMWRITE_JPEG_QUALITY, 92])
    print(f"開始蒐集 {args.region}：每 {args.interval}s 檢查一次，目標 {args.count} 張"
          f"（{args.format}）-> {out_dir}")
    if args.region == "minimap":
        print("提示：多走幾張地圖，**一定要包含同色地形那種**（自由市場的黃地板）"
              "——那正是顏色偵測會壞、需要模型的畫面")
    else:
        print("提示：邊玩邊蒐集，多換幾個點位/地圖，畫面要包含要打的怪與空景")
    saved, skipped = 0, 0
    last_fp = None
    while saved < args.count:
        frame = cap.grab(region)
        fp = _fingerprint(frame)
        if last_fp is not None and args.min_diff > 0:
            if np.abs(fp - last_fp).mean() < args.min_diff:
                skipped += 1
                time.sleep(args.interval)
                continue
        last_fp = fp
        name = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        cv2.imwrite(os.path.join(out_dir, f"{name}.{args.format}"), frame, params)
        saved += 1
        print(f"\r已存 {saved}/{args.count}（跳過重複 {skipped}）", end="", flush=True)
        time.sleep(args.interval)
    if args.region == "minimap":
        print(f"\n完成。下一步（先看老師標得對不對）：python tools/autolabel.py "
              f"--images {out_dir} --teacher minimap_dot --preview 6")
    else:
        print("\n完成。下一步（先看老師標得對不對）："
              "python tools/autolabel.py --preview 6")
    return 0


if __name__ == "__main__":
    sys.exit(main())
