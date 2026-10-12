"""maplebot 圖形介面進入點。

  python gui.py                                   # 開上次用的地圖（第一次是範例）
  python gui.py --profile config/profiles/mymap.yaml

命令列版仍然可用（main.py），兩邊共用同一份引擎與設定檔。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="mapleai 控制台")
    ap.add_argument("--config", default=None, help="全域設定（預設 config/default.yaml）")
    ap.add_argument("--profile", default=None,
                    help="地圖設定；不給就開上次用的那張，第一次是範例")
    args = ap.parse_args(argv)
    from maplebot.gui.setup import resolve_launch
    # 先把路徑變成絕對路徑再 chdir：捷徑的「開始位置」指到別處時，工具的
    # tools/… 相對路徑全部找不到；而 chdir 之後命令列給的相對路徑又會指錯地方
    config, profile = resolve_launch(args.config, args.profile, HERE)
    os.chdir(HERE)
    try:
        from maplebot.gui.app import main as run
    except ImportError as e:      # 少數 Linux 發行版的 python 沒帶 tkinter
        print(f"開不了圖形介面（{e}）。Windows 版 Python 內建 tkinter；"
              f"Linux 請安裝 python3-tk，或改用 python main.py")
        return 2
    return run(config, profile)


if __name__ == "__main__":
    sys.exit(main())
