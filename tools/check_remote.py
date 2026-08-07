"""遠端推理連線診斷：在遊戲機上跑，確認工作站的推理伺服器可用。

用法：
  python tools/check_remote.py                          # 用 config 的 remote_endpoint + 遊戲畫面
  python tools/check_remote.py --endpoint http://192.168.1.50:8100/detect
  python tools/check_remote.py --source shot.png        # 用靜態圖測（不用開遊戲）

會做三件事：查 /health、連送 N 張圖量測往返延遲、印出偵測結果。
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.capture import ImageCapture, WindowCapture  # noqa: E402
from maplebot.config import load_config  # noqa: E402
from maplebot.control.input_win import IS_WINDOWS  # noqa: E402
from maplebot.vision.remote_mobs import RemoteMobDetector  # noqa: E402


def summarize(latencies):
    return {
        "n": len(latencies),
        "min": min(latencies),
        "avg": statistics.mean(latencies),
        "max": max(latencies),
    }


def check_health(endpoint: str, timeout: float):
    base = endpoint.rsplit("/", 1)[0]
    url = f"{base}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read()), None
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as e:
        return None, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--endpoint", default="", help="不填就用 config 的 remote_endpoint")
    ap.add_argument("--source", default="", help="用靜態圖當畫面（不用開遊戲）")
    ap.add_argument("--n", type=int, default=10, help="送幾張圖量測延遲")
    ap.add_argument("--timeout", type=float, default=5.0)
    args = ap.parse_args()

    cfg = load_config(args.config)
    endpoint = args.endpoint or cfg.vision.remote_endpoint
    if not endpoint:
        print("沒有指定端點：用 --endpoint，或在 config 設 vision.remote_endpoint")
        return 2

    print(f"端點: {endpoint}\n")

    health, err = check_health(endpoint, args.timeout)
    if err:
        print(f"✗ /health 連不上: {err}")
        print("\n檢查清單：")
        print("  1. 工作站上 serve_yolo.py 有在跑嗎？")
        print("  2. IP 對嗎？（工作站上 hostname -I）")
        print("  3. 防火牆有開那個埠嗎？"
              "（Linux: sudo ufw allow 8100/tcp）")
        print("  4. 兩台在同一個區網嗎？")
        return 1
    print(f"✓ /health OK: {health}\n")

    if args.source:
        cap = ImageCapture(args.source)
    elif IS_WINDOWS:
        cap = WindowCapture(cfg.window_title, cfg.capture_method)
    else:
        print("非 Windows 環境請用 --source 指定一張圖")
        return 2

    region = cfg.regions.get("playfield")
    if region:
        frame = cap.grab(region)
        print(f"playfield ROI: {list(region)}")
    else:
        frame = cap.grab()
        print("⚠ config 沒有 regions.playfield，改送整個視窗畫面。"
              "請先跑 tools/calibrate.py 校正")

    # 報告「實際會送出去」的內容（RemoteMobDetector 會先縮到 remote_max_width）
    h, w = frame.shape[:2]
    max_w = cfg.vision.remote_max_width
    if max_w and w > max_w:
        shrunk = cv2.resize(frame, (max_w, max(round(h * max_w / w), 1)),
                            interpolation=cv2.INTER_AREA)
    else:
        shrunk = frame
    ok, buf = cv2.imencode(".jpg", shrunk,
                           [cv2.IMWRITE_JPEG_QUALITY, cfg.vision.remote_jpeg_quality])
    print(f"擷取畫面 {w}x{h} -> 實際送出 {shrunk.shape[1]}x{shrunk.shape[0]}"
          f"｜JPEG {len(buf) / 1024:.1f} KB"
          f"（quality={cfg.vision.remote_jpeg_quality}, max_width={max_w or '不縮'}）")

    ratio = w / shrunk.shape[1]
    if ratio >= 2.5:
        print(f"⚠ 縮了 {ratio:.1f} 倍：畫面上 30px 的怪會變成 {30 / ratio:.0f}px，"
              "YOLO 可能認不出來。")
        print("  建議把遊戲視窗調小（經典版 800x600 最單純），"
              "或把 vision.remote_max_width 調高到 1280")
    print()

    det = RemoteMobDetector(endpoint, confidence=cfg.vision.yolo_confidence,
                            timeout=args.timeout,
                            jpeg_quality=cfg.vision.remote_jpeg_quality,
                            max_width=cfg.vision.remote_max_width)
    latencies, mobs = [], []
    for i in range(args.n):
        t0 = time.perf_counter()
        mobs = det.detect(frame)
        latencies.append((time.perf_counter() - t0) * 1000)
        print(f"\r  {i + 1}/{args.n}", end="", flush=True)
    print()

    s = summarize(latencies)
    budget = 1000.0 / max(cfg.fps, 1.0)
    print(f"\n往返延遲: 平均 {s['avg']:.1f}ms｜最低 {s['min']:.1f}ms｜最高 {s['max']:.1f}ms")
    print(f"主迴圈每 tick 預算 {budget:.0f}ms（fps={cfg.fps:g}）")
    if s["avg"] < budget * 0.5:
        print("✓ 延遲充裕，可以正式使用")
    elif s["avg"] < budget:
        print("△ 延遲偏高但仍可用；WiFi 的話可調降 vision.remote_jpeg_quality")
    else:
        print("✗ 延遲超過預算：改用有線網路、調降 remote_jpeg_quality，"
              "或把 loop.fps 調低")

    print(f"\n這張圖偵測到 {len(mobs)} 個框：")
    for m in mobs[:5]:
        print(f"  - {m.name} ({m.cx},{m.cy}) {m.w}x{m.h} score={m.score:.2f}")
    if det.failures:
        print(f"⚠ 有 {det.failures} 次請求失敗")
    return 0


if __name__ == "__main__":
    sys.exit(main())
