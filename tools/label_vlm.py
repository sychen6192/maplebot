"""督導層當老師：整張畫面請 VLM 框出每一隻怪，寫成 YOLO 標籤。

描邊老師會把相鄰的東西黏成一框——蝸牛＋旁邊的楓幣、並排的三隻水靈——擠成一
串的還會寬過上限整串被丟掉，YOLO 學生學到的就是糊的框、還把掉落物當成怪的一
部分。VLM 的 grounding 是一隻一框，掉落物、玩家、寵物另外標成 item/player/pet，
不會混進怪的框裡（10 張實測見 maplebot/brain/advisor.py 的 _GROUND_PROMPT）。

用法（督導層要先在 config 裡設好 advisor.endpoint / model / api_key）：
  python tools/label_vlm.py --limit 10 --preview   # 1a. 先看 10 張框得對不對
  python tools/label_vlm.py                        # 1b. 全部（500 張約 30 分鐘）
  python tools/prepare_dataset.py                  # 2. 切 train/val
  python tools/train_yolo.py                       # 3. 訓練

VLM 的回答存在 <images>/vlm_ground.<提示的雜湊>.json，中斷後重跑只問沒問過的；
改了提示就是新的檔名，舊提示的答案不會被悄悄沿用。問不到的圖不寫標籤
（prepare_dataset 會把沒標籤的圖當成「沒有怪」），重跑補齊再往下走。
"""
import argparse
import hashlib
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402

from maplebot.brain.advisor import _GROUND_PROMPT, Advisor  # noqa: E402
from maplebot.config import load_config  # noqa: E402
from maplebot.dataset import ground_label_dir, list_images  # noqa: E402

# 快取跟著提示走：同一張圖換了提示答案就不一樣（加一句「香菇叢是裝飾」，
# 一張圖的怪從 12 隻變 7 隻），沿用舊答案等於拿舊提示的錯去訓練
CACHE_NAME = f"vlm_ground.{hashlib.sha1(_GROUND_PROMPT.encode()).hexdigest()[:8]}.json"


def write_previews(images_dir: str, cache_path: str, limit: int) -> str:
    """把 VLM 框的東西畫回原圖：怪綠框，其他（item/player/pet）橘框。"""
    out_dir = os.path.join(images_dir, "_vlm_preview")
    os.makedirs(out_dir, exist_ok=True)
    with open(cache_path, encoding="utf-8") as f:
        cache = json.load(f)
    for path in list_images(images_dir)[:limit]:
        objs = cache.get(os.path.basename(path))
        img = cv2.imread(path)
        if objs is None or img is None:
            continue
        for label, (x, y, w, h) in objs:
            color = (0, 200, 0) if label == "monster" else (0, 140, 255)
            cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
            cv2.putText(img, label, (x, max(y - 3, 10)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, color, 1)
        cv2.imwrite(os.path.join(out_dir, os.path.basename(path)), img)
    return out_dir


def main() -> int:
    ap = argparse.ArgumentParser(description="VLM 整張畫面框怪，寫成 YOLO 標籤")
    ap.add_argument("--images", default="datasets/raw")
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--limit", type=int, default=0, help="只標前 N 張（0 = 全部）")
    ap.add_argument("--preview", action="store_true",
                    help="把框畫回原圖存到 <images>/_vlm_preview 供抽查")
    ap.add_argument("--dry-run", action="store_true", help="只問、只存快取，不寫標籤檔")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if not cfg.advisor.endpoint:
        print("config 裡沒有 advisor.endpoint，督導層要先設好")
        return 2
    adv = Advisor(cfg.advisor, lambda note: None, logging.getLogger("label_vlm"))
    pf = cfg.regions.get("playfield")
    cache_path = os.path.join(args.images, CACHE_NAME)

    started = time.monotonic()

    def progress(n, total):
        rate = (time.monotonic() - started) / n
        print(f"\r  {n}/{total} 張，剩約 {rate * (total - n) / 60:.0f} 分鐘", end="", flush=True)

    res = ground_label_dir(args.images, adv.ground, cache_path,
                           exclude=cfg.vision.mob_exclude,
                           origin=(pf[0], pf[1]) if pf else (0, 0),
                           rewrite=not args.dry_run, limit=args.limit, progress=progress)
    print()
    print(f"標了 {res.images} 張（這次問了 {res.asked} 次），寫進標籤 {res.kept} 隻怪")
    for kind, n in sorted(res.kinds.items(), key=lambda kv: -kv[1]):
        print(f"  {kind:8s} {n}")
    if args.preview:
        print(f"框畫回原圖：{write_previews(args.images, cache_path, args.limit or 20)}")
    if args.dry_run:
        print("（--dry-run：標籤檔沒有寫）")
    if res.failed:
        print(f"\n有 {len(res.failed)} 張問不到答案、沒寫標籤，重跑同一個指令會只補問這些。"
              "\n補齊前不要跑 prepare_dataset：沒標籤的圖會被當成「畫面上沒有怪」。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
