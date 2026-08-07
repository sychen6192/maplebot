"""YOLO 推理伺服器：在有 GPU 的機器上跑，讓遊戲機透過區網呼叫。

用法（在工作站上）：
  python tools/serve_yolo.py --model runs/mobs/mobs/weights/best.pt

遊戲機那邊在 config/local.yaml 設：
  vision:
    mob_detector: remote
    remote_endpoint: "http://<工作站IP>:8100/detect"

端點：
  POST /detect?conf=0.5   body = JPEG bytes  ->  {"mobs":[...]}
  GET  /health            ->  {"status":"ok","model":...,"device":...}

注意：沒有任何身分驗證，只適合用在你自己的區網。
"""
import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import cv2
    import numpy as np
except ImportError as e:
    sys.exit(
        f"缺少套件（{e.name}）。這台推理機請先安裝：\n"
        "  pip install -r requirements-server.txt\n"
        "（headless Linux 若出現 libGL.so.1 錯誤，見 docs/YOLO_TRAINING.md）"
    )

MAX_BODY = 16 * 1024 * 1024   # 單張圖上限，擋掉異常請求


def build_handler(model, default_conf, device, model_path, verbose):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            if verbose:
                super().log_message(fmt, *args)

        def _json(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/health"):
                self._json(200, {"status": "ok", "model": model_path, "device": device})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self.path.startswith("/detect"):
                self._json(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > MAX_BODY:
                self._json(400, {"error": "bad content-length"})
                return

            conf = default_conf
            if "conf=" in self.path:
                try:
                    conf = float(self.path.split("conf=")[1].split("&")[0])
                except ValueError:
                    pass

            raw = self.rfile.read(length)
            img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                self._json(400, {"error": "cannot decode image"})
                return

            t0 = time.perf_counter()
            results = model.predict(img, conf=conf, device=device, verbose=False)
            mobs = []
            for r in results:
                names = r.names
                for box in r.boxes:
                    x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
                    mobs.append({
                        "name": str(names.get(int(box.cls[0]), int(box.cls[0]))),
                        "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2,
                        "w": x2 - x1, "h": y2 - y1,
                        "score": round(float(box.conf[0]), 4),
                    })
            ms = (time.perf_counter() - t0) * 1000
            if verbose:
                print(f"\r{len(mobs)} 個框 | 推理 {ms:.1f}ms", end="", flush=True)
            self._json(200, {"mobs": mobs, "inference_ms": round(ms, 2)})

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="訓練好的 .pt 權重")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--conf", type=float, default=0.5)
    ap.add_argument("--device", default="0", help="GPU 編號，或 cpu")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    # 先檢查權重再載入重量級套件，訊息才清楚
    if not os.path.exists(args.model):
        print(f"找不到權重: {args.model}")
        print("還沒訓練的話，先照 docs/YOLO_TRAINING.md 跑完 collect -> autolabel"
              " -> 校對 -> prepare -> train，train 完會印出權重路徑")
        return 2
    try:
        from ultralytics import YOLO
    except ImportError:
        print("需要先安裝 ultralytics：pip install -r requirements-server.txt")
        return 2

    print(f"載入模型 {args.model}（device={args.device}）…")
    model = YOLO(args.model)
    # 先跑一張暖機，避免第一個請求特別慢
    model.predict(np.zeros((320, 320, 3), np.uint8), device=args.device, verbose=False)

    handler = build_handler(model, args.conf, args.device, args.model, not args.quiet)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"推理伺服器已啟動: http://{args.host}:{args.port}/detect")
    print("遊戲機的 config 設定：")
    print("  vision:")
    print("    mob_detector: remote")
    print(f"    remote_endpoint: \"http://<這台機器的區網IP>:{args.port}/detect\"")
    print("⚠ 沒有身分驗證，請只在自己的區網使用。Ctrl+C 結束。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n結束")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
