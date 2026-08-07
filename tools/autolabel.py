"""用模板匹配器自動預標註 YOLO 標籤（bootstrap）。

對 datasets/raw/ 的每張截圖跑 TemplateMobDetector，寫出 labelImg
相容的同名 .txt 與 classes.txt。之後用 labelImg 開啟該資料夾人工
校對（補漏框、刪誤框），再跑 tools/prepare_dataset.py。

用法：
  python tools/autolabel.py
  python tools/autolabel.py --threshold 0.68 --single-class
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.config import load_config  # noqa: E402
from maplebot.dataset import autolabel_dir  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default="datasets/raw", help="待標註影像資料夾")
    ap.add_argument("--templates", default="data/templates/mobs")
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--threshold", type=float, default=None,
                    help="匹配門檻（預設用 config 的 mob_match_threshold）")
    ap.add_argument("--single-class", action="store_true",
                    help="全部怪合併成單一類別 mob（打法不分怪種時較好練）")
    args = ap.parse_args()

    threshold = args.threshold
    if threshold is None:
        threshold = load_config(args.config).vision.mob_match_threshold

    res = autolabel_dir(args.images, args.templates, threshold,
                        single_class=args.single_class)
    print(f"影像 {res.images} 張 | 有預標註 {res.labeled} 張 | 共 {res.boxes} 個框")
    print(f"類別: {res.classes}")
    if res.unlabeled_files:
        print(f"\n{len(res.unlabeled_files)} 張沒偵測到任何怪（校對時優先人工看）：")
        for name in res.unlabeled_files[:10]:
            print("  -", name)
        if len(res.unlabeled_files) > 10:
            print(f"  … 另外 {len(res.unlabeled_files) - 10} 張")
    print("\n下一步：pip install labelImg && labelImg", args.images,
          os.path.join(args.images, "classes.txt"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
