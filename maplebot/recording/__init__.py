"""把使用者自己走一遍的路線變成 profile 的巡邏點：錄製（recorder）與壓縮（route）。

只在使用者按下控制台的「錄製路線」或執行 tools/record_route.py 時跑，那時 bot
沒有在打怪；錄完產出的是一段 patrol YAML，巡邏時讀的是 profile，不是這裡。
即時迴圈不 import 這個套件。
"""
