# 與高星開源專案的比對報告

比對對象（2026-08 當時 GitHub 星數）：

| 專案 | 星數 | 定位 |
|---|---|---|
| [tanjeffreyz/auto-maple](https://github.com/tanjeffreyz/auto-maple) | 684★ | GMS 全功能 bot：CV + TensorFlow 符文破解 + GUI + routine 系統，此類專案中星數最高 |
| [KenYu910645/MapleStoryAutoLevelUp](https://github.com/KenYu910645/MapleStoryAutoLevelUp) | 356★ | 楓之谷 Artale（經典版）專用：純視覺、狀態機、路線錄製 |

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
| 巡邏點附掛按鍵動作（跳上平台等） | auto-maple routine 的 Point+commands（簡化版） | `waypoints: [{x: 95, keys: [alt]}]` → `RunKeys` |
| directional / AoE 兩種攻擊模式 | MapleStoryAutoLevelUp `attack: directional/aoe_skill` | `attack.type: aoe` 時不轉向直接施放 |
| 黑屏偵測（斷線/換頻道/讀圖） | auto-maple notifier 的 room-change 偵測（>90% 全黑） | `safety.is_black_screen()` → 自動暫停 + 截圖 |
| 危險事件聲音警報 | auto-maple notifier（pygame 音檔） | `alerts.py` 用 winsound 免音檔：Panic 長響、有人出現短響 |
| 按鍵時間 ±20% 抖動 | auto-maple `vkeys.press` 的 `0.8 + 0.4*random()` | `Executor._dur()` 全動作套用 |
| Panic 先按回城卷再停止 | MapleStoryAutoLevelUp `return_home_if_no_potion` | `profile.panic_return_key` |
| default + local 兩層設定 | MapleStoryAutoLevelUp `config_default` + `config_custom` | `config/local.yaml` 深度合併覆寫 |

## 評估後不採用

| 功能 | 來源 | 不採用原因 |
|---|---|---|
| 四叉樹地圖 + A* 尋路 | auto-maple `layout.py` | 需要預先錄製每張地圖的平台資料；我們的左右巡邏 + 脫困已覆蓋單平台練功場景，複雜地圖再議 |
| 符文（rune）自動破解 | 兩者皆有（TensorFlow 箭頭辨識） | 符文是 GMS/Artale 的反外掛機制；自動破解反偵測意味太強，超出研究範圍。本專案遇到異常畫面的策略一律是「暫停 + 警報 + 截圖」，交還給人 |
| tkinter GUI | auto-maple `gui/` | `tools/debug_view.py` 已涵蓋觀測需求；GUI 維護成本高、與研究目的無關 |
| 路線錄製成彩色路徑圖 | MapleStoryAutoLevelUp `maps/` | 表達力強但工具鏈重；YAML waypoint 對單平台場景足夠、可讀可 diff |
| 自動登入 / 換頻道 | 兩者皆有 | 涉及帳號憑證自動化，超出打怪研究範圍 |
| 玩家全域定位（畫面對整張大地圖配準） | MapleStoryAutoLevelUp `minimaps/` | 我們用小地圖座標已滿足巡邏需求，配準的成本收益不划算 |

## 比對後確認我們已領先的部分

- **可測試性**：兩個參考專案都沒有單元測試；本專案 67 個 pytest + CI + 真實截圖 ground truth
- **離線開發**：`--source 截圖` 可跑完整 pipeline，參考專案都必須開遊戲才能調
- **決策層純函式**：auto-maple 的決策散在 bot/routine/命令簿多處，狀態耦合全域 config；我們的 `fsm.decide()` 可以直接窮舉測試
- **ML 升級路徑**：YOLO 訓練管線（自動預標註）與 VLM 督導層是兩個參考專案都沒有的
