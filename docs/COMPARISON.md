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

## 評估後不採用

| 功能 | 來源 | 不採用原因 |
|---|---|---|
| 四叉樹地圖 + A* 尋路 | auto-maple `layout.py` | 需要預先錄製每張地圖的平台資料。我們改用「人工指定的多層 waypoint + 閉迴路驗證」：表達力較弱（不會自己繞路），但零錄製成本、失敗行為可預測。**繩子位置仍必須人工填**——自動找繩子誤判一次就會讓整條路線錯亂，代價不划算 |
| 符文（rune）自動破解 | 兩者皆有（TensorFlow 箭頭辨識） | 符文是 GMS/Artale 的反外掛機制；自動破解反偵測意味太強，超出研究範圍。本專案遇到異常畫面的策略一律是「暫停 + 警報 + 截圖」，交還給人 |
| tkinter GUI | auto-maple `gui/` | `tools/debug_view.py` 已涵蓋觀測需求；GUI 維護成本高、與研究目的無關 |
| 路線錄製成彩色路徑圖 | MapleStoryAutoLevelUp `maps/` | 表達力強但工具鏈重；YAML waypoint 對單平台場景足夠、可讀可 diff |
| 自動登入 / 換頻道 | 兩者皆有 | 涉及帳號憑證自動化，超出打怪研究範圍 |
| 玩家全域定位（畫面對整張大地圖配準） | MapleStoryAutoLevelUp `minimaps/` | 我們用小地圖座標已滿足巡邏需求，配準的成本收益不划算 |
| **防測謊機、防 GM** | 楓之谷達人 | 這是反偵測，不是自動化。本專案的原則一直是「遇到看不懂的畫面就暫停 + 警報 + 截圖，交還給人」——測謊機正是那種畫面。實作它等於把專案從「自動化研究」變成「規避偵測工具」，法律風險也完全不同（見 README 用途聲明） |
| **定時換頻** | 楓之谷達人 | 需要操作遊戲選單，且動機通常是躲人。我們已有更保守的版本：`pause_when_players` 偵測到其他玩家就停手，人走了自動繼續 |
| 記憶體外掛（無敵／瞬移／無延遲） | 台灣商業外掛常見功能 | 需要讀寫遊戲行程記憶體。本專案架構明確排除（README：不讀寫記憶體、不碰封包），這也是 MDY v. Blizzard 那類判決的核心爭點 |

## 比對後確認我們已領先的部分

- **可測試性**：兩個參考專案都沒有單元測試；本專案 156 個 pytest + CI + 真實截圖 ground truth
- **離線開發**：`--source 截圖` 可跑完整 pipeline，參考專案都必須開遊戲才能調
- **決策層純函式**：auto-maple 的決策散在 bot/routine/命令簿多處，狀態耦合全域 config；我們的 `fsm.decide()` 可以直接窮舉測試
- **ML 升級路徑**：YOLO 訓練管線（自動預標註）與 VLM 督導層是兩個參考專案都沒有的
