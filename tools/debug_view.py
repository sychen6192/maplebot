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

WINDOW = "maplebot debug (q to quit)"
MAX_DISPLAY_W = 1280   # 顯示視窗最大寬度，避免大解析度截圖塞爆螢幕


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--profile", default="config/profiles/example.yaml")
    ap.add_argument("--source", default="")
    args = ap.parse_args()

    cfg = load_config(args.config)
    profile = load_profile(args.profile)
    cap = ImageCapture(args.source) if args.source else \
        WindowCapture(cfg.window_title, cfg.capture_method)
    print(f"擷取尺寸: {cap.size[0]}x{cap.size[1]}｜擷取方式: {cap.method}")
    if cap.method == "screen":
        print("⚠ 此客戶端不支援 PrintWindow，改用螢幕擷取。"
              "請把這個偵錯視窗拖到遊戲畫面以外，否則會拍到視窗自己（畫面像是一直放大）")

    if cfg.minimap_auto:
        from maplebot.vision.locate import BR_NAME, TL_NAME, find_minimap, load_ui_template
        tl = load_ui_template(cfg.vision.ui_templates_dir, TL_NAME)
        br = load_ui_template(cfg.vision.ui_templates_dir, BR_NAME)
        region = find_minimap(cap.grab(), tl, br, cfg.vision.minimap_border) \
            if tl is not None and br is not None else None
        if region:
            cfg.regions["minimap"] = region
            print(f"小地圖自動定位: {list(region)}")
        else:
            print("警告：小地圖自動定位失敗（缺角落模板或分數過低）")

    perceiver = Perceiver(cfg, make_detector(cfg.vision, profile.templates_dir))
    rt = fsm.Runtime()
    pf = cfg.region("playfield")
    center = (pf[2] // 2, pf[3] // 2)

    # 只建立「一個」視窗；不這樣做的話，某些 Windows OpenCV 版本會在
    # 迴圈裡每幀開一個新視窗（畫面上會不斷疊出新視窗）。
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    delay = 200 if args.source else 30

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

        # 大解析度截圖等比例縮到最大寬度以內再顯示（座標讀值不受影響，
        # 疊框都畫在原尺寸 canvas 上）
        h, w = canvas.shape[:2]
        if w > MAX_DISPLAY_W:
            scale = MAX_DISPLAY_W / w
            shown = cv2.resize(canvas, (MAX_DISPLAY_W, int(h * scale)))
        else:
            shown = canvas
        cv2.imshow(WINDOW, shown)

        key = cv2.waitKey(delay) & 0xFF
        if key == ord("q"):
            break
        # 按視窗右上角的 X 關閉也結束
        if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            break
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
