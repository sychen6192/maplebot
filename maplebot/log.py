import logging
import os
import sys
from datetime import datetime

LOG_DIR = "logs"


def setup(name: str = "maplebot", level: int = logging.INFO) -> logging.Logger:
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
