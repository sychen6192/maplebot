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
        capture = WindowCapture(cfg.window_title)
        logger.info("已鎖定遊戲視窗（client 區 %dx%d）", *capture.size)

    keyboard = Keyboard(NullBackend() if (dry_run or not IS_WINDOWS) else None)
    detector = make_detector(cfg.vision, profile.templates_dir)

    runner = Runner(cfg, profile, capture, keyboard, detector, logger,
                    dry_run=dry_run, max_ticks=args.max_ticks)
    try:
        runner.run()
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，結束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
