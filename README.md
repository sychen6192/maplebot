# maplebot

楓之谷經典版（MapleStory classic）自動打怪研究專案：即時電腦視覺 + 規則決策 + 輸入模擬的完整 pipeline。

純「螢幕視覺」方案：**擷取畫面 → 影像辨識 → 狀態機決策 → 模擬按鍵**。
不讀寫遊戲記憶體、不碰封包、沒有任何反偵測功能——就是一個可以完整離線開發、
離線測試的即時 CV + 自動化控制研究題目。

**特色一覽**

- **感知**：小地圖定位（玩家/其他玩家、角落模板自動找 ROI）、HP/MP/EXP 比例讀取、
  怪物偵測**零設定即可用**（靠 sprite 黑色描邊，不用模板/訓練/標註），
  也可切換模板匹配或自訓 YOLO
- **決策**：純函式優先權狀態機——保命 > 補給 > 禮讓 > buff > 打怪 > 巡邏，
  含卡住偵測自動脫困，單元測試完整涵蓋
- **走位**：單層地圖巡邏點**可自動量測**（`waypoints: auto` 開場左右撞牆抓範圍）；
  多層地圖支援爬繩／下跳平台，**每一步都回頭確認小地圖 y 真的變了**，
  爬不上去會重試再放棄該點而不是卡死
- **控制**：SendInput scancode（DirectInput 遊戲吃得到），按鍵時間帶 ±20% 抖動
- **安全**：HP 危險線自動停機（可先回城）、其他玩家出現先暫停、黑屏/找不到角色
  自動暫停 + 截圖存證 + 聲音警報
- **可測性**：156 個 pytest（合成影像 + 真實截圖真值），整條主迴圈可用一張截圖 dry-run
- **ML 擴充**：YOLO 訓練管線（蒐集→自動預標註→校對→訓練→部署）與本地 VLM 督導層

設計對照過同類最高星的開源專案（684★ auto-maple、356★ MapleStoryAutoLevelUp），
採用/不採用清單見 **[docs/COMPARISON.md](docs/COMPARISON.md)**。

> **用途聲明**：本專案僅供電腦視覺／自動化控制的學習研究。在官方伺服器使用外掛
> 違反遊戲服務條款，可能導致帳號停權；請只在你自己擁有或獲得允許的環境
>（私服、本機測試環境）中實驗。

## 運作原理

```
┌─────────────┐   ┌──────────────────────────┐   ┌─────────────────┐   ┌──────────────┐
│ capture      │   │ vision                    │   │ brain/fsm        │   │ control       │
│ mss 視窗擷取 │──▶│ minimap: 玩家/其他人      │──▶│ 優先權狀態機      │──▶│ SendInput     │
│ (或靜態截圖) │   │ status : HP/MP/EXP 比例   │   │ (純函式,可測試)   │   │ scancode 按鍵 │
└─────────────┘   │ mobs   : 模板匹配 / YOLO  │   └─────────────────┘   └──────────────┘
                  └──────────────────────────┘        ▲ 每秒 8 tick
                        safety: F9 暫停 / F12 停止 / HP 危險線停機 / watchdog
                        advisor(選配): 本地 VLM 每 20s 檢查一次大局
```

決策優先權（高→低）：

1. 讀不到畫面狀態 → 等待（連續超時會自動暫停 + 截圖存證）
2. **HP 低於危險線 → 直接停機**（`safety.critical_hp_ratio`）
3. HP/MP 低於門檻 → 喝藥
4. 小地圖出現其他玩家 → 暫停動作（`safety.pause_when_players`）
5. Buff 到期 → 補 buff（垂直移動途中跳過）
6. 攻擊範圍內有怪 → 面向最近的怪施放技能（垂直移動途中跳過）
7. `waypoints: auto` 還沒校正 → 左右試探地圖邊界
8. 巡邏中卡住 → 跳一下脫困
9. x 到位但樓層不對 → 爬繩上樓／下繩或下跳平台
10. 都沒有 → 沿小地圖巡邏點走位（閉迴路：走一步→重新定位）

第 5、6 項在垂直移動途中會讓路：掛在繩子上按方向鍵或技能鍵會掉下來。
補血補魔不讓路——那是保命。

## 安裝

- Windows 10/11、Python 3.9+（即時擷取與按鍵僅支援 Windows；離線開發任何平台皆可）
- 遊戲用**視窗模式**執行，建議 800x600
- 若遊戲以系統管理員身分執行，終端機也要用系統管理員開，否則 SendInput 會被擋

```
pip install -r requirements.txt
```

用虛擬環境（推薦）：

```
uv venv && .venv\Scripts\activate && uv pip install -r requirements.txt
```

有 GPU 的機器要跑訓練或推理伺服器，另外裝 `requirements-server.txt`
（見 [docs/YOLO_TRAINING.md](docs/YOLO_TRAINING.md)）。

## 快速開始

> 第一次用？**[docs/TUTORIAL.md](docs/TUTORIAL.md)** 有含預期畫面的手把手教學，
> 從 clone 一路帶到掛機與接上 Ollama 督導層。

```bash
# 1. 設定視窗標題（config/default.yaml -> window.title，子字串比對）

# 2. 校正區域：框選 小地圖 / HP / MP / EXP / 主畫面，把輸出貼回 config/default.yaml
python tools/calibrate.py

# 3.（選配）怪物偵測預設就是零設定的 outline，要用模板路線才需要這步
python tools/grab_template.py --name snail

# 4. 驗證辨識：即時疊框顯示玩家黃點/其他人紅點/血魔比例/怪物框
#    多層地圖要用 --track 抄下各樓層與繩子的小地圖 (x, y)
python tools/debug_view.py

# 5. 編輯 config/profiles/example.yaml（技能鍵/巡邏點/藥水門檻/buff）

# 6. 先 dry-run 看決策（不會按鍵），確認合理再正式執行
python main.py --profile config/profiles/example.yaml --dry-run
python main.py --profile config/profiles/example.yaml
```

**熱鍵**：`F9` 暫停/繼續、`F12` 緊急停止（可在 config 改）。

### 單層地圖：巡邏點可以不用自己量

profile 裡把 `patrol.waypoints` 整行換成 `auto`，開場會左右各走到撞牆量出可走
範圍，內縮幾 px 自動生成兩個巡邏點。配上預設的零設定怪物偵測，單層練功地圖
只要校正一次 ROI（第 2 步）+ 填技能鍵藥水鍵就能掛。

```yaml
patrol:
  waypoints: auto
```

量到的範圍小於 `probe_min_span_px`（預設 12px）會直接停機並說明原因——
最常見的是方向鍵沒送進遊戲（遊戲用系統管理員跑的話終端機也要），
這種情況要吵出來，不能默默站在原地打空氣。

### 多層地圖：爬繩與下跳平台

巡邏點加上 `y` 就會處理樓層。完整範例見
[`config/profiles/multilevel.yaml`](config/profiles/multilevel.yaml)：

```yaml
patrol:
  waypoints:
    - { x: 30 }                        # 下層左端
    - { x: 100 }                       # 下層右端
    - { x: 68, y: 20 }                 # x 對到繩子(68) -> 爬到上層(y=20)
    - { x: 40, y: 20 }                 # 上層走位
    - { x: 40, y: 45, descend: jump }  # 下跳回下層（descend: rope = 抓繩下降）
```

**繩子在哪必須你告訴它**——程式不會自己找繩子。用 `tools/debug_view.py --track`
站到繩子前抄下小地圖 x 即可。

垂直移動是閉迴路的：每爬一步都回頭確認小地圖 y 真的變了。沒抓到繩子或爬一半被
怪打掉，y 會停住，這時先脫困微調位置重新對繩，連續失敗超過 `climb_retries` 次
就放棄這個巡邏點繼續走下一個。所以繩子座標寫錯的後果是「少走上層」，
不是整隻卡死在繩子前面。爬繩途中不會轉向打怪也不會補 buff（會掉下來），
但補血補魔照舊。

## 離線開發與測試

不開遊戲也能開發：所有辨識/決策都可以吃靜態截圖。

```bash
pytest                                   # 單元測試（合成影像 + 真實截圖真值）
python main.py --source tests/fixtures/mapleaga_800x600.jpg --dry-run --max-ticks 5
python tools/debug_view.py --source <你的截圖>
```

推上 GitHub 後 CI（`.github/workflows/ci.yml`）會自動跑整套測試。

`tests/fixtures/mapleaga_800x600.jpg` 是真實客戶端截圖，畫面上 HP 100%、MP 100%、
EXP 59.89%，測試直接拿這些畫面顯示值當 ground truth 驗證辨識精度。

## 設定檔重點

`config/default.yaml`（全域）：

| 欄位 | 說明 |
|---|---|
| `window.title` | 遊戲視窗標題子字串 |
| `regions.*` | 小地圖/HP/MP/EXP/主畫面的 ROI，用 `tools/calibrate.py` 產生 |
| `vision.color_tolerance` | 小地圖點色容差；誤判/漏判用 `tools/debug_view.py` 邊看邊調 |
| `vision.mob_detector` | `outline`（預設，零設定）/ `template` / `yolo` / `remote` |
| `vision.filter_followers` | 把寵物從怪物清單剔除（角色移動時牠不會在畫面上滑動） |
| `safety.*` | 危險線、熱鍵、他人暫停、watchdog 秒數 |
| `safety.attack_stall_seconds` | 連續攻擊這麼久位置卻沒變就強制去巡邏（打不死的東西） |
| `advisor.*` | 選配 VLM 督導層（見下） |

`config/profiles/*.yaml`（一張地圖一份）：

| 欄位 | 說明 |
|---|---|
| `patrol.waypoints` | 巡邏點清單，或 `auto` 讓程式開場自己量（單層地圖） |
| `patrol.probe_*` | `auto` 的探邊參數（試探步長、撞牆判定、邊界內縮、最小可走範圍） |
| `patrol.y_tolerance` / `climb_*` | 垂直移動：到位容差、每步時間、爬不動判定與重試次數、上下鍵 |
| `patrol.tolerance` / `step_*` / `stuck_*` / `jump_key` | 水平走位與卡住脫困 |
| `attack.*` | 技能鍵、`directional`/`aoe`、攻擊範圍、施放時間、連發數 |
| `buffs` / `potions` | buff 週期、藥水鍵與門檻 |
| `panic_return_key` | 進入危險線時先按回城卷再停止（留空 = 直接停止） |

## 進階：ML 感知層（有 GPU 的路線，例如 RTX 5090）

模板匹配對「怪物動作幀變化、重疊、背景干擾」比較脆弱。有顯卡時建議升級成兩層：

**1. 即時感知：YOLO 怪物偵測（取代模板匹配，毫秒級）**

完整教學見 **[docs/YOLO_TRAINING.md](docs/YOLO_TRAINING.md)**，流程一條龍：

```bash
python tools/collect_dataset.py --interval 2 --count 400   # 邊玩邊蒐集（自動去重）
python tools/autolabel.py                                  # 用模板匹配自動預標註
labelImg datasets/raw datasets/raw/classes.txt              # 人工校對（大多只是掃過）
python tools/prepare_dataset.py                            # 切 train/val + dataset.yaml
python tools/train_yolo.py                                 # 5090 上幾分鐘練完
```

訓練結尾會直接印出要貼進 `config/default.yaml` 的兩行（`mob_detector: yolo` +
權重路徑）。介面完全相同（`maplebot/vision/yolo_mobs.py`），決策層不用動。
YOLO11n 在 RTX 5090 上推理只要 1~3ms/幀，主迴圈 8 FPS 完全無感。

**2. 大局督導：本地 VLM（slow loop，選配）**

VLM 單張推理要 0.5~2 秒，當不了即時反應層，但很適合每隔一段時間看一次
「整體狀況」：卡死、對話框、驗證視窗、斷線畫面。要用**視覺**模型（看得懂截圖）。

用 Ollama 最簡單（設定檔預設就是 Ollama 端點）：

```bash
ollama pull qwen2.5vl:7b     # 6GB；要更強的判斷力可換 qwen2.5vl:32b（21GB）
```

```yaml
advisor:
  enabled: true              # 其餘用預設值（Ollama 127.0.0.1:11434 + qwen2.5vl:7b）
  interval: 20.0
```

vLLM 使用者：`vllm serve Qwen/Qwen2.5-VL-7B-Instruct --max-model-len 8192`，
並把 endpoint 改成 `http://127.0.0.1:8000/v1/chat/completions`。

VLM 判定異常時**只會把 bot 切到暫停並截圖存證**，不會執行 VLM 產生的任何操作指令。

## 專案結構

```
main.py                      # 進入點（--dry-run / --source / --max-ticks）
config/default.yaml          # 全域設定（視窗、ROI、視覺參數、安全、advisor）
config/profiles/example.yaml    # 單層地圖 profile 範例（含 waypoints: auto 說明）
config/profiles/multilevel.yaml # 多層地圖 profile 範例（爬繩 + 下跳平台）
maplebot/
  capture.py                 # mss 視窗擷取 / 靜態圖片來源（離線）
  window.py                  # 找遊戲視窗、client 區座標、DPI aware
  vision/minimap.py          #   玩家點（模板優先/顏色備援）、其他玩家紅點
  vision/locate.py           #   小地圖角落模板自動定位（regions.minimap: auto）
  vision/status.py           #   HP/MP/EXP 比例（逐欄色彩統計）
  vision/mobs.py             #   模板匹配偵測 + NMS（MobDetector 介面）
  vision/outline_mobs.py     #   描邊偵測（預設：零設定，不用模板/訓練）
  vision/follower.py         #   濾掉跟著角色跑的東西（寵物）
  vision/yolo_mobs.py        #   YOLO 偵測（選配，同介面）
  perception.py              # Perceiver：一張完整畫面 -> GameState（每 tick 只擷取一次）
  brain/state.py             # GameState：每 tick 的感知快照
  brain/fsm.py               # 決策狀態機（純函式，單元測試涵蓋）
  brain/advisor.py           # VLM 督導層（選配）
  executor.py                # Executor：Action -> 按鍵序列 + 冷卻/統計
  control/input_win.py       # SendInput scancode 鍵盤層（tap/release_all）
  safety.py                  # 熱鍵、危險停機、黑屏偵測、watchdog、異常截圖
  alerts.py                  # 危險事件嗶聲警報（winsound）
  runner.py                  # 主迴圈調度：擷取→感知→決策→執行 + 安全機制
  dataset.py                 # YOLO 資料集：模板自動預標註、train/val 打包
tools/
  calibrate.py               # 框選 ROI 產生 config
  grab_template.py           # 擷取怪物模板
  debug_view.py              # 即時辨識結果疊框（與主程式共用 Perceiver）
  collect_dataset.py         # 訓練資料蒐集（自動去除重複幀）
  autolabel.py               # 模板匹配自動預標註（labelImg 相容）
  prepare_dataset.py         # 切 train/val + 產生 dataset.yaml
  train_yolo.py              # ultralytics 訓練入口（遊戲畫面特化參數）
docs/TUTORIAL.md             # 手把手教學（含每步預期結果與疑難排解）
docs/YOLO_TRAINING.md        # 5090 訓練流程完整教學
docs/COMPARISON.md           # 與高星開源專案的比對與採用清單
tests/                       # pytest：合成影像 + 真實截圖 ground truth
```

## 疑難排解

- **按鍵沒反應**：遊戲以系統管理員執行時，Python 也要系統管理員權限。
- **抓不到視窗**：確認 `window.title` 是視窗標題的子字串；用視窗模式跑遊戲。
- **座標整組偏移**：Windows 顯示縮放不是 100% 時要重新校正（程式已宣告 DPI aware，
  校正後即一致）。
- **玩家黃點誤判**：某些地圖的小地圖地形帶黃色，調小 `vision.color_tolerance`
  或縮小 minimap ROI 到純地圖畫布，用 `tools/debug_view.py` 驗證。
- **怪物偵測不穩**：多抓幾張不同動作幀的模板、微調 `mob_match_threshold`，
  或直接升級 YOLO 路線。
- **`waypoints: auto` 一開場就停機說可走範圍太小**：角色根本沒動。先確認方向鍵
  有送進遊戲（`--dry-run` 看得到決策但不會按鍵，要拿掉才會真的動），
  以及小地圖 ROI 沒框到地圖畫布以外。
- **一直不上樓 / log 都是「垂直移動連續失敗」**：waypoint 的繩子 `x` 抄錯了，
  用 `tools/debug_view.py --track` 站到繩子前重抄。角色移速快、`tolerance` 又大的
  話也可能停的位置離繩子太遠，把 `tolerance` 調小一點。
