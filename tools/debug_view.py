"""即時偵錯視窗：用與主程式完全相同的 Perceiver 做辨識，疊框顯示。

顯示內容：各 ROI 框、玩家綠圈/其他人紅圈（含小地圖座標數值）、
HP/MP/EXP 比例、怪物框、狀態機當下會做的決策。

用法：
  python tools/debug_view.py                          # 對著遊戲視窗跑
  python tools/debug_view.py --source shot.png        # 看單張截圖
按 q 離開。
"""
import argparse
import os
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.brain import fsm  # noqa: E402
from maplebot.capture import ImageCapture, WindowCapture  # noqa: E402
from maplebot.config import load_config, load_profile  # noqa: E402
from maplebot.perception import Perceiver  # noqa: E402
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
    perceiver = Perceiver(cfg, make_detector(cfg.vision, profile.templates_dir))
    rt = fsm.Runtime()
    pf = cfg.region("playfield")
    center = (pf[2] // 2, pf[3] // 2)

    while True:
        t0 = time.monotonic()
        frame = cap.grab()
        state = perceiver.perceive(frame, t0)
        action = fsm.decide(state, cfg, profile, rt, t0, center)
        canvas = frame.copy()

        for name, (x, y, w, h) in cfg.regions.items():
            cv2.rectangle(canvas, (x, y), (x + w, y + h), WHITE, 1)
            cv2.putText(canvas, name, (x, max(y - 3, 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, WHITE, 1)

        if "minimap" in cfg.regions:
            mx, my = cfg.regions["minimap"][:2]
            if state.player:
                px, py = state.player
                cv2.circle(canvas, (mx + px, my + py), 4, GREEN, 2)
                cv2.putText(canvas, f"({px},{py})", (mx + px + 6, my + py),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, GREEN, 1)
            for ox, oy in state.others:
                cv2.circle(canvas, (mx + ox, my + oy), 4, RED, 2)

        if "playfield" in cfg.regions:
            fx, fy = cfg.regions["playfield"][:2]
            for mob in state.mobs:
                x1 = fx + mob.cx - mob.w // 2
                y1 = fy + mob.cy - mob.h // 2
                cv2.rectangle(canvas, (x1, y1), (x1 + mob.w, y1 + mob.h), YELLOW, 2)
                cv2.putText(canvas, f"{mob.name} {mob.score:.2f}", (x1, max(y1 - 4, 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, YELLOW, 1)

        def pct(v):
            return f"{v:.0%}" if v is not None else "?"

        fps = 1.0 / max(time.monotonic() - t0, 1e-6)
        header = (f"HP {pct(state.hp)} | MP {pct(state.mp)} | EXP {pct(state.exp)} | "
                  f"mobs {len(state.mobs)} | {fps:.0f} FPS | -> {type(action).__name__}")
        cv2.putText(canvas, header, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, GREEN, 2)

        cv2.imshow("maplebot debug (q to quit)", canvas)
        if cv2.waitKey(200 if args.source else 1) & 0xFF == ord("q"):
            break
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
