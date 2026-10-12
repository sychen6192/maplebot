# maplebot

楓之谷經典版的自動打怪研究專案：**只看螢幕、只送模擬輸入**。

擷取 → 感知 → 決策 → 模擬輸入。不讀記憶體、不碰封包（[ADR 0001](docs/adr/0001-vision-only-no-memory-or-packets.md)），
所以整條流程拿一張截圖就能離線重現、離線測試。

> **用途聲明**：本專案僅供電腦視覺／自動化控制的學習研究。在官方伺服器使用外掛
> 違反遊戲服務條款，可能導致帳號停權；請只在你自己擁有或獲得允許的環境
>（私服、本機測試環境）中實驗。

## 特色

- **找怪零設定**：靠 sprite 的黑色描邊找怪，不用截模板、不用訓練。要更穩可換模板／YOLO／遠端推理，決策層不用動
- **路線走一趟就好**：照平常走一遍，軌跡自動壓成巡邏點；單層地圖寫 `waypoints: auto` 開場自己探邊；
  多層地圖的垂直移動（抓繩、往下跳）每一步都回頭確認小地圖 y 真的變了
- **認得自己**：靠腳下的角色名牌定位，不會把自己當成怪站著打一整晚
- **出事會停**：HP 見底先灌藥搶救、拉不回來才停機（可先回城）；其他玩家出現就暫停；
  黑屏、找不到角色、經驗不動就暫停並截圖；遊戲行程消失就停手；死亡自動復活，10 分鐘內死 3 次就停機
- **看得到發生什麼**：開跑前 `tools/doctor.py` 自檢、跑的時候每分鐘印分段耗時、收工自動出報告
- **不開遊戲也能開發**：`decide()` 是純函式（[ADR 0002](docs/adr/0002-decide-is-a-pure-function.md)），900 多個測試全部離線

## 快速開始

需要 Windows 10/11、Python 3.9+，遊戲用**視窗模式**（建議 800x600）。

```powershell
git clone https://github.com/sychen6192/maplebot.git
cd maplebot
pip install -r requirements.txt
python gui.py
```

開出來的「mapleai 控制台」照 **準備** 分頁一步步做：
遊戲視窗 → 校正 → 截角色名牌 → 辨識自檢 → 另存為我的地圖 → 巡邏路線 → 預演一次，然後 ▶ 開始打怪。

- 範本（`example.yaml`、`範本-*.yaml`）不會被改到，設定會另存成你自己的地圖
- 遊戲用系統管理員執行的話，控制台也要用系統管理員開——不然 Windows 會默默丟掉按鍵
- 遊戲中 `F9` 暫停／繼續、`F12` 停止

第一次用、想看每一步該長什麼樣子：**[docs/TUTORIAL.md](docs/TUTORIAL.md)**。

### 命令列

兩邊共用同一套引擎與設定檔。

```powershell
python tools/doctor.py                    # 開跑前自檢：套件、設定、region、profile、遊戲視窗
python tools/calibrate.py --write         # 校正 region，寫進 config/local.yaml
python tools/grab_template.py --dir data/templates/ui --name player_nametag   # 截角色名牌
python tools/debug_view.py --snapshot check.png                              # 偵測器現在看到什麼
python main.py --profile config/profiles/<地圖>.yaml --dry-run   # 只看決策，不送按鍵
python main.py --profile config/profiles/<地圖>.yaml
```

## 運作方式

每個 tick（預設每秒 8 次）擷取一幀，讀出小地圖位置、HP/MP/EXP、怪物，交給狀態機決策：
**保命 > 喝藥 > 有其他玩家就暫停 > buff > 打怪 > 巡邏**。

- **兩層設定**：Config（`config/default.yaml` + `local.yaml`）綁這台電腦與視窗；
  Profile（`config/profiles/`）綁角色與地圖，新地圖從 `範本-*.yaml` 開始，射程怎麼抓見 [職業範本.md](config/profiles/職業範本.md)
- **解析度無關**：像素門檻都以 790px 寬的 playfield 為基準，實跑時自動換算（[ADR 0003](docs/adr/0003-pixel-thresholds-use-a-790px-reference-width.md)）
- **偵測器可換**：`outline`（預設）／`template`／`yolo`／`remote`，換哪個決策層都不知道

## 開發

```bash
python -m pytest -q                                                          # 全部離線
python main.py --source tests/fixtures/mapleaga_800x600.jpg --max-ticks 20   # 用截圖跑整條主迴圈（自動 dry-run）
```

## 文件

| 想做什麼 | 看這裡 |
|---|---|
| 手把手設定、每一步的預期結果、疑難排解 | [docs/TUTORIAL.md](docs/TUTORIAL.md) |
| 升級 YOLO 偵測（拿描邊當老師，免手動標註）、轉 ONNX | [docs/YOLO_TRAINING.md](docs/YOLO_TRAINING.md) |
| 選配的 VLM 督導層（只做判讀，從不執行它給的指令） | `config/default.yaml` 的 `advisor` 段 |
| 跟同類高星開源專案的取捨 | [docs/COMPARISON.md](docs/COMPARISON.md) |
| 用語定義、已定案不再重議的設計決策 | [CONTEXT.md](CONTEXT.md)、[docs/adr/](docs/adr/) |
