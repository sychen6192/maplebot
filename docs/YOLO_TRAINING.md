# YOLO 怪物偵測訓練指南（RTX 5090）

把感知層從模板匹配升級成 YOLO 的完整流程。跑完之後，怪物偵測從
「對動作幀敏感、會被重疊干擾」變成毫秒級、對變化魯棒的偵測器，
決策層（`brain/fsm.py`）完全不用改。

> **先等一下——你可能根本不需要訓練。**
>
> 預設的 `outline` 偵測器（`vision/outline_mobs.py`）靠 sprite 的黑色描邊找怪，
> **零模板、零訓練、零標註**，換地圖換怪都直接能用。做法取自
> [MapleStoryAutoLevelUp](https://github.com/KenYu910645/MapleStoryAutoLevelUp)（356★，
> 同為楓之谷 Artale）的 `template_free` 模式。
>
> 先跑 `python tools/debug_view.py --snapshot check.png` 看它抓不抓得到。
> 抓得到就收工，不用碰這份文件。真的不夠穩（背景很暗、怪與地形黏在一起）
> 再往下走 YOLO 路線。

## 核心觀念：你不用手動標註

業界做法叫 **知識蒸餾 / 自動標註（Autodistill）**：用一個「老師」自動把畫面
標好，再訓練一個又快又小的 YOLO「學生」。**人工標註不是必要步驟**——它只是
想再擠一點準度時的選配精修。

老師有兩種，挑一個：

| 老師 | 指令 | 適合 |
|---|---|---|
| **模板匹配**（你已經有了） | `tools/auto_pipeline.py` | 楓谷 sprite 每幀像素幾乎相同，模板在這個場景**反而最可靠**。你已經截過 snail 模板且命中 0.78，直接用 |
| **GroundingDINO**（大模型） | `tools/label_gdino.py` | 完全不想截模板，用一句英文 prompt（"monster"）標。但它是拿真實照片訓練的，對卡通 sprite 不保證認得——**先 `--test` 試一張** |

最短路徑（模板老師，一行指令跑完標註→切分→訓練）：

```
collect_dataset  ->  auto_pipeline  ->  改 config 部署
（邊玩邊蒐集）       （自動標註+訓練，一鍵）  （貼兩行）
```

想用大模型老師就把中間換成 `label_gdino -> prepare_dataset -> train_yolo`。
想再精修才需要 labelImg（見文末「選配：人工精修」）。

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

## 1. 蒐集畫面（遊戲機）

```bash
python tools/collect_dataset.py --interval 2 --count 400
```

- **邊正常遊玩邊蒐集**，讓畫面涵蓋：怪的各種動作幀、不同數量與重疊、
  你會去的每張地圖、以及**一部分完全沒怪的空景**（負樣本，壓誤報用）
- 工具會自動跳過與上一張幾乎相同的幀（`--min-diff 0` 可關）
- 建議量：單一地圖 300~500 張就能練出堪用模型；跨 2~4 張地圖 600~800 張更穩

把 `datasets/raw/` 連同 `data/templates/mobs/` 一起 scp 到有 GPU 的機器。

## 2. 一鍵：自動標註 + 訓練（GPU 機器）

```bash
python tools/auto_pipeline.py
# 想更準（怪小隻/背景花）：python tools/auto_pipeline.py --model yolo11s.pt --epochs 120
# 不分怪種、全部當一類：  python tools/auto_pipeline.py --single-class
```

一條指令跑完：用模板老師自動標註 → 切 train/val → 訓練 → 印出權重路徑與
要貼進 config 的兩行。全程零人工標註。

- 已內建遊戲畫面特化參數：關閉旋轉/上下翻轉/透視增強（2D 橫向卷軸用不到），
  保留左右翻轉（怪會轉向）
- 5090 參考速度：yolo11n + 500 張 + 80 epochs ≈ **2~4 分鐘**
- 終端的 `mAP50` 是主要指標，遊戲精靈圖通常能到 **0.9+**

### 想用大模型老師（免模板）

不想截模板的話，把第 2 步的自動標註換成 GroundingDINO——一句 prompt 就標：

```bash
uv pip install transformers pillow          # 多半已經有了

# 先試一張，確認它看得到你的怪（sprite 是它的弱項，一定要先測）
python tools/label_gdino.py --test datasets/raw/xxxx.jpg --prompt "monster"
# 沒框到就降門檻再試
python tools/label_gdino.py --test datasets/raw/xxxx.jpg --prompt "monster" --box-threshold 0.1

# 看到框對了再批次標，然後照常切分、訓練
python tools/label_gdino.py --prompt "monster" --box-threshold 0.15
python tools/prepare_dataset.py && python tools/train_yolo.py
```

**它會把 NPC 和其他玩家也當成怪**——對它來說都是「卡通人形」。
所以預設就開了負面 prompt，把這些框剔掉：

```bash
--reject "npc,person,player character,signboard"      # 預設值
```

`--test` 的預覽圖裡 **黃框 = 會拿去訓練，紅框 = 已剔除**，一眼就看得出
過濾對不對。NPC 還是漏網的話，把它的特徵加進去，例如：

```bash
python tools/label_gdino.py --test shot.jpg --prompt "monster" \
  --reject "npc,person,player character,shopkeeper,merchant,girl,boy,signboard"
```

反過來，如果**怪被誤剔**（黃框太少、紅框框到怪），把該詞從 `--reject` 拿掉，
或調高 `--iou-drop`（預設 0.4，調到 0.7 只剔除幾乎完全重疊的）。

> 用的是 **transformers 官方維護版**的 GroundingDINO，不是 autodistill 那包。
> autodistill 依賴的 `groundingdino` 套件已停止維護，在 transformers 5.x 會炸在
> `BertModel has no attribute 'get_head_mask'`——別浪費時間在那條路上。

`--test` 降到 0.1 還是沒框到就別勉強——回頭用模板老師，
楓谷 sprite 用模板反而更穩。

## 選配：人工精修（只有想再擠準度時才做）

自動標的框已經能直接訓練。真的想修（例如某隻怪一直漏抓），
先用 `tools/autolabel.py` 產生標籤再開 labelImg：

```bash
python tools/autolabel.py              # 只標註、不訓練，產生可校對的 .txt
pip install labelImg && labelImg datasets/raw datasets/raw/classes.txt
```

- 左側切到 **YOLO** 格式；`W` 畫框、`D` 下一張、開 Auto Save Mode
- 只改三種：漏框補上、誤框刪掉、框太鬆拉緊
- 改完 `python tools/prepare_dataset.py && python tools/train_yolo.py`

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

**先驗證連線**（在遊戲機上跑）：

```powershell
python tools/check_remote.py
```

會查 /health、連送 10 張圖量測往返延遲，並告訴你夠不夠快；連不上時會列出
檢查清單（伺服器沒開 / IP 錯 / 防火牆 / 不同網段）。

延遲概算（主迴圈每 tick 有 125ms 預算）：

| 環節 | 實測 |
|---|---|
| JPEG 編碼 + 傳輸（790x520 縮到 640 寬，約 89KB @ q80） | 區網 ~3ms；VPN 視頻寬而定 |
| 伺服器端 `model.predict()`（5090，含前後處理與 NMS） | **~4.5ms** |
| **合計往返** | 區網 **15~25ms** |

> 第一次請求會慢很多（20~30ms，CUDA 暖機），之後才穩定在 4.5ms。

實際跨機器量過的一組數字（Tailscale 直連、RTT 8ms、送 43KB）：
**往返 33.8ms**，其中推理 4.5ms、網路 RTT 9ms，其餘是編碼與協定開銷。

**慢的幾乎一定是網路，不是推理。** 實測參考：183KB 走 Tailscale 往返
145ms，其中推理只佔 4.5ms——延遲大致與封包大小成正比。三個對策依序試：

1. **`vision.mob_interval: 0.4`**——怪物偵測降頻到每 0.4 秒一次，
   沿用上次結果。主迴圈與 HP/走位/安全機制**仍然全速執行**，
   因為那些辨識在本機只要 ~1ms。楓谷的怪移動慢，這樣完全夠用。
2. 調降 `remote_jpeg_quality` 到 60（省約 30%）
3. 遊戲視窗調小，或改用區網 IP 而非 VPN 位址

**連線走 VPN（Tailscale 等）或 WiFi 時**，瓶頸通常是上傳頻寬而非推理。
客戶端已經做了兩件事來壓低成本：

- **連線重用**（HTTP keep-alive）：不用每張圖重新握手
- **送出前縮圖**：`remote_max_width: 640`。YOLO 內部本來就會縮到 640，
  所以這是零精度損失的頻寬節省

實測單張大小（790x520 的 playfield）：

| 設定 | 大小 |
|---|---|
| 原尺寸 q80 | 143 KB |
| 縮到 640 q80（**預設**） | 89 KB |
| 縮到 640 q60 | 62 KB |

還是太慢的話，依序試：`remote_jpeg_quality: 60` → 調低 `loop.fps`。

連不上或逾時時，客戶端會記錄警告並把該幀當成「沒看到怪」——bot 會繼續巡邏
而不是崩潰。

> Linux 工作站記得開防火牆埠：`sudo ufw allow 8100/tcp`

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
