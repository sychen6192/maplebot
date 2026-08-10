"""把訓練好的 .pt 轉成 ONNX，讓推理不必扛 PyTorch。

為什麼要這個：ultralytics + torch + torchvision 裝起來 2GB 起跳，還要對
CUDA 版本。但**掛機那台不需要訓練**，它只要跑推理。換成 ONNX 之後，
掛機環境只需要 onnxruntime（幾十 MB），CPU 推理通常還比 PyTorch eager 快
一到三倍——沒顯卡的筆電因此也能走 YOLO 路線而不是只能用描邊偵測。

  python tools/export_onnx.py --model runs/detect/train/weights/best.pt
  python tools/export_onnx.py --model best.pt --imgsz 640 --half   # GPU 上再快一點

轉完會印出要貼進設定檔的兩行。轉檔本身需要 ultralytics（在有訓練環境的
那台做一次就好），產出的 .onnx 複製到掛機那台即可。
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="YOLO .pt -> .onnx")
    ap.add_argument("--model", required=True, help="訓練好的 .pt 權重")
    ap.add_argument("--imgsz", type=int, default=640, help="推理解析度（預設 640）")
    ap.add_argument("--half", action="store_true",
                    help="FP16（只在 GPU 推理有意義，CPU 反而會變慢）")
    ap.add_argument("--opset", type=int, default=12)
    ap.add_argument("--simplify", action="store_true", default=True)
    ap.add_argument("--benchmark", action="store_true",
                    help="轉完隨機跑 20 次量一下延遲")
    args = ap.parse_args(argv)

    if not os.path.exists(args.model):
        print(f"❌ 找不到權重檔: {args.model}")
        return 1
    try:
        from ultralytics import YOLO
    except ImportError:
        print("❌ 需要 ultralytics 才能匯出：pip install ultralytics")
        print("   （匯出只需要做一次，可以在有訓練環境的那台做完再把 .onnx 複製過去）")
        return 1

    print(f"載入 {args.model} …")
    model = YOLO(args.model)
    out = model.export(format="onnx", imgsz=args.imgsz, half=args.half,
                       opset=args.opset, simplify=args.simplify)
    out = str(out)
    size_mb = os.path.getsize(out) / 1048576.0
    print(f"\n✅ 已匯出: {out}（{size_mb:.1f} MB）")

    if args.benchmark:
        import numpy as np
        from maplebot.vision.yolo_mobs import YoloMobDetector

        det = YoloMobDetector(out, confidence=0.5, imgsz=args.imgsz)
        frame = np.random.randint(0, 255, (args.imgsz, args.imgsz, 3), dtype=np.uint8)
        det.detect(frame)                     # 第一次含初始化，不計入
        t0 = time.perf_counter()
        for _ in range(20):
            det.detect(frame)
        ms = (time.perf_counter() - t0) / 20 * 1000
        print(f"⏱  平均推理 {ms:.1f} ms/幀"
              f"（主迴圈 8 FPS 的預算是 125ms，這一段佔 {ms / 125:.0%}）")

    print("\n把這兩行貼進 config/local.yaml：")
    print("\nvision:")
    print("  mob_detector: yolo")
    print(f"  yolo_model: {out}")
    if args.imgsz != 640:
        print(f"  yolo_imgsz: {args.imgsz}")
    print("\n掛機那台只要裝 onnxruntime 就好（不需要 torch）：")
    print("  pip install onnxruntime          # CPU")
    print("  pip install onnxruntime-gpu      # 有 CUDA 的話")
    return 0


if __name__ == "__main__":
    sys.exit(main())
