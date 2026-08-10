"""用 GroundingDINO 當「老師」自動標註（免模板、免手標）。

自動標註（Autodistill）路線：用開放詞彙偵測模型，靠一句文字 prompt
（例如 "monster"）把畫面標好，再蒸餾成快速的 YOLO 學生模型。

用的是 **HuggingFace transformers 官方維護版**的 GroundingDINO，
不是 autodistill 那包——autodistill 依賴的 `groundingdino` 套件已停止維護，
在 transformers 5.x 會炸在 `BertModel has no attribute 'get_head_mask'`。

⚠ 誠實提醒：GroundingDINO 是用真實照片訓練的，對 2D 卡通 sprite 不保證
   認得。**先用 --test 在一張截圖上試**，確認它真的框到怪再批次跑：
       python tools/label_gdino.py --test shot.jpg --prompt monster
   框不到的話改用描邊老師（python tools/auto_pipeline.py，預設就是），
   楓谷 sprite 都有純黑描邊，那招在這個場景反而更可靠、也不用模板。

安裝（有 GPU 的機器，多半已經有了）：
   uv pip install transformers pillow
第一次執行會下載模型（tiny 約 230MB / base 約 900MB）。

批次標註：
   python tools/label_gdino.py --prompt "monster" --images datasets/raw
之後照常：tools/prepare_dataset.py -> tools/train_yolo.py
"""
import argparse
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maplebot.dataset import list_images, write_yolo_labels  # noqa: E402

TINY = "IDEA-Research/grounding-dino-tiny"
BASE = "IDEA-Research/grounding-dino-base"


def build_text_prompt(prompts) -> str:
    """GroundingDINO 要求：小寫、每個詞以句點結尾。"""
    return " ".join(f"{p.strip().lower().rstrip('.')}." for p in prompts if p.strip())


def extract_boxes(result):
    """HF 輸出 -> [(x1, y1, x2, y2, score, label), ...]（像素座標）。"""
    boxes = result.get("boxes")
    scores = result.get("scores")
    if boxes is None or scores is None:
        return []
    # transformers 5.x 用 text_labels，舊版是 labels
    labels = result.get("text_labels")
    if labels is None:
        labels = result.get("labels") or []
    out = []
    for i, (box, score) in enumerate(zip(boxes, scores)):
        x1, y1, x2, y2 = (int(round(float(v))) for v in box)
        label = str(labels[i]).lower() if i < len(labels) else ""
        out.append((x1, y1, x2, y2, float(score), label))
    return out


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a[:4]
    bx1, by1, bx2, by2 = b[:4]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    return inter / (area_a + area_b - inter)


def _matches(label: str, phrases) -> bool:
    return any(p.strip().lower().rstrip(".") in label for p in phrases if p.strip())


def filter_boxes(boxes, keep_phrases, reject_phrases, iou_drop: float = 0.4):
    """只留下 keep 類、且沒有跟 reject 類重疊的框。

    NPC、其他玩家、招牌這些對 GroundingDINO 來說跟怪一樣是「卡通人形」，
    所以同時問正反兩組詞，再把反例（與其重疊者）剔掉。
    同一個框兩邊都命中時以 reject 為準——寧可漏標也不要教模型打 NPC。
    """
    rejects = [b for b in boxes if _matches(b[5], reject_phrases)]
    kept = []
    for b in boxes:
        if _matches(b[5], reject_phrases):
            continue
        if reject_phrases and not _matches(b[5], keep_phrases):
            continue        # 有指定正例時，標籤對不上的一律不要
        if any(_iou(b, r) >= iou_drop for r in rejects):
            continue        # 同一個東西也被判為 NPC/玩家
        kept.append(b)
    return kept


def to_yolo_dets(boxes, class_id: int = 0):
    """[(x1,y1,x2,y2,score,label)] -> [(cls_id, cx, cy, w, h, score)]。"""
    return [(class_id, (b[0] + b[2]) // 2, (b[1] + b[3]) // 2,
             b[2] - b[0], b[3] - b[1], b[4]) for b in boxes]


def load_teacher(keep, reject=(), box_threshold=0.25, text_threshold=0.25,
                 model_id=TINY, iou_drop=0.4):
    """回傳 predict(img_path) -> (保留的框, 被剔除的框)，皆為 extract_boxes 格式。"""
    try:
        import torch
        from PIL import Image
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
    except ImportError as e:
        raise ImportError("需要安裝：uv pip install transformers pillow") from e

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"載入 {model_id}（device={device}）…")
    processor = AutoProcessor.from_pretrained(model_id)
    model = AutoModelForZeroShotObjectDetection.from_pretrained(model_id).to(device)
    text = build_text_prompt(list(keep) + list(reject))
    print(f"文字 prompt: {text!r}")
    if reject:
        print(f"保留: {list(keep)}｜剔除: {list(reject)}")

    def predict(img_path):
        image = Image.open(img_path).convert("RGB")
        inputs = processor(images=image, text=text, return_tensors="pt").to(device)
        with torch.no_grad():
            outputs = model(**inputs)
        results = processor.post_process_grounded_object_detection(
            outputs, inputs.input_ids,
            threshold=box_threshold, text_threshold=text_threshold,
            target_sizes=[image.size[::-1]],   # (h, w)
        )
        allb = extract_boxes(results[0])
        kept = filter_boxes(allb, keep, reject, iou_drop)
        kept_ids = {id(b) for b in kept}
        return kept, [b for b in allb if id(b) not in kept_ids]

    return predict


def run_test(image, keep, reject, out_path, box_threshold, text_threshold,
             model_id, iou_drop):
    if not os.path.exists(image):
        print(f"找不到圖片: {image}")
        return
    predict = load_teacher(keep, reject, box_threshold, text_threshold,
                           model_id, iou_drop)
    kept, dropped = predict(image)
    print(f"\n在 {image}（box_threshold={box_threshold}）："
          f"保留 {len(kept)} 個框，剔除 {len(dropped)} 個")
    img = cv2.imread(image)
    for x1, y1, x2, y2, score, label in dropped:   # 紅色 = 被剔除
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 1)
        cv2.putText(img, f"x {label}", (x1, max(y1 - 4, 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
    for x1, y1, x2, y2, score, label in kept:      # 黃色 = 會拿去訓練
        print(f"  保留 ({(x1 + x2) // 2},{(y1 + y2) // 2}) "
              f"{x2 - x1}x{y2 - y1}px conf={score:.2f} label={label!r}")
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 255), 2)
        cv2.putText(img, f"{label} {score:.2f}", (x1, max(y1 - 4, 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    for x1, y1, x2, y2, score, label in dropped:
        print(f"  剔除 ({(x1 + x2) // 2},{(y1 + y2) // 2}) "
              f"conf={score:.2f} label={label!r}")
    cv2.imwrite(out_path, img)
    print(f"\n預覽已存到 {out_path}：**黃框=會拿去訓練，紅框=已剔除**")
    if not kept:
        print("⚠ 沒有保留任何框。依序試：")
        print("  1. 降門檻: --box-threshold 0.1")
        print("  2. 換 prompt: --prompt \"blue snail\" / \"cartoon creature\"")
        print("  3. 換大模型: --model base")
        print("  4. 都不行 —— 改用描邊老師：python tools/auto_pipeline.py --check")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="monster",
                    help="要標的東西；多個用逗號分隔（都標成同一類）")
    ap.add_argument("--reject", default="npc,person,player character,signboard",
                    help="不要標的東西（NPC、其他玩家等）；設空字串可關閉")
    ap.add_argument("--iou-drop", type=float, default=0.4,
                    help="怪物框與剔除框重疊超過這個比例就一起剔除")
    ap.add_argument("--class-name", default="mob", help="輸出的類別名稱")
    ap.add_argument("--images", default="datasets/raw")
    ap.add_argument("--test", default="", help="只在這張圖試跑並輸出預覽圖，不批次")
    ap.add_argument("--box-threshold", type=float, default=0.25,
                    help="偵測門檻；卡通 sprite 常要降到 0.1~0.2")
    ap.add_argument("--text-threshold", type=float, default=0.25)
    ap.add_argument("--model", default="tiny", choices=["tiny", "base"],
                    help="tiny 約 230MB 夠快；base 約 900MB 較準")
    args = ap.parse_args()

    keep = [p.strip() for p in args.prompt.split(",") if p.strip()]
    reject = [p.strip() for p in args.reject.split(",") if p.strip()]
    if not keep:
        print("prompt 不能是空的")
        return 2
    model_id = TINY if args.model == "tiny" else BASE

    if args.test:
        run_test(args.test, keep, reject, "gdino_test.jpg",
                 args.box_threshold, args.text_threshold, model_id, args.iou_drop)
        return 0

    predict = load_teacher(keep, reject, args.box_threshold, args.text_threshold,
                           model_id, args.iou_drop)
    paths = list_images(args.images)
    if not paths:
        print(f"{args.images} 裡沒有圖片，先用 tools/collect_dataset.py 蒐集")
        return 2

    labels_per_image = {}
    total = dropped_total = 0
    for i, path in enumerate(paths, 1):
        kept, dropped = predict(path)
        labels_per_image[path] = [d[:5] for d in to_yolo_dets(kept)]
        total += len(kept)
        dropped_total += len(dropped)
        print(f"\r  標註中 {i}/{len(paths)} 張（保留 {total}，剔除 {dropped_total}）",
              end="", flush=True)
    print()

    write_yolo_labels(args.images, labels_per_image, [args.class_name])
    labeled = sum(1 for v in labels_per_image.values() if v)
    print(f"完成：{len(paths)} 張，{labeled} 張有框，共 {total} 個框"
          f"（另剔除 {dropped_total} 個 NPC/玩家等）")
    print("下一步：python tools/prepare_dataset.py && python tools/train_yolo.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
