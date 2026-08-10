# 與高星開源專案的比對報告

比對對象（2026-08 當時 GitHub 星數）：

| 專案 | 星數 | 定位 |
|---|---|---|
| [tanjeffreyz/auto-maple](https://github.com/tanjeffreyz/auto-maple) | 684★ | GMS 全功能 bot：CV + TensorFlow 符文破解 + GUI + routine 系統，此類專案中星數最高 |
| [KenYu910645/MapleStoryAutoLevelUp](https://github.com/KenYu910645/MapleStoryAutoLevelUp) | 356★ | 楓之谷 Artale（經典版）專用：純視覺、狀態機、路線錄製 |
| [tingwei1111/maplestory-worlds-automation](https://github.com/tingwei1111/maplestory-worlds-automation) | 119★ | MapleStory Worlds（Artale）：YOLOv8 偵測 + 主動找怪 + **進程/資源監控與圖表** |
| [楓之谷達人](https://gamebox365wg.pixnet.net/blog/post/118048) | 商業閉源 | 台灣早期的自動掛機程式。無法讀原始碼，只能從公開功能表比對缺口 |

三個開源專案都是「螢幕視覺 + 模擬按鍵」路線，驗證了本專案的基本架構選擇。
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
| **怪物血條偵測** | MapleStoryAutoLevelUp `monster_detect.with_enemy_hp_bar` | `vision/mob_hpbar.py`。怪被打過之後頭上的綠色血條是**遊戲自己畫的 UI**（BGR 71,204,64），顏色固定、不用調門檻——描邊偵測是猜的，這個是確定的。專門補描邊漏掉的（太大/太小/跟地形黏在一起），兩邊用 IoU 合併、重疊時留描邊的框（那是量出來的比較準）。**預設關閉**：他們精確比色，我一開始自作聰明加了 ±25 容差，結果草地綠只差 17~24 階，實測一張畫面冒出三十幾隻假怪、整片草地被框起來。改回精確比色並加上形狀檢查（夠扁、寬度合理、實心矩形），但這招在什麼地圖成立要先自檢過才敢開 |
| **用身體判定攻擊範圍，不是用中心點** | MapleStoryAutoLevelUp 用攻擊框與怪框的**交集面積**（`max_mob_area_trigger`） | `fsm._in_attack_box()`。原本比中心點，體型大的怪「中心在範圍外、身體壓在臉上」會被當成打不到而放過。改成框對框相交（範圍框本來就是「打得到的範圍」，碰到就算打得到），比他們的面積門檻少一個要調的參數 |
| **組隊紅條定位角色** | MapleStoryAutoLevelUp `party_red_bar`（他們先做 nametag 模板匹配，後來標記為 deprecated 改用這個）；淘寶那份商業腳本也要求「隊伍放 P」 | `vision/player_bar.py`。原本假設「角色永遠在畫面正中央」是錯的——鏡頭有跟隨延遲、走到地圖邊緣會卡住，實測 1920 視窗可偏離 200px 以上，導致角色自己被當成怪打、攻擊範圍也沒對準。自己跟自己組隊後頭上的紅條是遊戲畫的 UI，抓到就是精確位置。我們多加一道保險：位置離畫面中心超過 35% 就不採信（那多半是場景裡的紅色物件），抓不到則退回畫面中央 |
| **tkinter GUI** | auto-maple `gui/`、各家商業腳本 | `gui.py`：狀態、控制、錄製、打怪/藥水/循環按鍵設定、系統日誌。原本評估為「維護成本高、與研究目的無關」而不採用，實際使用後推翻——設定散在兩個 YAML、校正要複製貼上，是最主要的出錯來源。邏輯全在 `gui/controller.py`（不碰 tkinter）所以測得到 |

## 對照 maplestory-worlds-automation（119★）

這份是 2026-08 補的。它跟前兩個參考專案的路線不同：**打怪邏輯比我們簡單很多**
（沒有小地圖定位、沒有巡邏路線、沒有 HP/MP 讀取、沒有爬繩、沒有狀態機——
`auto.py` 一支 787 行，決策就是「YOLO 找到 mob 就把滑鼠移過去按 Z」），
但它有一整套我們完全沒有的東西：**掛機的可觀測性**。

逐項對照如下。

### 他們有、我們原本沒有 → 這次補上

| 他們的功能 | 他們的做法 | 我們的實作 |
|---|---|---|
| **系統/進程監控** | `monitoring/monitor_plus.py`：psutil 掃 `MapleStory Worlds` 進程，記 CPU/RSS/執行緒/handle 數 | `sysmon.py`。但目的不同：他們拿來「看數字」，我們拿來**當安全機制**。遊戲行程消失 = 當掉/被關掉/被踢下線，這時 bot 還在對著桌面按技能鍵，`stop_when_game_exits` 直接停機。這是所有「遊戲不見了」訊號裡最不會誤判的一個——比黑屏偵測明確（讀圖也會黑）、比找不到玩家點明確（換圖也會找不到）。另外我們用**視窗 handle 反查 PID**（`GetWindowThreadProcessId`），他們用行程名比對：開兩個客戶端時名字會撞在一起，關掉「另一個」就會被誤判成遊戲當了 |
| **效能監控（FPS + 平均偵測耗時）** | `PerformanceMonitor`：每秒計數一次 FPS，記最近 100 次偵測耗時 | `metrics.py`。差別有兩點。(1) 我們量的是**分段**耗時（capture / perceive / decide / execute / monitor）——「慢」不可行動，「慢在 perceive」才可行動，所以 `advice()` 會直接說出該去調 `mob_search_box` 還是 `mob_interval`。(2) FPS 用 tick 之間的**真實時間差**算，不是把耗時倒數回去：後者會得到一個永遠達標的漂亮數字，因為 sleep 補足預算的那段時間被忽略了，而那正是人感受到的頻率。還有一個他們沒處理的坑——`execute` 的耗時本來就包含按住技能鍵的時間，算進超支率的話每份報告都會說「execute 最慢」，那是廢話，所以判定超支時要扣掉 |
| **資源告警（門檻 + 歷史）** | CPU>90%、RAM>85%、進程 RAM>2GB 就記一筆 alert，保留最近 100 筆 | `sysmon.evaluate()`（純函式，可窮舉測試）+ `alerts.py` 的帳本。多了**同類警報 2 分鐘冷卻**：CPU 高檔會連續好幾分鐘，每 5 秒吵一次沒人會看。訊息也寫成可行動的——「系統 CPU 95%，主迴圈會開始掉幀，反應變慢先看這裡再去調辨識參數」，而不是只報數字 |
| **JSON 數據持久化** | 每 30 秒把最近 100 個 snapshot 寫成 JSON | `report.py`。我們不做「持續寫入」而是**收工寫一份**：掛機中每 10 秒取一個樣點放記憶體（`Series`，有筆數上限），結束時一次落地成 `session_<時間>.json` |
| **matplotlib 效能圖表** | 每 5 分鐘自動出一張 2x2 圖（系統 CPU/RAM、遊戲 CPU/RAM） | `report.write_chart()`：HP/MP/EXP、視野內怪數、實際 FPS、遊戲記憶體。**圖上的字一律 ASCII**——他們的圖表標題寫中文，但 matplotlib 預設字型（DejaVu Sans）沒有中文字符，實際輸出是一排豆腐框。我們的測試會盯著 matplotlib 的 `missing from font` warning，寫中文進去就紅燈 |
| **文字報告匯出** | 選單第 5 項，把當前狀態存成 .txt | `report.render_markdown()`，跑完自動產生。內容取捨不同：他們的報告是「當下的系統資源」，我們的是**這一場的結算**——賺了幾等、每小時幾等、攻擊幾次、喝幾瓶藥、出過幾次警報、慢在哪一段、遊戲有沒有掛掉 |
| **啟動器的環境自檢** | `start.py` 檢查 Python 版本、套件、模型檔、設定檔，缺套件可代裝 | `tools/doctor.py` + `maplebot/doctor.py`（檢查邏輯是純函式，28 個測試）。我們多檢查的是**設定的內部矛盾**，那才是實際卡住人的地方：ROI 有沒有超出校正時的視窗大小、血條 ROI 有沒有被框進 playfield（症狀是站在空地一直揮）、`potions.hp.below_ratio` 有沒有低於 `critical_hp_ratio`（症狀是一掉血就停機，而人會以為是辨識壞了）。每一項都附「該動哪裡」，有 ❌ 就 exit 1 |
| **錯誤診斷腳本** | `tools/diagnose_errors.py`：import 測試 + `compile()` 語法檢查 | 併進 doctor 的套件檢查。語法檢查我們不做——那是 CI 的工作（`.github/workflows/ci.yml` 每次 push 跑 431 個測試，語法錯根本進不了 repo） |
| **最長運行時間** | `safety.max_runtime_hours: 2` | `safety.max_runtime_minutes`（0=不限）。用分鐘是因為「掛 90 分鐘」比「掛 1.5 小時」好填。到點正常收工並產生報告，不是硬中斷 |
| **執行中即時預覽疊框** | `start_automation(show_preview=True)` 用 cv2.imshow 畫偵測框 | `main.py --preview`。原本我們只有獨立的 `tools/debug_view.py`，那是**另一個行程**：各自擷取、各自辨識，看到的東西不保證跟主迴圈一樣，CPU 也多一份。繪圖搬到 `maplebot/overlay.py` 之後兩邊共用，預覽畫的就是主迴圈這一幀 |
| **ONNX 模型** | repo 裡放了 `best.onnx`（但程式沒用到，只吃 .pt） | `tools/export_onnx.py` + `yolo_mobs.py` 收 `.onnx`。真正的價值是**掛機那台不用裝 PyTorch**：torch + torchvision 2GB 起跳還要對 CUDA 版本，換 onnxruntime 只要幾十 MB，CPU 推理通常還快一到三倍 |

### 他們有、我們評估後不採用

| 功能 | 不採用原因 |
|---|---|
| **主動尋找怪物**（`horizontal` / `vertical` / `random` 三種搜尋模式） | 他們沒有巡邏系統，所以「兩秒沒看到怪就隨機走走跳跳」是他們唯一的走位。我們的 `patrol` 是它的超集且更可靠：巡邏點可錄製或 `auto` 量測，走一步就用小地圖重新定位（閉迴路），還能爬繩上下樓。`random` 搜尋在楓谷的多層地圖上會把角色帶到奇怪的地方，而且他們的「返回原位」只是往反方向按同樣時間的方向鍵——被怪打歪或撞到牆就回不去了 |
| **滑鼠移到怪物身上再攻擊**（`pyautogui.moveTo` + click/press） | 楓谷的攻擊是面向 + 技能鍵，跟游標位置無關；移游標只是白費時間（`duration=0.1` 一次就吃掉 100ms）。我們用 `fsm` 判斷面向再送 scancode |
| **多類別偵測行為表**（mob/item/npc/character/ui 各自 action + max_distance） | 表達力看起來強，但他們的設定檔裡除了 mob 全部是 `action: ignore`——因為 NPC 互動、撿物判定都需要遊戲內狀態，光靠一個框做不到。我們的撿物走 `loot`（清完場才撿、只在最後一次攻擊後 N 秒內撿），比「看到 item 就走過去」實際 |
| **多解析度視窗預設**（small/medium/large/fullhd/qhd 五組寫死的座標） | 我們用視窗標題自動抓 client 區座標（`window.py`，含 DPI aware），遊戲視窗移動或改大小都不用改設定。寫死螢幕座標的做法在多螢幕或有工作列時就會偏掉 |
| **`pyautogui` 按鍵** | 跨平台（他們支援 macOS）是優點，但 pyautogui 送的是 virtual-key，DirectInput 的遊戲收不到。我們用 SendInput scancode——這是「按鍵有沒有真的進遊戲」的分水嶺 |
| **`emergency_stop_corner`（滑鼠移到角落停機）** | pyautogui 的 FAILSAFE。我們用 F12 熱鍵：掛機時滑鼠本來就不會動，角落判定反而容易被誤觸 |

### 對照後我們仍然領先的部分

| 面向 | 他們 | 我們 |
|---|---|---|
| 角色定位 | 無。假設角色永遠在畫面正中央 | 小地圖黃點 + 組隊紅條雙重定位（鏡頭有跟隨延遲，實測可偏離中心 200px 以上） |
| HP/MP/EXP | 完全不讀 | 逐欄色彩統計 + 閃爍去雜訊 + 瀕死停機 + EXP 停滯偵測 |
| 喝藥 | 設定檔有 `potion: delete` 但程式沒有任何使用它的分支 | `potions` 依比例門檻，且 doctor 會擋掉「門檻低於停機線」這種矛盾設定 |
| 走位 | 隨機／固定方向按鍵（開迴路） | 巡邏點閉迴路 + 爬繩/下跳平台，每步回頭確認小地圖座標真的變了 |
| 怪物偵測 | 只有 YOLO，**必須先自己訓練模型**（repo 裡的 `best.pt` 是 132 bytes 的 placeholder） | 描邊偵測**零設定即可用**，另有 template / YOLO / 遠端推理三條路 |
| 決策 | 單一 for 迴圈，一輪最多做 3 個動作 | 純函式優先權狀態機，可窮舉測試 |
| 測試 | 0 個單元測試（`tools/test_*.py` 是手動示範腳本，要人看畫面） | 431 個 pytest + CI + 真實截圖 ground truth |
| 離線開發 | 必須開遊戲 | `--source 截圖` 可跑完整 pipeline |

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

- **可測試性**：三個開源參考專案都沒有單元測試；本專案 431 個 pytest + CI + 真實截圖 ground truth
- **離線開發**：`--source 截圖` 可跑完整 pipeline，參考專案都必須開遊戲才能調
- **決策層純函式**：auto-maple 的決策散在 bot/routine/命令簿多處，狀態耦合全域 config；我們的 `fsm.decide()` 可以直接窮舉測試
- **ML 升級路徑**：YOLO 訓練管線（自動預標註）與 VLM 督導層是兩個參考專案都沒有的
