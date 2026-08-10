"""maplebot 進入點。

常用指令：
  python main.py --profile config/profiles/example.yaml            # 正式執行
  python main.py --profile ... --dry-run                           # 只看決策不按鍵
  python main.py --profile ... --source tests/fixtures/xxx.jpg     # 離線用截圖跑
"""
import argparse
import sys

from maplebot import log
from maplebot.capture import ImageCapture, WindowCapture
from maplebot.config import ConfigError, load_config, load_profile
from maplebot.control.input_win import IS_WINDOWS, Keyboard, NullBackend
from maplebot.runner import Runner
from maplebot.vision.mobs import make_detector


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="MapleStory 經典版自動打怪（學術研究用）")
    p.add_argument("--config", default="config/default.yaml", help="全域設定檔")
    p.add_argument("--profile", default="config/profiles/example.yaml", help="地圖/職業 profile")
    p.add_argument("--dry-run", action="store_true", help="不送出按鍵，只顯示每個 tick 的決策")
    p.add_argument("--source", default="", help="用靜態截圖代替遊戲視窗（離線測試）")
    p.add_argument("--max-ticks", type=int, default=0, help="跑 N 個 tick 後自動結束（0=不限）")
    p.add_argument("--preview", action="store_true",
                   help="開一個視窗即時顯示主迴圈這一幀的辨識結果（會多花一點 CPU）")
    p.add_argument("--no-report", action="store_true", help="這次不要產生收工報告")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logger = log.setup()

    try:
        cfg = load_config(args.config)
        profile = load_profile(args.profile)
    except ConfigError as e:
        logger.error("設定錯誤: %s", e)
        return 2

    logger.info("設定檔: %s", " + ".join(cfg.sources))
    if len(cfg.sources) == 1:
        from maplebot.config import resolve_local_path
        logger.info("（沒有個人覆寫檔。要覆寫請建立 %s）",
                    resolve_local_path(args.config))

    dry_run = args.dry_run
    if args.source:
        capture = ImageCapture(args.source)
        if not dry_run:
            logger.info("使用靜態圖片來源，自動切換為 dry-run")
            dry_run = True
    else:
        if not IS_WINDOWS:
            logger.error("即時擷取只支援 Windows。開發環境請加 --source <截圖> 離線執行")
            return 2
        capture = WindowCapture(cfg.window_title, cfg.capture_method)
        logger.info("已鎖定遊戲視窗（client 區 %dx%d，擷取方式 %s）",
                    *capture.size, capture.method)
        if capture.method == "screen":
            logger.warning("此客戶端不支援 PrintWindow，改用螢幕擷取："
                           "執行期間不要讓任何視窗蓋住遊戲畫面")

    if args.no_report:
        cfg.report.enabled = False

    keyboard = Keyboard(NullBackend() if (dry_run or not IS_WINDOWS) else None)
    detector = make_detector(cfg.vision, profile.templates_dir, logger)

    preview = None
    if args.preview:
        from maplebot.overlay import Preview
        preview = Preview(cfg, profile, logger)
        logger.info("已開啟即時預覽——畫的是主迴圈當下這一幀，"
                    "跟決策看到的完全是同一份資料")

    runner = Runner(cfg, profile, capture, keyboard, detector, logger,
                    dry_run=dry_run, max_ticks=args.max_ticks, preview=preview)
    try:
        runner.run()
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，結束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
