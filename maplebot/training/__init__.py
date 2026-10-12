"""離線訓練用的程式：自動標註的老師（teachers）與 YOLO 資料集管線（dataset）。

這裡的東西只在 GPU 機上由 tools/ 的標註、切分、訓練腳本呼叫，即時迴圈
（runner → perception → decide → execute）從來不 import 它們。分成獨立套件，
是要讓「這段改了會不會影響 bot 實際行為」從目錄就看得出來；tests/test_layering.py
用子行程鎖住 `import maplebot.runner` 不會載入這個套件。
"""
