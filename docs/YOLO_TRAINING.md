# YOLO 怪物偵測訓練指南（RTX 5090）

把感知層從模板匹配升級成 YOLO 的完整流程。跑完之後，怪物偵測從
「對動作幀敏感、會被重疊干擾」變成毫秒級、對變化魯棒的偵測器，
決策層（`brain/fsm.py`）完全不用改。

整條管線：

```
collect_dataset  ->  autolabel      ->  labelImg 校對  ->  prepare_dataset  ->  train_yolo  ->  改 config 部署
（邊玩邊蒐集）      （模板匹配預標註）   （人工修框）        （切 train/val）      （5090 幾分鐘）
```

## 0. 環境準備（一次性，在有 GPU 的那台）

用 [uv](https://docs.astral.sh/uv/) 開虛擬環境最省事——不需要先有 pip，
連 Python 都能幫你裝：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env          # 或直接重開終端機

cd ~/projects/maplebot
uv venv --python 3.11                # 系統沒有 3.11 會自動下載
source .venv/bin/activate
uv pip install -r requirements-server.txt      # torch/opencv/ultralytics 約一分鐘

python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# 預期輸出類似: 2.13.0+cu130 True NVIDIA GeForce RTX 5090
```

之後每次開新終端機都要先 `source .venv/bin/activate`；
不想每次啟用的話，把指令改成 `uv run python tools/...` 即可。

兩個常見狀況：

- **`cuda.is_available()` 是 False，或跑起來報 `no kernel image available`**：
  RTX 5090 是 Blackwell（sm_120），需要夠新的 CUDA build。裝明確版本：
  ```bash
  uv pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu128
  ```
- **headless Linux 出現 `libGL.so.1: cannot open shared object file`**：
  ultralytics 依賴的 `opencv-python` 需要 GUI 函式庫。二選一：
  ```bash
  sudo apt install -y libgl1 libglib2.0-0                                    # 有 sudo
  uv pip uninstall opencv-python && uv pip install opencv-python-headless    # 沒 sudo
  ```

> 遊戲機那台**不需要**這些，裝原本的 `requirements.txt` 就好。

兩個常見狀況：

- **`cuda.is_available()` 是 False，或跑起來報 `no kernel image available`**：
  RTX 5090 是 Blackwell（sm_120），需要夠新的 CUDA build。裝明確版本：
  ```bash
  pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu128
  ```
- **headless Linux 出現 `libGL.so.1: cannot open shared object file`**：
  ultralytics 依賴的 `opencv-python` 需要 GUI 函式庫。二選一：
  ```bash
  sudo apt install -y libgl1 libglib2.0-0            # 有 sudo
  pip uninstall -y opencv-python && pip install opencv-python-headless   # 沒 sudo
  ```

> 遊戲機那台**不需要**這些，裝原本的 `requirements.txt` 就好。

## 1. 蒐集畫面

```bash
python tools/collect_dataset.py --interval 2 --count 400
```

- **邊正常遊玩邊蒐集**，讓畫面涵蓋：怪的各種動作幀、不同數量與重疊、
  你會去的每張地圖、以及**一部分完全沒怪的空景**（負樣本，壓誤報用）
- 工具會自動跳過與上一張幾乎相同的幀（`--min-diff 0` 可關）
- 建議量：單一地圖 300~500 張就能練出堪用模型；跨 2~4 張地圖 600~800 張更穩

## 2. 自動預標註（bootstrap，省掉大部分人工）

用你現有的怪物模板（`data/templates/mobs/`）自動產生初版標籤：

```bash
python tools/autolabel.py            # 門檻沿用 config 的 mob_match_threshold
# 想抓寬一點（寧可多框讓人工刪）：
python tools/autolabel.py --threshold 0.65
# 不分怪種、全部合併成一類 mob（打法不挑怪時建議，資料需求更低）：
python tools/autolabel.py --single-class
```

輸出：每張圖同名 `.txt`（YOLO 格式）+ `classes.txt`，與 labelImg 完全相容。
結尾會列出「一隻都沒偵測到」的影像清單——這些通常是模板匹配的弱點案例，
**正是 YOLO 要學的重點，校對時優先人工補框**。

## 3. 人工校對

```bash
pip install labelImg
labelImg datasets/raw datasets/raw/classes.txt
```

操作要點：

- 左側切到 **YOLO** 格式（預設可能是 PascalVOC）
- `W` 畫框、`D` 下一張、`Ctrl+S` 存檔
- 檢查三件事：**漏框**（有怪沒標）、**誤框**（背景/NPC 被標成怪）、
  **框太鬆**（框緊貼怪物身體，不含血條與名牌）
- 純背景圖保持零框直接存檔即可——空標籤就是負樣本

預標註品質夠好的話，這一步大多是掃過去按 `D`，幾百張約 30~60 分鐘。

## 4. 打包訓練集

```bash
python tools/prepare_dataset.py               # 85/15 切 train/val
```

輸出 `datasets/yolo/`（images/labels 各分 train/val）與 `dataset.yaml`。

## 5. 訓練

```bash
python tools/train_yolo.py                    # yolo11n, imgsz 800, 80 epochs
# 想更準（怪很小隻/背景很花時）：
python tools/train_yolo.py --model yolo11s.pt --epochs 120
```

- 已內建遊戲畫面特化參數：關閉旋轉/上下翻轉/透視增強（2D 橫向卷軸用不到），
  保留左右翻轉（怪會轉向）
- 5090 參考速度：yolo11n + 500 張 + 80 epochs ≈ **2~4 分鐘**；batch 用 `-1` 自動吃滿 VRAM
- 看結果：終端的 `mAP50` 是主要指標，遊戲精靈圖通常能到 **0.95+**；
  低於 0.85 通常代表標註有問題（漏標/框不準）而不是模型不行

訓練完會直接印出權重路徑（`runs/mobs/mobs/weights/best.pt`）與要貼進 config 的兩行。
路徑以腳本實際印出的為準。

## 6. 部署與驗證

`config/default.yaml`：

```yaml
vision:
  mob_detector: yolo
  yolo_model: runs/mobs/mobs/weights/best.pt   # 用訓練結尾印出的實際路徑
  yolo_confidence: 0.5
```

```bash
python tools/debug_view.py        # 先看疊框：該框的有框、不該框的沒框
python main.py --profile config/profiles/example.yaml --dry-run   # 再看決策
python main.py --profile config/profiles/example.yaml             # 上線
```

推理耗時 1~3ms/幀，相對 8 FPS 主迴圈（125ms/tick）完全無感；
`vision/yolo_mobs.py` 與模板匹配實作同一個 `MobDetector` 介面，其餘程式碼零改動。

## GPU 在另一台機器？用推理伺服器

遊戲在筆電/主力機、5090 在工作站的話，不用把模型搬來搬去——讓工作站當推理伺服器：

**工作站上**（已照第 0 步建好 venv）：

```bash
source .venv/bin/activate
python tools/serve_yolo.py --model runs/mobs/mobs/weights/best.pt
# 印出 http://0.0.0.0:8100/detect
hostname -I     # 記下這台機器的區網 IP，例如 192.168.1.50（Windows 用 ipconfig）
```

**遊戲機上** `config/local.yaml`：

```yaml
vision:
  mob_detector: remote
  remote_endpoint: "http://192.168.1.50:8100/detect"
  remote_timeout: 1.0
```

遊戲機完全不用裝 PyTorch/ultralytics（只要原本的 requirements）。

延遲概算（主迴圈每 tick 有 125ms 預算）：

| 環節 | 有線區網 | WiFi |
|---|---|---|
| JPEG 編碼 + 傳輸（800x520 約 60KB） | ~2ms | 15~40ms |
| 5090 推理（yolo11n） | 1~3ms | 1~3ms |
| **合計往返** | **5~15ms** | **20~50ms** |

兩者都遠低於預算。WiFi 若不穩可把 `remote_jpeg_quality` 降到 60~70。

連不上或逾時時，客戶端會記錄警告並把該幀當成「沒看到怪」——bot 會繼續巡邏
而不是崩潰。驗證伺服器活著：`curl http://192.168.1.50:8100/health`

> 伺服器沒有身分驗證，只適合自己的區網；不要開到公網。

## 什麼時候需要重練

- 換新地圖/新怪：蒐集新地圖 100~200 張 → autolabel → 校對 → 併入 `datasets/raw/` 重跑 4、5
- 出現固定誤報（某個 NPC/裝飾一直被當成怪）：截 20~30 張含該物件的**空景**加入資料集
  （零框負樣本），重練即可壓掉

## 疑難排解

| 症狀 | 解法 |
|---|---|
| `torch.cuda.is_available()` 是 False | 重裝 cu128 版 PyTorch（見步驟 0），並確認驅動 ≥ 570 |
| `no kernel image is available` | 同上——裝到舊 CUDA 版的 wheel 了 |
| 訓練 mAP 高但實戰漏抓 | 蒐集時畫面不夠多樣；補「實戰漏抓當下」的截圖進資料集重練 |
| 誤報多 | 調高 `yolo_confidence`（0.6~0.7），或加負樣本重練 |
| VRAM 爆（不太可能發生在 5090） | `--batch 32` 手動指定 |
