"""一鍵訓練：自動標註 -> 切分 -> 訓練，全程不需要人工標註。

用法（在有 GPU 的機器上，datasets/raw 與 data/templates/mobs 都在）：
  python tools/auto_pipeline.py
  python tools/auto_pipeline.py --epochs 120 --model yolo11s.pt

原理：用你既有的怪物模板自動產生標註，直接餵給 YOLO 訓練。標註會有些
雜訊（漏標、框不夠準），但模型通常學得比模板匹配好——它會學到一致的
特徵、忽略隨機誤差。訓練完務必用 tools/debug_view.py 看實際效果。

不夠好的話再考慮：
  1. 蒐集更多畫面重跑（最省力）
  2. --refine：用第一輪的模型重新標註再練一次（自訓練，仍然不用人工）
  3. 只人工修「模型抓不到」的那些畫面（見 docs/YOLO_TRAINING.md）
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.dataset import autolabel_dir, prepare_dataset, train  # noqa: E402


def _progress(index, total, boxes):
    print(f"\r  [1/3] 自動標註 {index}/{total} 張（已找到 {boxes} 個框）",
          end="", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default="datasets/raw")
    ap.add_argument("--templates", default="data/templates/mobs")
    ap.add_argument("--out", default="datasets/yolo")
    ap.add_argument("--threshold", type=float, default=0.68,
                    help="模板匹配門檻；寧可寬鬆一點多標，讓模型自己學")
    ap.add_argument("--single-class", action="store_true",
                    help="不分怪種，全部當一類 mob（資料需求更低）")
    ap.add_argument("--model", default="yolo11n.pt")
    ap.add_argument("--imgsz", type=int, default=800)
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--batch", type=int, default=-1)
    ap.add_argument("--device", default="0")
    ap.add_argument("--name", default="mobs")
    ap.add_argument("--val", type=float, default=0.15)
    args = ap.parse_args()

    if not os.path.isdir(args.images):
        print(f"找不到影像資料夾 {args.images}。"
              "先在遊戲機上跑 tools/collect_dataset.py 蒐集畫面")
        return 2

    lab = autolabel_dir(args.images, args.templates, args.threshold,
                        single_class=args.single_class, progress=_progress)
    print()
    print(f"  -> {lab.images} 張影像，{lab.labeled} 張有框，共 {lab.boxes} 個框"
          f"｜類別 {lab.classes}")
    if lab.boxes < 50:
        print("警告：框太少（建議 200 個以上）。多蒐集一些畫面，"
              "或把 --threshold 調低（例如 0.6）再跑一次")

    print("\n[2/3] 切分 train/val…")
    prep = prepare_dataset(args.images, args.out, val_fraction=args.val)
    print(f"  -> train {prep.train} 張｜val {prep.val} 張"
          f"｜背景負樣本 {prep.negatives} 張")

    print(f"\n[3/3] 訓練（{args.model}, {args.epochs} epochs）…\n")
    best = train(prep.yaml_path, model=args.model, imgsz=args.imgsz,
                 epochs=args.epochs, batch=args.batch, device=args.device,
                 name=args.name)

    print("\n=== 完成 ===")
    print(f"最佳權重: {best}")
    print("\n這台當推理伺服器：")
    print(f"  python tools/serve_yolo.py --model {best}")
    print("\n或在遊戲機本機跑，config 加：")
    print("  vision:")
    print("    mob_detector: yolo")
    print(f"    yolo_model: {best}")
    print("\n上線前先看效果：python tools/debug_view.py --snapshot check.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
