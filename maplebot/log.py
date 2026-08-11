import logging
import os
import sys
from datetime import datetime

LOG_DIR = "logs"


def console_safe() -> None:
    """印不出來的字元降級成 '?'，而不是讓整個程式炸掉。

    繁中 Windows 終端機預設 cp950，印 ✅/❌ 這類 emoji 會直接
    UnicodeEncodeError。保留終端機原本的編碼（中文才不會變亂碼），
    只把 encode 錯誤策略改成 replace。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass


def setup(name: str = "maplebot", level: int = logging.INFO) -> logging.Logger:
    console_safe()
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(fmt)
    logger.addHandler(console)

    logfile = os.path.join(LOG_DIR, datetime.now().strftime("run_%Y%m%d_%H%M%S.log"))
    fileh = logging.FileHandler(logfile, encoding="utf-8")
    fileh.setLevel(logging.DEBUG)
    fileh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    logger.addHandler(fileh)
    return logger
