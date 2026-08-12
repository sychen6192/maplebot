# maplebot

楓之谷經典版的自動打怪研究專案。畫面是唯一的輸入、模擬按鍵是唯一的輸出，
所以整條流程都能拿靜態截圖離線重現。

## Language

### 畫面與座標

**Client 區**:
遊戲視窗扣掉標題列與邊框之後的內容區。所有 region 座標以它的左上角為原點。
_Avoid_: 視窗, window, screen

**Playfield**:
畫面上演出角色與怪物的那一塊，不含小地圖與下方狀態列。怪物座標的原點。
_Avoid_: 主畫面, 遊戲畫面, game area

**小地圖 (Minimap)**:
角落那張俯視圖。角色的世界位置只有這裡讀得到，巡邏完全靠它。y 往下增加。
_Avoid_: mini-map, 地圖

**Region**:
校正時框出來的一塊 client 區矩形（minimap / hp_bar / mp_bar / exp_bar / playfield）。
_Avoid_: ROI, 區域, box

**基準寬度 (Reference width)**:
790 px。所有用像素表達的門檻都以這個 playfield 寬度為準，實跑時按實際寬度換算。
_Avoid_: 標準寬度, base width

### 畫面上的角色與東西

**角色 (Character)**:
這個 bot 操作的那個人物。
_Avoid_: player, 玩家, 自己

**其他玩家 (Other player)**:
小地圖上出現的別人。看到就暫停是預設行為。
_Avoid_: others, 路人, 玩家

**角色定位 (Character fix)**:
角色在 playfield 上的實際位置，依序由名牌、組隊紅條、畫面正中央三個來源取得。
_Avoid_: player_screen, 角色位置

**怪物 (Mob)**:
可以攻擊的目標。偵測層一律只輸出這一個類別，不分怪種。
_Avoid_: monster, enemy, 敵人

**跟隨物 (Follower)**:
跟著角色在畫面上移動、因此不是怪物的東西；實務上就是寵物。
_Avoid_: pet, 寵物, 跟班

**名牌 (Nametag)**:
角色腳下那塊深色底的名字。角色定位的第一順位來源。
_Avoid_: 角色名稱, name label

**組隊紅條 (Party bar)**:
自己跟自己組隊後角色頭上那條紅條。角色定位的第二順位來源。
_Avoid_: player_bar, 血條

**怪物血條 (Mob HP bar)**:
被打過的怪頭上那條綠條。是遊戲畫的 UI，顏色固定。
_Avoid_: hpbar, 綠條

### 偵測

**偵測器 (Detector)**:
把一張 playfield 影像變成一串怪物框的東西。描邊、模板、YOLO、遠端四種可換。
_Avoid_: model, 辨識器

**描邊偵測 (Outline detection)**:
靠 sprite 的黑色輪廓找怪。零設定、換地圖直接能用，是預設偵測器。
_Avoid_: template-free, 黑邊偵測

**模板匹配 (Template matching)**:
拿事先截好的怪物圖比對。標得出怪種，但每種怪每張地圖都要重截。
_Avoid_: template detection, 圖片比對

### 自動標註

**老師 (Teacher)**:
產生 YOLO 預標註的偵測器。老師抓不到的東西，學生也學不到。
_Avoid_: labeler, 標註器

**學生 (Student)**:
拿老師的標註訓練出來的 YOLO 模型。
_Avoid_: model, 訓練結果

**負樣本 (Negative sample)**:
沒有任何框的畫面。刻意留著，它教模型「這裡沒有怪」。
_Avoid_: 空標註, background

### 巡邏

**巡邏點 (Waypoint)**:
小地圖上的一個目標座標，角色在這些點之間來回。
_Avoid_: route point, patrol point, 路徑點

**探邊 (Probe)**:
開場往左右走到走不動，用撞到的兩面牆推算出巡邏範圍。
_Avoid_: 校正, calibration, 自動巡邏校正

**校正 (Calibration)**:
人工框出各個 region 的那一步。跟探邊無關，兩者不要混用同一個詞。
_Avoid_: 對位, setup

**垂直移動 (Climb)**:
換樓層：抓繩上下，或站在平台上往下跳。
_Avoid_: 爬繩, jump down

**脫困 (Escape)**:
卡住時往反方向跳一下。
_Avoid_: unstuck, 跳脫

### 沒有進展的四種樣子

各自的成因與處置都不同，不要都叫「卡住」。

**卡住 (Stuck)**:
巡邏移動中小地圖位置長時間沒變。地形卡死或被怪彈回。

**空揮 (Attack stall)**:
一直在攻擊、角色位置卻沒變。多半是把跟隨物當成怪，或隔著地形打不到。

**沒抓到繩 (Climb stall)**:
垂直移動中小地圖 y 沒變。

**撞牆 (Wall hit)**:
探邊時 x 走不動了，代表這一側到底。

### 安全

**停機 (Panic)**:
判定不能再跑下去，結束整場。可設定先按回城卷。不可逆。
_Avoid_: abort, 緊急停止, 中止

**暫停 (Pause)**:
持續不動作但繼續看畫面，直到熱鍵或狀況解除。
_Avoid_: hold, 停止

**讓過 (Wait)**:
這一個 tick 不動作，下一個 tick 照常重新判斷。
_Avoid_: 等待, idle

**黑屏 (Black screen)**:
整個畫面幾乎全黑：斷線、換頻道或讀圖中。
_Avoid_: blackout, 全黑

**異常畫面 (Anomaly)**:
出事當下存下來的那張截圖。
_Avoid_: 錯誤截圖, screenshot

### 設定

**Config**:
綁這台機器與這個視窗的設定：region 座標、偵測門檻、安全線。換電腦要重來。
_Avoid_: 設定檔, settings

**Profile**:
綁這個角色與這張地圖的設定：技能鍵、巡邏點、藥水門檻、buff。換地圖換一份。
_Avoid_: 角色設定, preset
