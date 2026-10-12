# tools/：命令列工具

17 支腳本，全部在 **repo 根目錄**執行：`python tools/<名稱>.py`。預設路徑
（`config/default.yaml`、`datasets/raw`、`data/templates/mobs`……）都是相對於目前
目錄，換個目錄執行會找不到檔案。每支都有 `--help`。

依「在哪一台跑」分兩組。只有一台電腦的話兩組都在同一台跑；分組是告訴你 GPU 在
另一台時，哪些要到那台去跑。掛機那台裝 `requirements.txt`，訓練那台另外裝
`requirements-server.txt`（ultralytics，連帶 torch）。

| 腳本 | 做什麼 | 什麼時候用 |
|---|---|---|
| **掛機那台用的** | **設定、實跑、診斷，以及蒐集訓練畫面**（Windows、遊戲開著） | |
| `doctor.py` | 開跑前自檢：套件、設定、region、profile、遊戲視窗。有 ❌ 就回傳 1 | 第一次設定完、改過設定、跑不起來時先跑。GUI 的環境檢查是同一份邏輯（`maplebot/diagnostics/doctor.py`） |
| `calibrate.py` | 框選小地圖／HP／MP／EXP／playfield 這幾個 region；`--write` 寫進 `config/local.yaml`，原本的註解會留著 | 第一次用、換解析度或視窗大小之後。GUI「準備 › 2 校正」跑的就是它 |
| `grab_template.py` | 從畫面框一塊存成模板 PNG（預設存到 `data/templates/mobs`） | 截角色名牌：`--dir data/templates/ui --name player_nametag`（GUI「準備 › 3」）；改用模板偵測器時截怪 |
| `debug_view.py` | 用跟 bot 同一個 Perceiver 辨識，把 region、角色、怪物框、這一幀的決策疊在畫面上 | 想知道偵測器現在看到什麼。`--snapshot` 只存一張圖（GUI「準備 › 4 辨識自檢」）；`--track` 一直印角色的小地圖座標，用來抄巡邏點 |
| `test_keys.py` | 實際送幾次鍵，順便檢查權限（UIPI）和前景視窗 | log 有按鍵、遊戲卻沒反應時。只能在 Windows 跑。名字像測試但不是，pytest 只收 `tests/` |
| `record_route.py` | 你走一遍路線，它印出 `patrol.waypoints` 區塊 | 不開 GUI 錄路線。跟 GUI「錄製路線」同一套引擎（`maplebot/recording/`） |
| `check_remote.py` | 查推理伺服器的 `/health`、連送幾張圖量往返延遲、印出偵測結果 | 偵測器改成 `remote` 之後、開跑之前 |
| `collect_dataset.py` | 定時把 playfield 存進 `datasets/raw/`（`--region minimap` 改存小地圖到 `datasets/raw_minimap/`），跳過跟上一張幾乎一樣的幀 | YOLO 路線的第一步。它要抓遊戲視窗，所以在這台跑，存好的資料夾再搬去訓練那台 |
| **訓練／GPU 那台用的** | **標註、資料集、訓練、匯出、推理伺服器** | |
| `auto_pipeline.py` | 自動標註 → 預覽 → 切 train/val → 訓練，一個指令跑完 | 見下方「五個標註入口」 |
| `autolabel.py` | 只標註，寫出 labelImg 讀得懂的標籤 | 同上 |
| `autolabel_outline.py` | 描邊老師（有模板就加上模板老師聯集）標註，可排除固定 UI | 同上 |
| `label_vlm.py` | 請督導層的 VLM 整張畫面框怪 | 同上 |
| `label_gdino.py` | GroundingDINO 靠文字提示標註 | 同上 |
| `prepare_dataset.py` | 把標好的影像＋標籤切成 train/val，寫到 `datasets/yolo/` | 用 `autolabel`／`autolabel_outline`／`label_vlm`／`label_gdino` 標完之後（`auto_pipeline.py` 會自己做這步） |
| `train_yolo.py` | 只跑訓練 | 資料集已經切好，只想重練：換模型、加 epochs、改 `--imgsz` |
| `export_onnx.py` | 把 `.pt` 轉成 `.onnx`，印出要貼進設定檔的兩行 | 練好之後想讓掛機那台推理快一點。在這台轉一次，把 `.onnx` 複製過去（掛機那台目前仍要裝 torch，見 docs/YOLO_TRAINING.md） |
| `serve_yolo.py` | HTTP 推理伺服器：`POST /detect`、`GET /health`，**沒有身分驗證** | GPU 在另一台時。掛機那台設 `vision.mob_detector: remote`，只在自己的區網用 |

完整的訓練流程與實測數字見 [docs/YOLO_TRAINING.md](../docs/YOLO_TRAINING.md)。

## 五個標註入口：挑哪一個

五個入口正式標註時都在影像旁邊寫同名 `.txt` 和 `classes.txt`，標完接
`prepare_dataset.py` → `train_yolo.py`（`auto_pipeline.py` 自己會接）。沒有框的圖
寫空的 `.txt`，當成負樣本。

| 入口 | 老師、走哪段程式 | 挑它的時候 |
|---|---|---|
| `auto_pipeline.py` | `training.dataset.autolabel_dir`，老師來自 `training/teachers.py`。預設描邊老師，偵測器由 `make_detector` 建，跟 bot 實跑用的是同一個函式；`--teacher template` 改模板老師 | 想一個指令從畫面練到權重。標完一定先輸出 6 張預覽到 `<images>/_preview`；`--check` 標完、出完預覽就停，不切分也不訓練 |
| `autolabel.py` | 跟 `auto_pipeline.py` 同一個 `autolabel_dir`、同一組老師，但只標不練 | 想先用 labelImg 人工修框再訓練。`--preview N` 只出 N 張預覽、不寫標籤；描邊框太少時用 `--scan-black` 試幾個門檻。小地圖玩家點模型（`--teacher minimap_dot`）也是用這支標 |
| `autolabel_outline.py` | `training.dataset.autolabel_outline_dir`。自己建描邊偵測器、不經 `make_detector`：面積下限在命令列給，刻意比實跑寬鬆（150 對 300），其餘描邊參數沿用 config；`--templates` 資料夾裡有模板就跟模板老師依 IoU 聯集 | YOLO_TRAINING 建議的路線：換地圖不用重截模板，有模板就順便提高召回率。畫面上有固定的 UI（小地圖、快捷鍵盤、公告）要用 `--exclude x,y,w,h` 當場排掉時 |
| `label_vlm.py` | `training.dataset.ground_label_dir` 加 `Advisor.ground`：督導層的 VLM 整張畫面一隻一框，只有 `monster` 進標籤，item／player／pet 留在快取 | 已經在 config 設好 `advisor.endpoint`，而且描邊老師常把相鄰的怪和掉落物黏成一框。先跑 `--limit 10 --preview` 抽查。問不到答案的圖**不寫**標籤檔（寫空檔等於告訴學生「這張沒有怪」），指令回傳 1，重跑只補問那些 |
| `label_gdino.py` | HuggingFace transformers 版的 GroundingDINO，文字提示（預設 `monster`），`--reject` 剔掉 NPC／玩家／招牌，用 `training.dataset.write_yolo_labels` 寫檔 | 不想截模板、也沒有 VLM 督導層，想試開放詞彙模型時。它是拿真實照片練的，不一定認得卡通 sprite：先 `--test <一張截圖>` 確認框得到怪。要另外裝 `transformers pillow` |

兩條描邊路線差在哪：`auto_pipeline.py`／`autolabel.py` 的描邊老師吃 config 裡的
`outline_*` 和 `mob_exclude`，標出來的就是 bot 實跑看到的；`autolabel_outline.py`
用來刻意標得比實跑寬鬆、加上模板聯集，或在命令列排除區塊。

## 改這個資料夾之前

這裡的檔名有很多地方寫死，**不要搬家或改名**：GUI「準備」分頁的按鈕用相對路徑
執行 `calibrate.py`、`grab_template.py`、`debug_view.py`（`maplebot/gui/controller.py`），
doctor 的修法字串寫著 `python tools/<名稱>.py`，`maplebot/gui/live.py` 再用 regex
把它們換成控制台的按鈕；測試也照檔案路徑載入 `label_gdino.py`、`serve_yolo.py`、
`doctor.py`、`calibrate.py`，並直接執行 `debug_view.py`。每支腳本開頭把 `tools/` 的上一層
（repo 根目錄）加進 `sys.path`，所以都要剛好在 repo 根目錄的下一層。
