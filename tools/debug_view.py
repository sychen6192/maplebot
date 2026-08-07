"""即時偵錯視窗：把所有辨識結果疊在畫面上顯示。

用法：
  python tools/debug_view.py                          # 對著遊戲視窗跑
  python tools/debug_view.py --source shot.png        # 看單張截圖的辨識結果
按 q 離開。
"""
import argparse
import os
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.capture import ImageCapture, WindowCapture  # noqa: E402
from maplebot.config import load_config, load_profile  # noqa: E402
from maplebot.vision import minimap, status  # noqa: E402
from maplebot.vision.mobs import make_detector  # noqa: E402

GREEN = (0, 255, 0)
RED = (0, 0, 255)
YELLOW = (0, 255, 255)
WHITE = (255, 255, 255)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--profile", default="config/profiles/example.yaml")
    ap.add_argument("--source", default="")
    args = ap.parse_args()

    cfg = load_config(args.config)
    profile = load_profile(args.profile)
    cap = ImageCapture(args.source) if args.source else WindowCapture(cfg.window_title)
    detector = make_detector(cfg.vision, profile.templates_dir)

    while True:
        t0 = time.monotonic()
        frame = cap.grab()
        canvas = frame.copy()

        for name, (x, y, w, h) in cfg.regions.items():
            cv2.rectangle(canvas, (x, y), (x + w, y + h), WHITE, 1)
            cv2.putText(canvas, name, (x, max(y - 3, 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, WHITE, 1)

        if "minimap" in cfg.regions:
            mx, my, mw, mh = cfg.regions["minimap"]
            mm = frame[my:my + mh, mx:mx + mw]
            player = minimap.find_player(mm, cfg.vision)
            if player:
                cv2.circle(canvas, (mx + player[0], my + player[1]), 4, GREEN, 2)
            for ox, oy in minimap.find_others(mm, cfg.vision):
                cv2.circle(canvas, (mx + ox, my + oy), 4, RED, 2)

        texts = []
        for kind in ("hp", "mp", "exp"):
            rn = f"{kind}_bar"
            if rn in cfg.regions:
                x, y, w, h = cfg.regions[rn]
                ratio = status.bar_ratio(frame[y:y + h, x:x + w],
                                         cfg.vision.bar_colors.get(kind, "red"))
                texts.append(f"{kind.upper()} {ratio:.0%}" if ratio is not None else f"{kind.upper()} ?")

        if "playfield" in cfg.regions:
            px, py, pw, ph = cfg.regions["playfield"]
            for mob in detector.detect(frame[py:py + ph, px:px + pw]):
                x1 = px + mob.cx - mob.w // 2
                y1 = py + mob.cy - mob.h // 2
                cv2.rectangle(canvas, (x1, y1), (x1 + mob.w, y1 + mob.h), YELLOW, 2)
                cv2.putText(canvas, f"{mob.name} {mob.score:.2f}", (x1, max(y1 - 4, 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, YELLOW, 1)

        fps = 1.0 / max(time.monotonic() - t0, 1e-6)
        texts.append(f"{fps:.1f} FPS")
        cv2.putText(canvas, " | ".join(texts), (8, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, GREEN, 2)

        cv2.imshow("maplebot debug (q to quit)", canvas)
        if cv2.waitKey(200 if args.source else 1) & 0xFF == ord("q"):
            break
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
