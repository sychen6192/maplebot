"""用描邊偵測自動標註資料集（不需要模板，換地圖直接能用）。

  python tools/autolabel_outline.py                       # 標 datasets/raw
  python tools/autolabel_outline.py --exclude 0,0,310,390 --exclude 1740,1150,819,162

跟 tools/autolabel.py（模板老師）的差別：模板老師每換一張圖、每多一種怪
都要重截模板；描邊老師靠 sprite 的黑邊，對任何怪都成立。標完接
tools/prepare_dataset.py -> tools/train_yolo.py。

--exclude 用來排除疊在畫面上的 UI（小地圖、快捷鍵盤、公告）。它們固定
出現在同一個位置，不排除的話模型會把那裡學成一隻怪。座標是 playfield
（也就是收集到的圖檔本身）的 x,y,w,h。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.config import load_config  # noqa: E402
from maplebot.dataset import autolabel_outline_dir  # noqa: E402
from maplebot.log import console_safe  # noqa: E402


def _rect(text: str):
    parts = [int(v) for v in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("格式是 x,y,w,h（四個整數）")
    return tuple(parts)


def main(argv=None) -> int:
    console_safe()
    ap = argparse.ArgumentParser(description="描邊自動標註")
    ap.add_argument("--images", default="datasets/raw")
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--min-area", type=int, default=150,
                    help="標註用的面積下限（790px 基準）。比執行時寬鬆："
                         "漏標比多標傷害大")
    ap.add_argument("--black-level", type=int, default=None,
                    help="預設沿用 config 的 vision.outline_black_level")
    ap.add_argument("--exclude", action="append", type=_rect, default=[],
                    help="要排除的 UI 區塊 x,y,w,h（可重複）")
    ap.add_argument("--templates", default="data/templates/mobs",
                    help="同時用模板匹配當第二個老師並聯集（留空 = 只用描邊）")
    ap.add_argument("--template-threshold", type=float, default=0.62)
    ap.add_argument("--class-name", default="mob")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    vc = cfg.vision
    black = args.black_level if args.black_level is not None else vc.outline_black_level

    def progress(i, total, boxes):
        print(f"\r  標註 {i}/{total} 張（已找到 {boxes} 個框）", end="", flush=True)

    res = autolabel_outline_dir(
        args.images, black_level=black, min_area=args.min_area,
        max_area=vc.outline_max_area, close_kernel=vc.outline_close_kernel,
        max_size=tuple(vc.outline_max_size), max_aspect=vc.outline_max_aspect,
        player_box=tuple(vc.outline_player_box), ui_dir=vc.ui_templates_dir,
        exclude=args.exclude, class_name=args.class_name,
        templates_dir=args.templates, template_threshold=args.template_threshold,
        progress=progress)
    print()
    print(f"完成：{res.images} 張圖、{res.labeled} 張有框、共 {res.boxes} 個框"
          f"（平均 {res.boxes / max(res.images, 1):.1f} 個/張）")
    if res.unlabeled_files:
        print(f"其中 {len(res.unlabeled_files)} 張沒有任何框——那些會成為背景負樣本，"
              "有助於壓低誤報。數量過多的話把 --min-area 調低")
    print("接著：python tools/prepare_dataset.py 然後 python tools/train_yolo.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
