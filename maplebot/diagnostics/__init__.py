"""開跑前與出問題時的診斷：環境自檢（doctor）、辨識自檢（selfcheck），以及
把辨識結果疊回畫面的繪圖（overlay）。

這裡的程式在使用者「還沒開始掛機」或「想知道 bot 看到什麼」的時候跑：
tools/doctor.py、tools/debug_view.py --snapshot、控制台的準備分頁與辨識自檢都
從這裡拿結論，CLI 和 GUI 才不會講出不一樣的話。即時迴圈只碰兩個點：runner 的
_preflight 借 doctor.check_status_bars 驗狀態列，`main.py --preview` 借 overlay
畫主迴圈這一幀。除此之外改這裡不會動到 bot 打怪的行為。
"""
