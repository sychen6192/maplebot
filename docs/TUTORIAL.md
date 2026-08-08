# 手把手教學：從 clone 到掛機

照順序做完就能跑，**大約 15 分鐘**。每一步都寫了「預期結果」，對不上就看文末疑難排解。

> 前提：Windows 10/11、遊戲用**視窗模式**。
>
> **建議把遊戲視窗開在 800x600 左右**。大視窗（2560x1440 全螢幕）不是不能用，
> 但辨識比較慢、之後每個座標都要重校正。

> 好消息：怪物偵測是**零設定**的（靠 sprite 的黑色描邊找怪），
> 不用截模板、不用訓練、不用標註。巡邏點也可以讓它自己量。

## 第 0 步：安裝

```powershell
git clone https://github.com/sychen6192/maplebot.git
cd maplebot
pip install -r requirements.txt
pytest -q
```

**預期結果**：最後一行是 `xxx passed`。測試完全離線，不用開遊戲。

> 想用虛擬環境（推薦）就改用 [uv](https://docs.astral.sh/uv/)：
> ```powershell
> powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
> uv venv && .venv\Scripts\activate && uv pip install -r requirements.txt
> ```
> 之後每次開新終端機都要先 `.venv\Scripts\activate`。

## 第 1 步：告訴程式你的遊戲視窗叫什麼

開遊戲，看視窗最上方標題列的文字（例如 `MapleSaga`）。

建立 **`config/local.yaml`**——你的個人設定都放這裡，更新程式不會被蓋掉：

```yaml
window:
  title: "MapleSaga"     # 改成你的視窗標題，子字串即可
```

## 第 2 步：校正五個區域

```powershell
python tools/calibrate.py
```

依序跳出五個框選視窗，每個都是**滑鼠拖出矩形 → 按 Enter**（按 `c` 跳過）：

| 區域 | 框什麼 |
|---|---|
| `minimap` | 小地圖的**地圖畫布**本體（不含標題列） |
| `hp_bar` | 紅色 HP 條本體（不含 `HP[...]` 文字） |
| `mp_bar` | 藍色 MP 條本體 |
| `exp_bar` | 經驗條（**建議框**，健康檢查會用到） |
| `playfield` | 主遊戲畫面（不含底部 UI 列） |

**預期結果**：終端機印出一段 `regions:`，整段貼進 `config/local.yaml`。

## 第 3 步：驗證辨識（最重要的一步）

```powershell
python tools/debug_view.py --snapshot check.png
```

不開視窗，只存一張標註圖。終端機會印：

```
擷取尺寸: 800x600｜擷取方式: printwindow
playfield ROI: [8, 60, 790, 520]
  HP 100% | MP 100% | EXP 59%
  玩家小地圖座標: (36, 45)｜其他玩家: 0
  偵測到怪物: 3
  當下決策: Attack
```

打開 `check.png` 檢查四件事：

1. 小地圖上你的位置有**綠圈**
2. `HP xx% | MP xx% | EXP xx%` 跟遊戲畫面一致
3. 場上的怪有**黃框**
4. 沒有把樹、雲、UI 框成怪

**抓不到怪或框錯**，只要調兩個旋鈕（`config/local.yaml`）：

```yaml
vision:
  outline_black_level: 8    # 抓不到 → 調高到 12~20；框到一堆背景 → 調低到 4
  outline_min_area: 300     # 框到小碎塊 → 調高；小怪被漏掉 → 調低
```

> 也可以用 `python tools/debug_view.py` 開即時視窗（按 q 離開），
> 但如果擷取方式是 `screen`，視窗蓋住遊戲會拍到自己，用 `--snapshot` 最保險。

## 第 4 步：寫你的地圖 profile

```powershell
copy config\profiles\example.yaml config\profiles\mymap.yaml
```

打開 `mymap.yaml`，最少改這幾個地方：

```yaml
patrol:
  waypoints: auto           # ← 懶人寫法：開場自己左右走到撞牆，量出巡邏範圍

loot:
  key: z                    # 撿取鍵（不想撿就留空）

attack:
  key: x                    # 你的攻擊技能鍵
  type: directional         # 原地放的技能（祭司 Heal 之類）改成 aoe

potions:
  hp: { key: pageup,   below_ratio: 0.50 }
  mp: { key: pagedown, below_ratio: 0.30 }

buffs:
  - { key: "8", every: 120, cast_seconds: 1.5 }   # 沒 buff 就整段刪掉
```

**`waypoints: auto` 是關鍵**——它會在開場自己往左右走到撞牆，量出可走範圍，
你不用先去量座標。單層練功圖用這個就夠了。

<details>
<summary>想自己指定巡邏點 / 多技能 / 多層地圖（點開）</summary>

**手動巡邏點**：先讀座標

```powershell
python tools/debug_view.py --track
```

一邊玩一邊看終端機印出的 `x=`，走到左端點記一次、右端點記一次：

```yaml
patrol:
  waypoints: [36, 91]
```

**多技能輪替**（大絕 + 主攻）：

```yaml
skills:
  - key: v                # 範圍大絕：優先但條件嚴格
    type: aoe
    cooldown: 30
    min_mobs: 3           # 至少 3 隻才放，不浪費在單隻怪身上
    min_mp: 0.2
    range_px: 400
  - key: x                # 主攻：保底
    type: directional
    cooldown: 0.2
    range_px: 320
```

有 `skills:` 時 `attack:` 會被忽略。

**多層地圖**（要爬繩子）：見 `config/profiles/multilevel.yaml`，
用 `{x: 68, y: 20}` 指定「x 對準後爬到 y」。繩子的座標要自己用 `--track` 量。
</details>

## 第 5 步：先預演（不會按任何鍵）

```powershell
python main.py --profile config/profiles/mymap.yaml --dry-run
```

**預期結果**：每行 log 的決策要合理——

```
tick 3 | HP 100% MP 100% | 玩家 (36, 45) | 怪 2 | -> Attack
tick 4 | HP 100% MP 100% | 玩家 (36, 45) | 怪 0 | -> Loot
tick 5 | HP 100% MP 100% | 玩家 (40, 45) | 怪 0 | -> Move
```

看個一分鐘沒問題就 `Ctrl+C`。

## 第 6 步：正式執行

```powershell
python main.py --profile config/profiles/mymap.yaml
```

啟動後**點一下遊戲視窗**讓它保持在前景。

| 熱鍵 / 情況 | 行為 |
|---|---|
| `F9` | 暫停 / 繼續 |
| `F12` | 緊急停止 |
| HP 低於 25% | 自動停機 + 長嗶 + 截圖（可設 `panic_return_key` 先回城） |
| 小地圖出現其他玩家 | 暫停動作 + 短嗶，人走了自動繼續 |
| 黑屏 / 找不到角色 | 自動暫停 + 嗶聲 + 截圖到 `logs/anomalies/` |
| 卡在地形 | 自動跳一下脫困 |
| **10 分鐘沒賺到經驗** | 自動暫停 + 警報（代表技能鍵設錯之類的問題） |

每 60 秒會印一次進度：

```
狀態：運行 12.3 分鐘 | tick 5904 | 攻擊 412 次 | 撿物 88 次 | 脫困 2 次
進度：EXP 63.4%｜累積 +1.32 等｜約 1.18 等/小時｜升級 1 次
```

完整紀錄在 `logs/run_*.log`。

## 選配

- **接你的 Ollama 督導層**：`config/local.yaml` 加
  `advisor: { enabled: true, model: "qwen3-vl:8b" }`（見 README）
- **升級 YOLO 怪物偵測**：outline 夠用就不用碰。想升級見 [YOLO_TRAINING.md](YOLO_TRAINING.md)

## 疑難排解

| 症狀 | 解法 |
|---|---|
| 抓不到怪 / 框到背景 | 調 `outline_black_level`（8→15 抓更多、8→4 抓更少）與 `outline_min_area` |
| 大視窗很卡 | `config/local.yaml` 加 `vision: { mob_search_box: [700, 400] }`，只搜角色周圍 |
| 按鍵沒反應 | 終端機用**系統管理員**開；確認遊戲視窗在前景 |
| `找不到標題含「...」的視窗` | `window.title` 打錯；要用視窗模式 |
| 畫面像無限鏡像一直放大 | 擷取方式退回 `screen` 且偵錯視窗蓋住遊戲。用 `--snapshot` 就不會 |
| 玩家綠圈不見/亂跳 | 小地圖框太大含到雜物 → 重框；或調 `vision.color_tolerance` |
| HP/MP % 不對 | 條的框含到文字/外框 → 重框，只框色條本體 |
| 自動巡邏報「校正失敗」 | 方向鍵沒送進遊戲（權限問題），或小地圖 ROI 錯了 |
| 一直說沒賺到經驗 | 技能鍵對嗎？怪打得到嗎？先用 `--dry-run` 看決策是不是一直 `Move` |
| 座標整組偏移 | Windows 顯示縮放改過 → 重跑第 2 步校正 |
