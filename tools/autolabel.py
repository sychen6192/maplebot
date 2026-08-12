"""自動預標註 YOLO 標籤（bootstrap），不用手標。

對 datasets/raw/ 的每張截圖跑「老師」偵測器，寫出 labelImg 相容的同名
.txt 與 classes.txt。之後可以用 labelImg 開該資料夾人工校對，也可以直接
跑 tools/prepare_dataset.py 硬練。

兩種老師（見 maplebot/teachers.py）：
  outline  描邊偵測，**不用截任何模板**（預設）。只有一個類別 mob。
  template 模板匹配，要先用 tools/grab_template.py 截怪；有怪種資訊。

用法：
  python tools/autolabel.py --preview 6      # 先看老師標得對不對（強烈建議）
  python tools/autolabel.py                  # 正式標
  python tools/autolabel.py --scan-black     # 描邊抓太少時，試幾個門檻看哪個好
  python tools/autolabel.py --teacher template --threshold 0.68
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.config import load_config  # noqa: E402
from maplebot.dataset import autolabel_dir, list_images, preview_labels  # noqa: E402
from maplebot.teachers import TEACHERS, OutlineTeacher, make_teacher  # noqa: E402

SCAN_LEVELS = (0, 8, 12, 15, 20, 25)
SCAN_SAMPLE = 8


def scan_black_level(cfg, images_dir: str) -> int:
    """在幾張抽樣影像上試不同的描邊門檻，印出各自標到幾個框。

    JPEG 壓縮會把純黑壓成 (3,2,4) 這種值，所以離線標註的門檻常常要比即時
    擷取高。與其叫使用者猜，不如直接把幾個值跑一遍給他看。
    """
    paths = list_images(images_dir)
    if not paths:
        print(f"{images_dir} 裡沒有圖片")
        return 2
    step = max(len(paths) // SCAN_SAMPLE, 1)
    sample = paths[::step][:SCAN_SAMPLE]
    print(f"用 {len(sample)} 張抽樣影像試門檻（目前 config 是 "
          f"{cfg.vision.outline_black_level}）：\n")
    import cv2
    imgs = [im for im in (cv2.imread(p, cv2.IMREAD_COLOR) for p in sample)
            if im is not None]
    for level in SCAN_LEVELS:
        teacher = OutlineTeacher(cfg, black_level=level)
        boxes = sum(len(teacher.label(im)) for im in imgs)
        print(f"  --black-level {level:>3}  ->  {boxes} 個框"
              f"（平均每張 {boxes / max(len(imgs), 1):.1f}）")
    print("\n挑一個跟你眼睛看到的怪數量最接近的，再跑："
          "\n  python tools/autolabel.py --black-level <值> --preview 6"
          "\n（數字一路往上衝表示已經開始把背景當怪了，不是越多越好）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default="datasets/raw", help="待標註影像資料夾")
    ap.add_argument("--teacher", choices=list(TEACHERS), default="outline",
                    help="用哪個偵測器當老師（預設 outline，不需要模板）")
    ap.add_argument("--templates", default="data/templates/mobs",
                    help="--teacher template 時的模板資料夾")
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--threshold", type=float, default=None,
                    help="模板匹配門檻（預設用 config 的 mob_match_threshold）")
    ap.add_argument("--black-level", type=int, default=None,
                    help="描邊門檻（預設用 config 的 outline_black_level）。"
                         "JPEG 會破壞純黑，離線標註常要調高到 12~20")
    ap.add_argument("--scan-black", action="store_true",
                    help="只試不同的描邊門檻各標到幾個框，不寫檔")
    ap.add_argument("--single-class", action="store_true",
                    help="全部怪合併成單一類別 mob（打法不分怪種時較好練）")
    ap.add_argument("--preview", type=int, default=0, metavar="N",
                    help="只標 N 張並輸出畫好框的圖供人工確認，不寫標籤檔")
    ap.add_argument("--preview-dir", default="",
                    help="預覽輸出資料夾（預設 <images>/_preview）")
    args = ap.parse_args()

    if not os.path.isdir(args.images):
        print(f"找不到影像資料夾 {args.images}。"
              "先在遊戲機上跑 tools/collect_dataset.py 蒐集畫面")
        return 2

    cfg = load_config(args.config)
    if args.scan_black:
        return scan_black_level(cfg, args.images)

    try:
        teacher = make_teacher(args.teacher, cfg, templates_dir=args.templates,
                               threshold=args.threshold,
                               single_class=args.single_class,
                               black_level=args.black_level)
    except ValueError as e:      # 沒模板之類的設定問題，不用印整串 traceback
        print(e)
        return 2
    print(f"老師：{args.teacher}｜類別 {teacher.classes}")

    if args.preview:
        out_dir = args.preview_dir or os.path.join(args.images, "_preview")
        written = preview_labels(args.images, teacher, out_dir, args.preview)
        print(f"\n已輸出 {len(written)} 張預覽到 {out_dir}")
        print(teacher.explain())
        print("\n**打開來看**：黃框就是會拿去訓練的標註。")
        print("  框到怪了      -> 拿掉 --preview 正式標")
        print("  框太少/沒框到 -> python tools/autolabel.py --scan-black")
        print("  框到角色自己  -> 見 docs/YOLO_TRAINING.md「角色被標成怪」")
        return 0

    def show(index, total, boxes):
        print(f"\r  標註中 {index}/{total} 張（已找到 {boxes} 個框）",
              end="", flush=True)

    res = autolabel_dir(args.images, progress=show, teacher=teacher)
    print()
    print(f"影像 {res.images} 張 | 有預標註 {res.labeled} 張 | 共 {res.boxes} 個框")
    print(f"類別: {res.classes}")
    print(teacher.explain())
    if res.unlabeled_files:
        print(f"\n{len(res.unlabeled_files)} 張沒偵測到任何怪（校對時優先人工看）：")
        for name in res.unlabeled_files[:10]:
            print("  -", name)
        if len(res.unlabeled_files) > 10:
            print(f"  … 另外 {len(res.unlabeled_files) - 10} 張")
    print("\n下一步：pip install labelImg && labelImg", args.images,
          os.path.join(args.images, "classes.txt"))
    print("或直接練：python tools/prepare_dataset.py && python tools/train_yolo.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
