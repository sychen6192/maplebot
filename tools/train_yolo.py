"""YOLO 怪物偵測訓練（RTX 5090 建議路線）。

用法：
  python tools/train_yolo.py                       # 用 datasets/yolo/dataset.yaml
  python tools/train_yolo.py --model yolo11s.pt --epochs 120

需要：pip install ultralytics（5090/Blackwell 要先裝 cu128 版 PyTorch，
見 docs/YOLO_TRAINING.md）。訓練完會直接印出要貼進 config 的兩行設定。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.dataset import GAME_TRAIN_OVERRIDES  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="datasets/yolo/dataset.yaml")
    ap.add_argument("--model", default="yolo11n.pt",
                    help="yolo11n.pt 就夠快夠準；想更準用 yolo11s.pt")
    ap.add_argument("--imgsz", type=int, default=800,
                    help="接近 playfield 原始尺寸即可（會取 32 的倍數）")
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--batch", type=int, default=-1, help="-1 = 依 VRAM 自動")
    ap.add_argument("--device", default="0")
    ap.add_argument("--project", default="runs/mobs")
    ap.add_argument("--name", default="mobs")
    args = ap.parse_args()

    try:
        from ultralytics import YOLO
    except ImportError:
        print("需要先安裝 ultralytics：pip install ultralytics")
        print("RTX 5090 請先裝 cu128 版 PyTorch（見 docs/YOLO_TRAINING.md）")
        return 2
    if not os.path.exists(args.data):
        print(f"找不到 {args.data}，請先跑 tools/prepare_dataset.py")
        return 2

    model = YOLO(args.model)
    model.train(
        data=args.data,
        imgsz=args.imgsz,
        epochs=args.epochs,
        batch=args.batch,
        device=args.device,
        # 用絕對路徑，否則 ultralytics 會再套一層 runs/detect/ 進去
        project=os.path.abspath(args.project),
        name=args.name,
        patience=20,
        **GAME_TRAIN_OVERRIDES,
    )

    best = str(model.trainer.best)
    print("\n=== 訓練完成 ===")
    print(f"最佳權重: {best}")
    print("\n把下面貼進 config/default.yaml 的 vision: 區塊即可切換：\n")
    print("  mob_detector: yolo")
    print(f"  yolo_model: {best}")
    print("\n先驗證再上線：python tools/debug_view.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
