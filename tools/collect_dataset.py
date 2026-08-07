"""YOLO 資料集蒐集：定時把 playfield 畫面存成 jpg，供標註訓練用。

用法：
  python tools/collect_dataset.py --interval 2 --count 300

存到 datasets/raw/。之後用 labelImg / Roboflow / CVAT 標怪物框，
再用 ultralytics 訓練（見 README「進階：ML 感知層」）。
"""
import argparse
import os
import sys
import time
from datetime import datetime

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.capture import WindowCapture  # noqa: E402
from maplebot.config import load_config  # noqa: E402

OUT_DIR = os.path.join("datasets", "raw")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--interval", type=float, default=2.0, help="幾秒存一張")
    ap.add_argument("--count", type=int, default=300, help="總共存幾張")
    args = ap.parse_args()

    cfg = load_config(args.config)
    cap = WindowCapture(cfg.window_title)
    region = cfg.region("playfield")
    os.makedirs(OUT_DIR, exist_ok=True)

    print(f"開始蒐集：每 {args.interval}s 一張，共 {args.count} 張 -> {OUT_DIR}")
    for i in range(args.count):
        frame = cap.grab(region)
        name = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        cv2.imwrite(os.path.join(OUT_DIR, f"{name}.jpg"), frame,
                    [cv2.IMWRITE_JPEG_QUALITY, 92])
        print(f"\r{i + 1}/{args.count}", end="", flush=True)
        time.sleep(args.interval)
    print("\n完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
