"""錄路線：你自己把路線走一遍，程式生出 profile 的 patrol.waypoints。

跟 GUI 的「錄製路線」同一套引擎（maplebot/recorder.py + route.py），
給不開 GUI 的人用：

  python tools/record_route.py --profile config/profiles/弓箭手訓練場一.yaml
      按 Enter 開始，走完一圈按 Ctrl+C，waypoints 區塊直接印在終端機

  python tools/record_route.py --profile ... --seconds 60 --out route.yaml
      錄固定秒數後自動停止，並把區塊另存一份（適合被其他程式呼叫）

錄的時候照平常的方式玩就好：左右走到底、該爬繩就爬繩、站定放的技能
會掛到該巡邏點的 keys。方向鍵不會被記進 keys——那是「怎麼走到這裡」，
執行時由巡邏邏輯自己決定。

注意：遊戲若以系統管理員執行，Windows 會把打進遊戲的按鍵對非提權行程
隱藏（UIPI），這支程式就只錄得到位置、錄不到你按的技能鍵。要連按鍵
一起錄，請在提權終端跑（跟 main.py 實機執行的要求相同）。只想量座標
的話，非提權跑也完全可用。
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.capture import ImageCapture, WindowCapture  # noqa: E402
from maplebot.config import load_config, load_profile  # noqa: E402
from maplebot.perception import Perceiver  # noqa: E402
from maplebot.recorder import KeyWatcher, Recorder  # noqa: E402
from maplebot.route import compress, coverage, describe, to_yaml_block  # noqa: E402
from maplebot.vision.mobs import make_detector  # noqa: E402


def _locate_minimap(cfg, cap) -> None:
    """跟 debug_view/runner 相同的小地圖自動定位（minimap_auto 開著才做）。"""
    if not cfg.minimap_auto:
        return
    from maplebot.vision.locate import BR_NAME, TL_NAME, find_minimap, load_ui_template
    tl = load_ui_template(cfg.vision.ui_templates_dir, TL_NAME)
    br = load_ui_template(cfg.vision.ui_templates_dir, BR_NAME)
    region = find_minimap(cap.grab(), tl, br, cfg.vision.minimap_border) \
        if tl is not None and br is not None else None
    if region:
        cfg.regions["minimap"] = region
        print(f"小地圖自動定位: {list(region)}")
    else:
        print("警告：小地圖自動定位失敗，沿用 config 裡的 minimap ROI")


def main() -> int:
    ap = argparse.ArgumentParser(description="把「自己走一遍」錄成 patrol.waypoints")
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--profile", default="config/profiles/example.yaml",
                    help="拿 tolerance/y_tolerance/jump_key 當壓縮參數的 profile")
    ap.add_argument("--source", default="", help="用靜態截圖當來源（離線測試用）")
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="錄固定秒數後自動停止（0 = 手動 Ctrl+C 結束）")
    ap.add_argument("--interval", type=float, default=0.2,
                    help="取樣間隔秒數。路線只需要轉折點，不用錄太密")
    ap.add_argument("--out", default="", help="把 patrol 區塊另存到這個檔案")
    ap.add_argument("--no-wait", action="store_true",
                    help="不等 Enter 直接開錄（給程式呼叫用）")
    args = ap.parse_args()

    cfg = load_config(args.config)
    profile = load_profile(args.profile)
    cap = ImageCapture(args.source) if args.source else \
        WindowCapture(cfg.window_title, cfg.capture_method)
    print(f"擷取尺寸: {cap.size[0]}x{cap.size[1]}｜擷取方式: {cap.method}")
    _locate_minimap(cfg, cap)

    perceiver = Perceiver(cfg, make_detector(cfg.vision, profile.templates_dir))

    def sample(now: float):
        state = perceiver.perceive(cap.grab(), now)
        return state.minimap_xy if state.minimap_xy else (None, None)

    rec = Recorder(sample, KeyWatcher())

    if not args.no_wait:
        try:
            input("切到遊戲視窗，按 Enter 開始錄製（走完一圈按 Ctrl+C 結束）...")
        except EOFError:
            pass          # stdin 被接走（如提權 worker）就直接開錄
    print("錄製中：照平常的方式走路線。", "" if args.seconds
          else "按 Ctrl+C 結束。")

    rec.start()
    try:
        while True:
            t0 = time.monotonic()
            s = rec.step()
            if s.x is not None:
                keys = "+".join(k for k in s.keys) or "-"
                print(f"\r  {rec.seconds:5.1f}s  x={s.x:4d} y={s.y:4d}  "
                      f"按鍵:{keys:<12.12s}  樣本 {len(rec.samples)}",
                      end="", flush=True)
            else:
                print(f"\r  {rec.seconds:5.1f}s  （這幀找不到玩家點）"
                      f"  樣本 {len(rec.samples)}          ",
                      end="", flush=True)
            if args.seconds and rec.seconds >= args.seconds:
                break
            time.sleep(max(args.interval - (time.monotonic() - t0), 0.0))
    except KeyboardInterrupt:
        pass
    print()

    if not rec.samples:
        print("沒有錄到任何畫面")
        return 1
    lost = len(rec.samples) - rec.tracked
    if lost and lost >= len(rec.samples) // 2:
        print(f"警告：{lost}/{len(rec.samples)} "
              "幀找不到小地圖玩家點，路線可能不完整——"
              "先用 tools/debug_view.py --snapshot 確認小地圖 ROI")

    points = compress(rec.samples, tolerance=profile.patrol.tolerance,
                      y_tolerance=profile.patrol.y_tolerance,
                      jump_key=profile.patrol.jump_key)
    print(f"\n錄了 {rec.seconds:.0f} 秒、{len(rec.samples)} 幀 -> {coverage(points)}")
    print(f"路線：{describe(points)}")
    if len(points) < 2:
        print("只錄到不到 2 個點——要真的左右走到底才量得出巡邏範圍；"
              "跨距太小也可能是小地圖 ROI 框錯了")

    block = to_yaml_block(points)
    print("\n把下面貼進 profile（取代原本的 patrol.waypoints）：\n")
    print(block)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(block)
        print(f"已另存: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
