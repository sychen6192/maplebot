# 與高星開源專案的比對報告

比對對象（2026-08 當時 GitHub 星數）：

| 專案 | 星數 | 定位 |
|---|---|---|
| [tanjeffreyz/auto-maple](https://github.com/tanjeffreyz/auto-maple) | 684★ | GMS 全功能 bot：CV + TensorFlow 符文破解 + GUI + routine 系統，此類專案中星數最高 |
| [KenYu910645/MapleStoryAutoLevelUp](https://github.com/KenYu910645/MapleStoryAutoLevelUp) | 356★ | 楓之谷 Artale（經典版）專用：純視覺、狀態機、路線錄製 |
| [楓之谷達人](https://gamebox365wg.pixnet.net/blog/post/118048) | 商業閉源 | 台灣早期的自動掛機程式。無法讀原始碼，只能從公開功能表比對缺口 |

兩者都是「螢幕視覺 + 模擬按鍵」路線，驗證了本專案的基本架構選擇。
以下是逐項比對後**採用**與**不採用**的清單。

## 已採用（v1.3.0）

| 借鑑 | 來源 | 在本專案的實作 |
|---|---|---|
| 小地圖角落模板自動定位（免手動校正，小地圖拖動/展開也不怕） | auto-maple `capture.py` 的 `minimap_tl/br_template` | `vision/locate.py`；`regions.minimap: auto` + `tools/grab_template.py --dir data/templates/ui` |
| 玩家點用模板匹配優先、顏色遮罩備援 | auto-maple `player_template` | `vision/minimap.py`（有 `minimap_player.png` 就用模板） |
| 點狀色塊面積上限，排除同色地形（自由市場黃地板誤判） | auto-maple 用模板迴避此問題；面積過濾是我們對色彩路線的等效解 | `vision.max_dot_pixels` + 連通元件過濾 |
| 卡住偵測 → 跳躍脫困 | MapleStoryAutoLevelUp `is_player_stuck()`（位移門檻 + 逾時） | `fsm._check_stuck()` → `Escape` 動作，換方向跳一下 |
| 相對座標巡邏點（0~1 佔小地圖寬度比例） | auto-maple `convert_to_relative` | `Waypoint.x <= 1.0` 視為比例 |
| 巡邏點附掛按鍵動作（放置技能等） | auto-maple routine 的 Point+commands（簡化版） | `waypoints: [{x: 95, keys: [alt]}]` → `RunKeys` |
| 多層地圖的垂直移動（爬繩、下跳平台） | auto-maple 的 `Move` 指令會自動爬繩；MapleStoryAutoLevelUp 靠路徑圖顏色標註 | `waypoints: [{x: 68, y: 20}]` → `Climb`。**閉迴路**：每步比對小地圖 y，沒抓到繩子會重試再放棄該點（兩個參考專案都是開迴路按鍵） |
| directional / AoE 兩種攻擊模式 | MapleStoryAutoLevelUp `attack: directional/aoe_skill` | `attack.type: aoe` 時不轉向直接施放 |
| 黑屏偵測（斷線/換頻道/讀圖） | auto-maple notifier 的 room-change 偵測（>90% 全黑） | `safety.is_black_screen()` → 自動暫停 + 截圖 |
| 危險事件聲音警報 | auto-maple notifier（pygame 音檔） | `alerts.py` 用 winsound 免音檔：Panic 長響、有人出現短響 |
| 按鍵時間 ±20% 抖動 | auto-maple `vkeys.press` 的 `0.8 + 0.4*random()` | `Executor._dur()` 全動作套用 |
| Panic 先按回城卷再停止 | MapleStoryAutoLevelUp `return_home_if_no_potion` | `profile.panic_return_key` |
| default + local 兩層設定 | MapleStoryAutoLevelUp `config_default` + `config_custom` | `config/local.yaml` 深度合併覆寫 |
| **自動撿取掉落物** | 楓之谷達人「自動撿拾物品」 | `profile.loot` → `Loot` 動作。**清完場才撿**（範圍內還有怪就先打），且只在最後一次攻擊後 `after_combat` 秒內撿，避免路過空地一直按 |
| **依 MP 決定要不要施放** | 楓之谷達人「根據 MP 狀態施放技能」 | `attack.min_mp` / `buffs[].min_mp`。MP 不夠就繼續巡邏等回魔，不站著空揮；MP 讀不到時照常施放（辨識失敗不該讓 bot 停擺） |
| **怪物血條偵測** | MapleStoryAutoLevelUp `monster_detect.with_enemy_hp_bar` | `vision/mob_hpbar.py`。怪被打過之後頭上的綠色血條是**遊戲自己畫的 UI**（BGR 71,204,64），顏色固定、不用調門檻——描邊偵測是猜的，這個是確定的。專門補描邊漏掉的（太大/太小/跟地形黏在一起），兩邊用 IoU 合併、重疊時留描邊的框（那是量出來的比較準）。他們精確比色，我們給 ±25 的容差，因為擷取方式不同顏色會差一兩階 |
| **用身體判定攻擊範圍，不是用中心點** | MapleStoryAutoLevelUp 用攻擊框與怪框的**交集面積**（`max_mob_area_trigger`） | `fsm._in_attack_box()`。原本比中心點，體型大的怪「中心在範圍外、身體壓在臉上」會被當成打不到而放過。改成框對框相交（範圍框本來就是「打得到的範圍」，碰到就算打得到），比他們的面積門檻少一個要調的參數 |
| **tkinter GUI** | auto-maple `gui/`、各家商業腳本 | `gui.py`：狀態、控制、錄製、打怪/藥水/循環按鍵設定、系統日誌。原本評估為「維護成本高、與研究目的無關」而不採用，實際使用後推翻——設定散在兩個 YAML、校正要複製貼上，是最主要的出錯來源。邏輯全在 `gui/controller.py`（不碰 tkinter）所以測得到 |

## 評估後不採用

| 功能 | 來源 | 不採用原因 |
|---|---|---|
| 四叉樹地圖 + A* 尋路 | auto-maple `layout.py` | 需要預先錄製每張地圖的平台資料。我們改用「人工指定的多層 waypoint + 閉迴路驗證」：表達力較弱（不會自己繞路），但零錄製成本、失敗行為可預測。**繩子位置仍必須人工填**——自動找繩子誤判一次就會讓整條路線錯亂，代價不划算 |
| 符文（rune）自動破解 | 兩者皆有（TensorFlow 箭頭辨識） | 符文是 GMS/Artale 的反外掛機制；自動破解反偵測意味太強，超出研究範圍。本專案遇到異常畫面的策略一律是「暫停 + 警報 + 截圖」，交還給人 |
| 路線錄製成彩色路徑圖 | MapleStoryAutoLevelUp `maps/` | 表達力強但工具鏈重。我們錄的是**小地圖軌跡**壓成 YAML waypoint：可讀、可 diff、可手改（見下方錄製腳本） |
| 自動登入 / 換頻道 | 兩者皆有 | 涉及帳號憑證自動化，超出打怪研究範圍 |
| 玩家全域定位（畫面對整張大地圖配準） | MapleStoryAutoLevelUp `minimaps/` | 我們用小地圖座標已滿足巡邏需求，配準的成本收益不划算 |
| **防測謊機、防 GM** | 楓之谷達人 | 這是反偵測，不是自動化。本專案的原則一直是「遇到看不懂的畫面就暫停 + 警報 + 截圖，交還給人」——測謊機正是那種畫面。實作它等於把專案從「自動化研究」變成「規避偵測工具」，法律風險也完全不同（見 README 用途聲明） |
| **定時換頻** | 楓之谷達人 | 需要操作遊戲選單，且動機通常是躲人。我們已有更保守的版本：`pause_when_players` 偵測到其他玩家就停手，人走了自動繼續 |
| 記憶體外掛（無敵／瞬移／無延遲） | 台灣商業外掛常見功能 | 需要讀寫遊戲行程記憶體。本專案架構明確排除（README：不讀寫記憶體、不碰封包），這也是 MDY v. Blizzard 那類判決的核心爭點 |

## 對照淘寶在賣的商業腳本（「圖靈助手」類）

賣家列的 7 項功能，逐項對照：

| 商業腳本的功能 | 我們 | 說明 |
|---|---|---|
| 1. 自動打怪 | ✅ | `fsm.decide()` 的攻擊分支，支援多技能輪替（他們只有單一攻擊鍵） |
| 2. 自動 buff | ✅ | `buffs:` 清單，GUI 提供 8 組循環按鍵（跟他們的 UI 一樣） |
| 3. **錄製腳本** | ✅ 本次補上 | `route.py` + GUI 的錄製鈕。做法不同：他們錄的是按鍵時序（被打歪就整條走錯），我們錄的是**路線**——壓成巡邏點後由閉迴路巡邏執行，方向鍵不錄進去 |
| 4. 爬樓梯 | ✅ | `Climb`，每一步回頭確認小地圖 y 真的變了，爬不上去會重試再放棄該點 |
| 5. 完全模擬手動、不改任何數據 | ✅ | 本專案從第一天就是這個架構：SendInput + 螢幕視覺，不讀記憶體不碰封包 |
| 6. 自動吃藥 | ✅ | `potions:`，門檻用百分比（跟他們的 UI 一樣） |
| 7. AI + 腳本自動打怪 | ✅ | 描邊偵測（零設定）／YOLO／遠端推理三選一，另有本地 VLM 督導層 |

他們的 UI 上還看得到幾項，我們的處理：

| 他們有 | 我們 | 理由 |
|---|---|---|
| HP/MP 讀**數字**（97/100） | 讀色條比例 | 數字要 OCR 點陣字型，每個伺服器字型不同。色條夠用，誤讀的真正成因是 ROI 框錯——所以改成開場自檢直接擋下來（`_preflight`），並把校正當下的視窗大小記進設定檔 |
| 紅點出現時**自動換線** | 暫停等人走 | 換線要操作遊戲選單，而且動機是躲人。我們維持較保守的作法 |
| 「不打怪自檢」是一張要人自己核對的清單 | 程式自己檢查 | 視窗大小、HP 讀值、找不到玩家點，開場就檢查並指出要修哪裡 |
| 每日掛機 3~4 小時、不支援測謊 | 不做反偵測 | 見下節「評估後不採用」 |

## 從別的領域借的

| 借鑑 | 來源 | 在本專案的實作 |
|---|---|---|
| 「同一組參數要能跨解析度」 | [Airtest](https://github.com/AirtestProject/Airtest)（網易的遊戲 QA 自動化框架）用 SIFT 等特徵匹配做到尺度不變 | 我們沒用 SIFT（描邊偵測不吃模板），但這個角度讓我們發現**所有以像素為單位的門檻都是解析度相依的**，同一組值只在某一種視窗大小下有效。先後修了三處：outline 面積門檻（`outline_auto_scale`，而且面積要**平方**縮放不是線性）、攻擊距離（`attack_auto_scale`）。基準統一在 `config.REFERENCE_WIDTH = 790` |

## 三個參考專案都沒有、我們自己加的

- **多技能輪替**（`profile.skills`）：依優先權挑第一個「冷卻好了、MP 夠、
  範圍內怪數達標」的技能。關鍵是 `min_mobs`——30 秒冷卻的大絕不該浪費在
  單隻蝸牛身上，而 MP 中等時應該退而求其次用主攻，不是完全不打。
  兩個參考專案都只支援單一攻擊鍵 + 一個 AoE 開關。
- **經驗值進度追蹤 + 停滯偵測**（`progress.py`）：換算等/小時，並在連續 N 分鐘
  沒有經驗進帳時暫停 + 警報 + 截圖。這是唯一能驗證**整條鏈路**的健康檢查——
  技能鍵設錯、怪物偵測抓不到、角色卡在打不到怪的地方，其他 watchdog 都看不出來
  （小地圖點還在、畫面也沒黑），只有「EXP 沒在動」抓得到。
  升級回捲（99%→0%）算成進度，小幅倒退則記為死亡扣經驗。

## 比對後確認我們已領先的部分

- **可測試性**：兩個參考專案都沒有單元測試；本專案 322 個 pytest + CI + 真實截圖 ground truth
- **離線開發**：`--source 截圖` 可跑完整 pipeline，參考專案都必須開遊戲才能調
- **決策層純函式**：auto-maple 的決策散在 bot/routine/命令簿多處，狀態耦合全域 config；我們的 `fsm.decide()` 可以直接窮舉測試
- **ML 升級路徑**：YOLO 訓練管線（自動預標註）與 VLM 督導層是兩個參考專案都沒有的
