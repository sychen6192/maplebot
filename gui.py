"""maplebot 圖形介面進入點。

  python gui.py                                   # 用預設 profile 開啟
  python gui.py --profile config/profiles/mymap.yaml

命令列版仍然可用（main.py），兩邊共用同一份引擎與設定檔。
"""
import argparse
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="maplebot 控制台")
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--profile", default="config/profiles/example.yaml")
    args = ap.parse_args(argv)
    try:
        from maplebot.gui.app import main as run
    except ImportError as e:      # 少數 Linux 發行版的 python 沒帶 tkinter
        print(f"開不了圖形介面（{e}）。Windows 版 Python 內建 tkinter；"
              f"Linux 請安裝 python3-tk，或改用 python main.py")
        return 2
    return run(args.config, args.profile)


if __name__ == "__main__":
    sys.exit(main())
