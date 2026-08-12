"""一鍵訓練：自動標註 -> 切分 -> 訓練，全程不需要人工標註。

用法（在有 GPU 的機器上，datasets/raw 已經有蒐集好的畫面）：
  python tools/auto_pipeline.py                       # 描邊當老師，不用模板
  python tools/auto_pipeline.py --check               # 只標註+輸出預覽，不訓練
  python tools/auto_pipeline.py --teacher template    # 改用模板老師
  python tools/auto_pipeline.py --epochs 120 --model yolo11s.pt

原理：用**你現在就跑得動的偵測器**當老師自動產生標註，直接餵給 YOLO。
預設老師是描邊偵測（bot 現在用的那個），所以不用截任何模板。標註會有些
雜訊（漏標、框不夠準），但模型通常學得比老師好——它會學到一致的特徵、
忽略隨機誤差。

⚠ 老師標錯的地方，學生會學得非常牢。開始訓練前會先輸出幾張畫好框的預覽圖
   到 datasets/raw/_preview——**打開看過再讓它練下去**。

不夠好的話再考慮：
  1. 蒐集更多畫面重跑（最省力）
  2. 調老師的門檻：python tools/autolabel.py --scan-black
  3. 只人工修「模型抓不到」的那些畫面（見 docs/YOLO_TRAINING.md）
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.config import load_config  # noqa: E402
from maplebot.dataset import autolabel_dir, prepare_dataset, preview_labels, train  # noqa: E402
from maplebot.teachers import TEACHERS, make_teacher  # noqa: E402

PREVIEW_COUNT = 6


def _progress(index, total, boxes):
    print(f"\r  [1/3] 自動標註 {index}/{total} 張（已找到 {boxes} 個框）",
          end="", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default="datasets/raw")
    ap.add_argument("--teacher", choices=list(TEACHERS), default="outline",
                    help="用哪個偵測器當老師（預設 outline，不需要模板）")
    ap.add_argument("--templates", default="data/templates/mobs",
                    help="--teacher template 時的模板資料夾")
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--out", default="datasets/yolo")
    ap.add_argument("--threshold", type=float, default=0.68,
                    help="模板匹配門檻；寧可寬鬆一點多標，讓模型自己學")
    ap.add_argument("--black-level", type=int, default=None,
                    help="描邊門檻（預設用 config 的 outline_black_level）。"
                         "JPEG 會破壞純黑，離線標註常要調高到 12~20")
    ap.add_argument("--single-class", action="store_true",
                    help="不分怪種，全部當一類 mob（資料需求更低）")
    ap.add_argument("--check", action="store_true",
                    help="只做標註與預覽就停下，不切分也不訓練")
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

    cfg = load_config(args.config)
    try:
        teacher = make_teacher(args.teacher, cfg, templates_dir=args.templates,
                               threshold=args.threshold,
                               single_class=args.single_class,
                               black_level=args.black_level)
    except ValueError as e:      # 沒模板之類的設定問題，不用印整串 traceback
        print(e)
        return 2
    print(f"老師：{args.teacher}｜類別 {teacher.classes}")

    lab = autolabel_dir(args.images, progress=_progress, teacher=teacher)
    print()
    print(f"  -> {lab.images} 張影像，{lab.labeled} 張有框，共 {lab.boxes} 個框"
          f"｜類別 {lab.classes}")
    print("  " + teacher.explain().replace("\n", "\n  "))

    # 預覽放在標註之後、訓練之前：這是唯一一次「還來得及不要練下去」的機會
    teacher.reset()
    preview_dir = os.path.join(args.images, "_preview")
    written = preview_labels(args.images, teacher, preview_dir, PREVIEW_COUNT)
    if written:
        print(f"\n  預覽 {len(written)} 張已存到 {preview_dir}"
              "（黃框=會拿去訓練，先打開看一眼）")

    if lab.boxes < 50:
        print("[!] 框太少（建議 200 個以上）。多蒐集一些畫面，"
              + ("或跑 python tools/autolabel.py --scan-black 調描邊門檻"
                 if args.teacher == "outline"
                 else "或把 --threshold 調低（例如 0.6）") + "再跑一次")

    if args.check:
        print("\n--check：到此為止，沒有訓練。看過預覽覺得 OK 就拿掉 --check 重跑")
        return 0

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
    print("\n在遊戲機本機跑，config 加：")
    print("  vision:")
    print("    mob_detector: yolo")
    print(f"    yolo_model: {best}")
    print("\n或這台當推理伺服器：")
    print(f"  python tools/serve_yolo.py --model {best}")
    print("\n上線前先看效果：python tools/debug_view.py --snapshot check.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
