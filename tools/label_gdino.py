"""用 GroundingDINO 當「老師」自動標註（免模板、免手標）。

自動標註（Autodistill）路線：用開放詞彙偵測模型，靠一句文字 prompt
（例如 "monster"）把畫面標好，再蒸餾成快速的 YOLO 學生模型。

用的是 **HuggingFace transformers 官方維護版**的 GroundingDINO，
不是 autodistill 那包——autodistill 依賴的 `groundingdino` 套件已停止維護，
在 transformers 5.x 會炸在 `BertModel has no attribute 'get_head_mask'`。

⚠ 誠實提醒：GroundingDINO 是用真實照片訓練的，對 2D 卡通 sprite 不保證
   認得。**先用 --test 在一張截圖上試**，確認它真的框到怪再批次跑：
       python tools/label_gdino.py --test shot.jpg --prompt monster
   框不到的話改用模板老師（python tools/auto_pipeline.py），楓谷 sprite
   每幀像素幾乎相同，模板匹配在這個場景反而更可靠。

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


def parse_results(result, class_id: int = 0):
    """把 HF 的輸出轉成 [(cls_id, cx, cy, w, h, score), ...]（像素座標）。"""
    boxes = result.get("boxes")
    scores = result.get("scores")
    if boxes is None or scores is None:
        return []
    out = []
    for box, score in zip(boxes, scores):
        x1, y1, x2, y2 = (int(round(float(v))) for v in box)
        out.append((class_id, (x1 + x2) // 2, (y1 + y2) // 2,
                    x2 - x1, y2 - y1, float(score)))
    return out


def load_teacher(prompts, box_threshold=0.25, text_threshold=0.25, model_id=TINY):
    """回傳 predict(img_path) -> [(cls_id, cx, cy, w, h, score), ...]。"""
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
    text = build_text_prompt(prompts)
    print(f"文字 prompt: {text!r}")

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
        return parse_results(results[0])

    return predict


def run_test(image, prompts, out_path, box_threshold, text_threshold, model_id):
    if not os.path.exists(image):
        print(f"找不到圖片: {image}")
        return
    predict = load_teacher(prompts, box_threshold, text_threshold, model_id)
    dets = predict(image)
    print(f"\n在 {image}（box_threshold={box_threshold}）偵測到 {len(dets)} 個框")
    img = cv2.imread(image)
    for _, cx, cy, w, h, score in dets:
        x1, y1 = cx - w // 2, cy - h // 2
        print(f"  中心({cx},{cy}) {w}x{h}px  conf={score:.2f}")
        cv2.rectangle(img, (x1, y1), (x1 + w, y1 + h), (0, 255, 255), 2)
        cv2.putText(img, f"{score:.2f}", (x1, max(y1 - 4, 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    cv2.imwrite(out_path, img)
    print(f"\n標註預覽已存到 {out_path} —— 一定要親眼看這張圖，"
          "確認框的是怪而不是樹或 UI")
    if not dets:
        print("⚠ 一個都沒框到。依序試：")
        print("  1. 降門檻: --box-threshold 0.1")
        print("  2. 換 prompt: --prompt \"blue snail\" / \"cartoon creature\"")
        print("  3. 換大模型: --model base")
        print("  4. 都不行就是它認不得 sprite —— 改用模板老師："
              "python tools/auto_pipeline.py（一行跑完，零手標）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="monster",
                    help="文字 prompt；多個用逗號分隔（都標成同一類）")
    ap.add_argument("--class-name", default="mob", help="輸出的類別名稱")
    ap.add_argument("--images", default="datasets/raw")
    ap.add_argument("--test", default="", help="只在這張圖試跑並輸出預覽圖，不批次")
    ap.add_argument("--box-threshold", type=float, default=0.25,
                    help="偵測門檻；卡通 sprite 常要降到 0.1~0.2")
    ap.add_argument("--text-threshold", type=float, default=0.25)
    ap.add_argument("--model", default="tiny", choices=["tiny", "base"],
                    help="tiny 約 230MB 夠快；base 約 900MB 較準")
    args = ap.parse_args()

    prompts = [p.strip() for p in args.prompt.split(",") if p.strip()]
    if not prompts:
        print("prompt 不能是空的")
        return 2
    model_id = TINY if args.model == "tiny" else BASE

    if args.test:
        run_test(args.test, prompts, "gdino_test.jpg",
                 args.box_threshold, args.text_threshold, model_id)
        return 0

    predict = load_teacher(prompts, args.box_threshold, args.text_threshold, model_id)
    paths = list_images(args.images)
    if not paths:
        print(f"{args.images} 裡沒有圖片，先用 tools/collect_dataset.py 蒐集")
        return 2

    labels_per_image = {}
    total = 0
    for i, path in enumerate(paths, 1):
        dets = predict(path)
        labels_per_image[path] = [(d[0], d[1], d[2], d[3], d[4]) for d in dets]
        total += len(dets)
        print(f"\r  標註中 {i}/{len(paths)} 張（共 {total} 個框）", end="", flush=True)
    print()

    write_yolo_labels(args.images, labels_per_image, [args.class_name])
    labeled = sum(1 for v in labels_per_image.values() if v)
    print(f"完成：{len(paths)} 張，{labeled} 張有框，共 {total} 個框")
    print("下一步：python tools/prepare_dataset.py && python tools/train_yolo.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
