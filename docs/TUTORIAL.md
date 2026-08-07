# 手把手教學：從 clone 到掛機

照順序做完就能跑。每一步都寫了「預期結果」，對不上就看最後的疑難排解。

> 前提：Windows 10/11、遊戲用**視窗模式**（建議 800x600）。
> 遊戲若以系統管理員身分執行，下面所有指令的終端機（PowerShell）也要
> 「以系統管理員身分執行」，否則按鍵送不進遊戲。

## 第 0 步：安裝

```powershell
git clone https://github.com/sychen6192/maplebot.git
cd maplebot
pip install -r requirements.txt
pytest -q
```

**預期結果**：最後一行 `67 passed`。這代表環境沒問題（測試完全離線，不用開遊戲）。

## 第 1 步：告訴程式你的遊戲視窗叫什麼

開遊戲，看視窗最上方標題列的文字（例如 `MapleSaga`、`MapleStory Worlds`）。

建立 **`config/local.yaml`**（個人設定放這裡，之後更新程式不會被蓋掉）：

```yaml
window:
  title: "MapleSaga"     # 改成你的視窗標題，子字串即可
```

## 第 2 步：校正五個區域

```powershell
python tools/calibrate.py
```

會依序跳出五個框選視窗，每個：**滑鼠拖出矩形 → 按 Enter 確認**（按 `c` 跳過）：

| 區域 | 框什麼 |
|---|---|
| `minimap` | 小地圖的**地圖畫布**本體（不含標題列） |
| `hp_bar` | 紅色 HP 條本體（不含 `HP[...]` 文字） |
| `mp_bar` | 藍色 MP 條本體 |
| `exp_bar` | 經驗條（可跳過） |
| `playfield` | 主遊戲畫面（不含底部 UI 列） |

**預期結果**：終端機印出一段 `regions:`，整段貼進 `config/local.yaml`。

## 第 3 步：截怪物模板

站到有怪的地方：

```powershell
python tools/grab_template.py --name snail    # 名字自取
```

框住怪物本體按 Enter（框緊一點，不要含大片背景），同一隻怪**不同動作幀截 2~3 張**，按 `c` 結束。

**預期結果**：`data/templates/mobs/` 出現 `snail_01.png`、`snail_02.png`…（左右翻轉會自動處理）。

## 第 4 步：驗證辨識（最重要的一步）

```powershell
python tools/debug_view.py
```

> 如果視窗一直遞迴疊圖（擷取方式是 `screen` 且視窗擠不開），改用不開視窗的
> 快照模式，看存出來的 PNG 即可：
> `python tools/debug_view.py --snapshot check.png`

檢查四件事：

1. 小地圖上你的位置有**綠圈**，旁邊顯示座標 `(x,y)`
2. 上方 `HP xx% | MP xx%` 與遊戲顯示一致
3. 場上的怪有**黃框**
4. 右上角顯示目前決策（`-> Move`、`-> Attack`…）

**順便做**：走到你想巡邏的左端點，記下綠圈座標的 x；再走到右端點記一次。這兩個數字下一步要用。

按 `q` 離開。有問題先跳到文末疑難排解調參數，調到四項都對再往下。

## 第 5 步：寫你的地圖 profile

```powershell
copy config\profiles\example.yaml config\profiles\mymap.yaml
```

打開 `mymap.yaml`，把這幾個欄位改成你的：

```yaml
patrol:
  waypoints: [35, 90]       # 第 4 步記下的左右端點 x
attack:
  key: x                    # 你的攻擊技能鍵
  type: directional         # 需要面向的技能；祭司 Heal 這類原地放的改 aoe
potions:
  hp: { key: pageup,   below_ratio: 0.50 }   # 你的 HP 藥水鍵
  mp: { key: pagedown, below_ratio: 0.30 }
buffs:
  - { key: "8", every: 120, cast_seconds: 1.5 }   # 沒有 buff 就整段刪掉
```

## 第 6 步：先預演（不會按任何鍵）

```powershell
python main.py --profile config/profiles/mymap.yaml --dry-run
```

**預期結果**：每行 log 像這樣，決策要合理（有怪 → Attack、沒怪 → Move、有人 → Wait）：

```
tick 3 | HP 100% MP 100% | 玩家 (36, 45) | 怪 2 | -> Attack
```

看個一分鐘沒問題就 `Ctrl+C` 結束。

## 第 7 步：正式執行

```powershell
python main.py --profile config/profiles/mymap.yaml
```

啟動後**點一下遊戲視窗**讓它保持在前景（按鍵送給前景視窗）。

| 情況 | 行為 |
|---|---|
| `F9` | 暫停 / 繼續 |
| `F12` | 緊急停止 |
| HP 低於 25% | 自動停機 + 長嗶 + 截圖（可設 `panic_return_key` 先回城） |
| 小地圖出現其他玩家 | 暫停動作 + 短嗶，人走了自動繼續 |
| 黑屏 / 找不到角色 | 自動暫停 + 嗶聲 + 截圖存到 `logs/anomalies/` |
| 卡在地形 | 自動跳一下脫困 |

完整紀錄在 `logs/run_*.log`，每 60 秒印一次統計（攻擊/喝藥/脫困次數）。

## 第 8 步（選配）：接上你的 Ollama 督導層

`config/local.yaml` 加：

```yaml
advisor:
  enabled: true
  model: "qwen3-vl:8b"      # 或你已有的 qwen3.6:35b-a3b（見 README 共存原則）
```

**預期結果**：啟動時 log 出現 `VLM 督導層已啟動`。它每 20 秒看一次畫面，發現對話框/驗證視窗/斷線就暫停 + 嗶聲。

## 第 9 步（選配）：升級 YOLO 怪物偵測

模板匹配夠用就不用急。想升級（更穩、毫秒級）照著 [YOLO_TRAINING.md](YOLO_TRAINING.md) 做，5090 上訓練只要幾分鐘。

## 疑難排解

| 症狀 | 解法 |
|---|---|
| 畫面像無限鏡像一直放大／視窗一層層疊 | 擷取方式是 `screen` 且偵錯視窗蓋在遊戲上，拍到自己。程式會自動挪開視窗；螢幕不夠大就用 `--snapshot out.png`（不開視窗）|
| 按鍵沒反應 | 終端機用系統管理員開；確認遊戲視窗在前景 |
| `找不到標題含「...」的視窗` | `window.title` 打錯；視窗模式才抓得到 |
| 玩家綠圈不見/亂跳 | 小地圖框太大含到雜物 → 重框；或調 `vision.color_tolerance`（黃點偏色）、`max_dot_pixels`（黃色地形干擾）。最穩：截一張玩家點模板存成 `data/templates/ui/minimap_player.png` |
| HP/MP % 不對 | 條的框含到文字/外框 → 重框，只框色條本體 |
| 怪物黃框抓不到 | 多截幾張不同動作幀模板；`mob_match_threshold` 降到 0.65 試 |
| 怪物誤框背景 | `mob_match_threshold` 調高到 0.78+；模板重截框緊一點 |
| 換地圖/小地圖被拖動就失效 | 進階：截小地圖角落模板改用 `minimap: auto`（見 default.yaml 註解） |
| 座標整組偏移 | Windows 顯示縮放改過 → 重跑第 2 步校正 |
