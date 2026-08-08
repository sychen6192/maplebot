"""偵錯視覺化：用與主程式完全相同的 Perceiver 做辨識，把結果疊在畫面上。

顯示內容：各 ROI 框、玩家綠圈/其他人紅圈（含小地圖座標數值）、
HP/MP/EXP 比例、怪物框、狀態機當下會做的決策。

用法：
  python tools/debug_view.py                       # 即時視窗（按 q 離開）
  python tools/debug_view.py --snapshot out.png    # 只存一張標註圖，不開視窗
  python tools/debug_view.py --source shot.png     # 用靜態截圖當來源

擷取方式若是 screen（客戶端不支援 PrintWindow），偵錯視窗蓋住遊戲時會
拍到自己造成畫面遞迴疊圖——程式會自動把視窗挪開，挪不開時請用 --snapshot。
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
from maplebot.window import pick_free_position, virtual_screen  # noqa: E402

GREEN = (0, 255, 0)
RED = (0, 0, 255)
YELLOW = (0, 255, 255)
WHITE = (255, 255, 255)

WINDOW = "maplebot debug (q to quit)"
MAX_DISPLAY_W = 1280   # 顯示視窗最大寬度，避免大解析度畫面塞爆螢幕
TITLE_BAR_H = 40       # 估算標題列高度，找擺放位置時要算進去


def _pct(v):
    return f"{v:.0%}" if v is not None else "?"


def _display_size(capture_size):
    w, h = capture_size
    if w <= MAX_DISPLAY_W:
        return w, h
    return MAX_DISPLAY_W, int(h * MAX_DISPLAY_W / w)


def annotate(frame, state, action, cfg, fps=None):
    """把辨識結果畫上去（畫在原尺寸畫面，座標讀值才準）。"""
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

    header = (f"HP {_pct(state.hp)} | MP {_pct(state.mp)} | EXP {_pct(state.exp)} | "
              f"mobs {len(state.mobs)}")
    if fps is not None:
        header += f" | {fps:.0f} FPS"
    header += f" | -> {type(action).__name__}"
    cv2.putText(canvas, header, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, GREEN, 2)
    return canvas


def _place_away_from_game(cap, disp_w, disp_h) -> None:
    """螢幕擷取模式下視窗蓋住遊戲就會拍到自己，自動挪到遊戲畫面以外。"""
    spot = pick_free_position((*cap.origin, *cap.size),
                              (disp_w, disp_h + TITLE_BAR_H), virtual_screen())
    if spot:
        cv2.moveWindow(WINDOW, spot[0], spot[1])
        print(f"已把偵錯視窗移到 {spot}，避免拍到自己造成畫面遞迴疊圖")
    else:
        print("⚠ 螢幕空間不足以把偵錯視窗放到遊戲畫面以外。"
              "請改用 --snapshot out.png（不開視窗），或縮小遊戲視窗／用副螢幕")


def _report(state, action):
    print(f"  HP {_pct(state.hp)} | MP {_pct(state.mp)} | EXP {_pct(state.exp)}")
    print(f"  玩家小地圖座標: {state.player}｜其他玩家: {len(state.others)}")
    print(f"  偵測到怪物: {len(state.mobs)}")
    for mob in state.mobs[:5]:
        print(f"    - {mob.name} ({mob.cx},{mob.cy}) 分數 {mob.score:.2f}")
    print(f"  當下決策: {type(action).__name__}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--profile", default="config/profiles/example.yaml")
    ap.add_argument("--source", default="", help="用靜態截圖當畫面來源")
    ap.add_argument("--snapshot", default="",
                    help="只抓一幀存成標註圖後結束，完全不開視窗")
    ap.add_argument("--track", action="store_true",
                    help="持續印出玩家小地圖座標，用來讀巡邏點（Ctrl+C 結束）")
    args = ap.parse_args()

    cfg = load_config(args.config)
    print(f"設定檔: {' + '.join(cfg.sources)}")
    profile = load_profile(args.profile)
    cap = ImageCapture(args.source) if args.source else \
        WindowCapture(cfg.window_title, cfg.capture_method)
    print(f"擷取尺寸: {cap.size[0]}x{cap.size[1]}｜擷取方式: {cap.method}")
    if cap.method == "screen":
        print("⚠ 此客戶端不支援 PrintWindow，改用螢幕擷取："
              "任何蓋住遊戲的視窗都會被拍進去")

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

    if args.track:
        mm_w = cfg.regions.get("minimap", (0, 0, 0, 0))[2]
        print("\n讀巡邏點座標（x 值）＋ 監看 HP/MP 有沒有亂跳。Ctrl+C 結束。")
        print("HP 欄若在正常玩的情況下突然掉到很低，就是血條 ROI 有問題——"
              "bot 會誤判成瀕死而停機。\n")
        samples, lost, hp_min, hp_drops = 0, 0, 1.0, 0
        prev_hp = None
        try:
            while True:
                now = time.monotonic()
                state = perceiver.perceive(cap.grab(), now)
                samples += 1
                if state.hp is not None:
                    hp_min = min(hp_min, state.hp)
                    if prev_hp is not None and state.hp < prev_hp - 0.3:
                        hp_drops += 1      # 一幀掉超過 30% = 幾乎確定是誤讀
                    prev_hp = state.hp
                if state.player:
                    x, y = state.player
                    pos = int(x / mm_w * 30) if mm_w else 0
                    bar = "".join("●" if i == pos else "─" for i in range(30))
                    print(f"\r  x={x:4d} y={y:4d} [{bar}] "
                          f"HP {_pct(state.hp)} MP {_pct(state.mp)} "
                          f"｜最低 HP {hp_min:.0%} 突降 {hp_drops} 次",
                          end="", flush=True)
                else:
                    lost += 1
                    print(f"\r  找不到玩家點…（{lost}/{samples} 幀）"
                          f" HP {_pct(state.hp)} MP {_pct(state.mp)}    ",
                          end="", flush=True)
                time.sleep(0.3)
        except KeyboardInterrupt:
            print(f"\n\n=== {samples} 幀統計 ===")
            print(f"找不到玩家點: {lost} 幀"
                  f"{'（小地圖 ROI 或顏色參數要調）' if lost else ''}")
            print(f"HP 最低讀值: {hp_min:.0%}｜單幀突降 >30%: {hp_drops} 次")
            if hp_drops:
                print("⚠ 血條讀值不穩：重跑 tools/calibrate.py 只框紅色條本體"
                      "（不要含數字、外框、旁邊的 UI）。")
                print("  暫時解法：safety.critical_hp_frames 調大（預設 3），"
                      "或 safety.critical_hp_ratio 調低")
        return 0

    if args.snapshot:
        now = time.monotonic()
        frame = cap.grab()
        state = perceiver.perceive(frame, now)
        action = fsm.decide(state, cfg, profile, rt, now, center)
        cv2.imwrite(args.snapshot, annotate(frame, state, action, cfg))
        print(f"\n已存標註圖: {args.snapshot}")
        _report(state, action)
        return 0

    # 只建立「一個」視窗；不這樣做的話，某些 Windows OpenCV 版本會在
    # 迴圈裡每幀開一個新視窗（畫面上會不斷疊出新視窗）。
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL | cv2.WINDOW_GUI_NORMAL)
    disp_w, disp_h = _display_size(cap.size)
    cv2.resizeWindow(WINDOW, disp_w, disp_h)
    if cap.method == "screen":
        _place_away_from_game(cap, disp_w, disp_h)
    delay = 200 if args.source else 30

    while True:
        t0 = time.monotonic()
        frame = cap.grab()
        state = perceiver.perceive(frame, t0)
        action = fsm.decide(state, cfg, profile, rt, t0, center)
        fps = 1.0 / max(time.monotonic() - t0, 1e-6)
        canvas = annotate(frame, state, action, cfg, fps)

        shown = canvas if canvas.shape[1] <= disp_w else \
            cv2.resize(canvas, (disp_w, disp_h))
        cv2.imshow(WINDOW, shown)

        if cv2.waitKey(delay) & 0xFF == ord("q"):
            break
        if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            break  # 按了視窗右上角的 X
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
