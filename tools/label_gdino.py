"""用 GroundingDINO 當「老師」自動標註（Autodistill 路線，免模板、免手標）。

這是業界標準的自動標註做法：用一個大型開放詞彙偵測模型，靠一句文字
prompt（例如 "monster"）把畫面標好，再交給 tools/prepare_dataset.py +
train 蒸餾成快速的 YOLO 學生模型。整條流程零人工標註。

⚠ 誠實提醒：GroundingDINO 是用真實照片訓練的，對 2D 卡通 sprite 不保證
   認得。**先用 --test 在一張截圖上試**，確認它真的框到怪再批次跑：
       python tools/label_gdino.py --test datasets/raw/somepic.jpg --prompt monster
   框不到的話，改用模板老師（tools/autolabel.py）——楓谷 sprite 每幀像素
   幾乎相同，模板匹配在這個場景反而更可靠。

安裝（有 GPU 的機器）：
   uv pip install autodistill autodistill-grounding-dino scikit-learn roboflow
第一次執行會從 HuggingFace 下載約 700MB 權重（需要對外網路）。

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


def load_teacher(prompt_map, box_threshold=0.35, text_threshold=0.25):
    """prompt_map: {文字prompt: 類別名}。回傳 (predict_fn, classes)。

    box_threshold 預設 0.35 是給真實照片的；卡通 sprite 常要調到 0.2 以下
    才框得到。寧可先寬鬆多框（訓練時雜訊可被學生模型平均掉），
    也不要一個都沒有。
    """
    try:
        from autodistill.detection import CaptionOntology
        from autodistill_grounding_dino import GroundingDINO
    except ImportError as e:
        raise ImportError(
            "需要安裝：uv pip install autodistill autodistill-grounding-dino "
            "scikit-learn roboflow") from e

    ontology = CaptionOntology(prompt_map)
    model = GroundingDINO(ontology=ontology, box_threshold=box_threshold,
                          text_threshold=text_threshold)
    classes = list(dict.fromkeys(prompt_map.values()))  # 去重且保留順序

    def predict(img_path):
        """回傳 [(cls_id, cx, cy, w, h, score), ...]。"""
        det = model.predict(img_path)
        out = []
        for box, cls_id, conf in zip(det.xyxy, det.class_id, det.confidence):
            x1, y1, x2, y2 = (int(v) for v in box)
            out.append((int(cls_id), (x1 + x2) // 2, (y1 + y2) // 2,
                        x2 - x1, y2 - y1, float(conf)))
        return out

    return predict, classes


def run_test(image, prompt_map, out_path, box_threshold, text_threshold):
    if not os.path.exists(image):
        print(f"找不到圖片: {image}")
        return
    predict, classes = load_teacher(prompt_map, box_threshold, text_threshold)
    dets = predict(image)
    print(f"\n在 {image}（prompt={list(prompt_map)}, box_threshold={box_threshold}）"
          f"偵測到 {len(dets)} 個框")
    img = cv2.imread(image)
    for cls_id, cx, cy, w, h, score in dets:
        x1, y1 = cx - w // 2, cy - h // 2
        print(f"  {classes[cls_id]:8s} 中心({cx},{cy}) {w}x{h}px  conf={score:.2f}")
        cv2.rectangle(img, (x1, y1), (x1 + w, y1 + h), (0, 255, 255), 2)
        cv2.putText(img, f"{classes[cls_id]} {score:.2f}", (x1, max(y1 - 4, 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    cv2.imwrite(out_path, img)
    print(f"\n標註預覽已存到 {out_path} —— 一定要親眼看這張圖，"
          "確認框的是怪而不是樹或 UI")
    if not dets:
        print("⚠ 一個都沒框到。依序試：")
        print(f"  1. 降門檻: --box-threshold 0.15")
        print(f"  2. 換 prompt: --prompt \"blue snail\" / \"cartoon monster\" / \"creature\"")
        print("  3. 都不行就是它認不得 sprite —— 改用模板老師："
              "python tools/auto_pipeline.py（一行跑完，零手標）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="monster",
                    help="文字 prompt；多個用逗號分隔（都會標成同一類 mob）")
    ap.add_argument("--class-name", default="mob", help="輸出的類別名稱")
    ap.add_argument("--images", default="datasets/raw")
    ap.add_argument("--test", default="", help="只在這張圖試跑並輸出預覽圖，不批次")
    ap.add_argument("--box-threshold", type=float, default=0.35,
                    help="偵測門檻；預設 0.35 是給真實照片的，卡通 sprite 常要降到 0.2 以下")
    ap.add_argument("--text-threshold", type=float, default=0.25)
    args = ap.parse_args()

    prompt_map = {p.strip(): args.class_name for p in args.prompt.split(",") if p.strip()}
    if not prompt_map:
        print("prompt 不能是空的")
        return 2

    if args.test:
        run_test(args.test, prompt_map, "gdino_test.jpg",
                 args.box_threshold, args.text_threshold)
        return 0

    predict, classes = load_teacher(prompt_map, args.box_threshold, args.text_threshold)
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

    write_yolo_labels(args.images, labels_per_image, classes)
    labeled = sum(1 for v in labels_per_image.values() if v)
    print(f"完成：{len(paths)} 張，{labeled} 張有框，共 {total} 個框，類別 {classes}")
    print("下一步：python tools/prepare_dataset.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
