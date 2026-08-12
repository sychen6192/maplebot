# decide() 是純函式

`brain/fsm.py` 的 `decide()` 不碰鍵盤、不碰螢幕、也不看時鐘——`now` 與
`playfield_center` 都由呼叫端傳入，回傳的是一個描述「要做什麼」的 Action
物件，實際按鍵由 executor 負責。

這樣做的代價是每一層都要把時間與畫面資訊往下傳，讀起來比直接呼叫
`time.monotonic()` 囉嗦。換到的是整套打怪決策可以用單元測試完整驗證：冷卻
時間、連續低血、卡住偵測、探邊重試這些跟時間有關的行為，測試裡只要傳不同的
`now` 就能驗，不用 sleep、不用 mock 時鐘、不用開遊戲。

## Consequences

`decide()` 裡不要出現 I/O、亂數或時間呼叫。需要跨 tick 記住的東西放進
`Runtime`，由呼叫端持有。
