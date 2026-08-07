"""怪物模板擷取工具：從畫面框選怪物，存成模板 PNG。

用法：
  python tools/grab_template.py --name snail            # 從遊戲視窗抓
  python tools/grab_template.py --name snail --source shot.png

建議：同一隻怪抓 2~3 張不同動作幀（站立/移動），框選時貼緊怪物身體、
避免框到背景大片區域，匹配會更穩。
"""
import argparse
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.capture import ImageCapture, WindowCapture  # noqa: E402
from maplebot.config import load_config  # noqa: E402

OUT_DIR = os.path.join("data", "templates", "mobs")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="怪物名稱（檔名用）")
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--source", default="")
    args = ap.parse_args()

    cap = ImageCapture(args.source) if args.source else WindowCapture(load_config(args.config).window_title)
    frame = cap.grab()

    os.makedirs(OUT_DIR, exist_ok=True)
    n = 0
    while True:
        title = f"框選 {args.name}（Enter 確認，c 結束）"
        x, y, w, h = cv2.selectROI(title, frame, showCrosshair=True)
        cv2.destroyWindow(title)
        if w == 0 or h == 0:
            break
        n += 1
        path = os.path.join(OUT_DIR, f"{args.name}_{n:02d}.png")
        cv2.imwrite(path, frame[y:y + h, x:x + w])
        print(f"已存 {path}（{w}x{h}）")
    print(f"共存了 {n} 張模板")
    return 0


if __name__ == "__main__":
    sys.exit(main())
