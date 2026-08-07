# maplebot

楓之谷經典版自動打怪程式（學術研究用途）。

純「螢幕視覺」方案：**擷取畫面 → 影像辨識 → 狀態機決策 → 模擬按鍵**。
不讀寫遊戲記憶體、不碰封包、沒有任何反偵測功能——整個 pipeline 就是一個
即時電腦視覺 + 自動化控制的研究題目，所有辨識與決策邏輯都能用截圖離線開發與測試。

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
5. Buff 到期 → 補 buff
6. 攻擊範圍內有怪 → 面向最近的怪施放技能
7. 都沒有 → 沿小地圖巡邏點走位（閉迴路：走一步→重新定位）

## 安裝

- Windows 10/11、Python 3.9+（即時擷取與按鍵僅支援 Windows；離線開發任何平台皆可）
- 遊戲用**視窗模式**執行，建議 800x600
- 若遊戲以系統管理員身分執行，終端機也要用系統管理員開，否則 SendInput 會被擋

```
pip install -r requirements.txt
```

## 快速開始

```bash
# 1. 設定視窗標題（config/default.yaml -> window.title，子字串比對）

# 2. 校正區域：框選 小地圖 / HP / MP / EXP / 主畫面，把輸出貼回 config/default.yaml
python tools/calibrate.py

# 3. 蒐集怪物模板：對著要打的怪框 2~3 張（會自動含左右翻轉）
python tools/grab_template.py --name snail

# 4. 驗證辨識：即時疊框顯示玩家黃點/其他人紅點/血魔比例/怪物框
#    順便從畫面讀玩家的小地圖 x 座標，填進 profile 的巡邏點
python tools/debug_view.py

# 5. 編輯 config/profiles/example.yaml（技能鍵/巡邏點/藥水門檻/buff）

# 6. 先 dry-run 看決策（不會按鍵），確認合理再正式執行
python main.py --profile config/profiles/example.yaml --dry-run
python main.py --profile config/profiles/example.yaml
```

**熱鍵**：`F9` 暫停/繼續、`F12` 緊急停止（可在 config 改）。

## 離線開發與測試

不開遊戲也能開發：所有辨識/決策都可以吃靜態截圖。

```bash
pytest                                   # 32 個單元測試（合成影像 + 真實截圖真值）
python main.py --source tests/fixtures/mapleaga_800x600.jpg --dry-run --max-ticks 5
python tools/debug_view.py --source <你的截圖>
```

`tests/fixtures/mapleaga_800x600.jpg` 是真實客戶端截圖，畫面上 HP 100%、MP 100%、
EXP 59.89%，測試直接拿這些畫面顯示值當 ground truth 驗證辨識精度。

## 設定檔重點

`config/default.yaml`（全域）：

| 欄位 | 說明 |
|---|---|
| `window.title` | 遊戲視窗標題子字串 |
| `regions.*` | 小地圖/HP/MP/EXP/主畫面的 ROI，用 `tools/calibrate.py` 產生 |
| `vision.color_tolerance` | 小地圖點色容差；誤判/漏判用 `tools/debug_view.py` 邊看邊調 |
| `vision.mob_detector` | `template`（預設）或 `yolo` |
| `safety.*` | 危險線、熱鍵、他人暫停、watchdog 秒數 |
| `advisor.*` | 選配 VLM 督導層（見下） |

`config/profiles/*.yaml`（一張地圖一份）：巡邏點、攻擊鍵/範圍/施放時間、
buff 週期、藥水鍵與門檻。

## 進階：ML 感知層（有 GPU 的路線，例如 RTX 5090）

模板匹配對「怪物動作幀變化、重疊、背景干擾」比較脆弱。有顯卡時建議升級成兩層：

**1. 即時感知：YOLO 怪物偵測（取代模板匹配，毫秒級）**

```bash
python tools/collect_dataset.py --interval 2 --count 500   # 蒐集畫面到 datasets/raw/
# 用 labelImg / Roboflow / CVAT 標註怪物框，整理成 YOLO 格式
pip install ultralytics
yolo detect train data=datasets/mobs.yaml model=yolo11n.pt imgsz=800 epochs=80
```

訓練完把 `config/default.yaml` 改成：

```yaml
vision:
  mob_detector: yolo
  yolo_model: runs/detect/train/weights/best.pt
```

介面完全相同（`maplebot/vision/yolo_mobs.py`），決策層不用動。
YOLO11n 在 RTX 5090 上推理只要 1~3ms/幀，主迴圈 8 FPS 完全無感。

**2. 大局督導：本地 VLM（slow loop，選配）**

VLM 單張推理要 0.5~2 秒，當不了即時反應層，但很適合每隔一段時間看一次
「整體狀況」：卡死、對話框、驗證視窗、斷線畫面。用 vLLM / LM Studio / Ollama
在本機開 OpenAI 相容端點即可：

```bash
vllm serve Qwen/Qwen2.5-VL-7B-Instruct --max-model-len 8192
```

```yaml
advisor:
  enabled: true
  endpoint: "http://127.0.0.1:8000/v1/chat/completions"
  model: "Qwen/Qwen2.5-VL-7B-Instruct"
  interval: 20.0
```

VLM 判定異常時**只會把 bot 切到暫停並截圖存證**，不會執行 VLM 產生的任何操作指令。

## 專案結構

```
main.py                      # 進入點（--dry-run / --source / --max-ticks）
config/default.yaml          # 全域設定（視窗、ROI、視覺參數、安全、advisor）
config/profiles/*.yaml       # 各地圖 profile（巡邏、攻擊、buff、藥水）
maplebot/
  capture.py                 # mss 視窗擷取 / 靜態圖片來源（離線）
  window.py                  # 找遊戲視窗、client 區座標、DPI aware
  vision/minimap.py          #   玩家黃點、其他玩家紅點
  vision/status.py           #   HP/MP/EXP 比例（逐欄色彩統計）
  vision/mobs.py             #   模板匹配偵測 + NMS（MobDetector 介面）
  vision/yolo_mobs.py        #   YOLO 偵測（選配，同介面）
  brain/state.py             # GameState：每 tick 的感知快照
  brain/fsm.py               # 決策狀態機（純函式，單元測試涵蓋）
  brain/advisor.py           # VLM 督導層（選配）
  control/input_win.py       # SendInput scancode 鍵盤層（tap/hold/release_all）
  safety.py                  # 熱鍵、危險停機、watchdog、異常截圖
  runner.py                  # 主迴圈：擷取→感知→決策→執行
tools/
  calibrate.py               # 框選 ROI 產生 config
  grab_template.py           # 擷取怪物模板
  debug_view.py              # 即時辨識結果疊框
  collect_dataset.py         # YOLO 資料集蒐集
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
