"""把校對完的影像+標籤打包成 ultralytics 訓練結構（train/val 切分）。

用法：
  python tools/prepare_dataset.py
  python tools/prepare_dataset.py --val 0.2 --seed 7
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.dataset import prepare_dataset  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="datasets/raw")
    ap.add_argument("--out", default="datasets/yolo")
    ap.add_argument("--val", type=float, default=0.15, help="驗證集比例")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    res = prepare_dataset(args.raw, args.out, val_fraction=args.val, seed=args.seed)
    print(f"train {res.train} 張 | val {res.val} 張 | 背景負樣本 {res.negatives} 張")
    print(f"類別: {res.classes}")
    print(f"dataset.yaml: {res.yaml_path}")
    print("\n下一步：python tools/train_yolo.py --data", res.yaml_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
