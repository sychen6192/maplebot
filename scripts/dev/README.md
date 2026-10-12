# scripts/dev：開發用的提權執行器

**玩家用不到這個資料夾。** 遊戲用系統管理員執行時，照
[TUTORIAL](../../docs/TUTORIAL.md) 開頭講的，從「以系統管理員身分執行」開的終端機
`cd` 到專案資料夾、跑 `python gui.py`（或 `main.py`）就好，不需要這裡的任何東西。

## 這是什麼

`admin_worker.bat` + `admin_worker.ps1` 是給開發用的：讓一個**沒有提權**的程式
（例如在終端機裡幫忙測試的 AI 助手，或自動化測試腳本）也能在遊戲以系統管理員
執行時驅動按鍵測試（`tools/test_keys.py`）和 bot 實跑。

會需要它，是因為遊戲提權之後，Windows 的 UIPI 會默默丟掉一般權限行程送出的
按鍵：`SendInput` 回報成功、遊戲卻收不到。測試的人自己沒提權，送出的鍵就全被
擋掉，所以改成把指令丟給一個已經提權的視窗代跑。

運作方式（`admin_worker.ps1`）：

- 佇列放在 `%TEMP%\maplebot_worker`（用 `-QueueDir` 可以改）。刻意不放在 repo 或
  某次 session 的暫存資料夾裡，換一次 session 路徑就變了。
- 每 0.8 秒看一次有沒有 `cmd.ps1`。有的話改名成 `running_<時分秒>.ps1`，**用系統
  管理員權限執行**，全部輸出寫進 `out.txt`（每次覆蓋），跑完寫 `done.txt`。
- 每一輪都更新 `heartbeat.txt`，送指令的那一方可以拿它確認視窗還開著。
- 指令的工作目錄是 repo 根目錄（`.ps1` 在 `scripts/dev/`，所以往上兩層），
  `python tools/test_keys.py`、`python -m pytest` 這種相對路徑照常能用。

## 怎麼開、怎麼關

- **開**：雙擊 `scripts\dev\admin_worker.bat`，UAC 問的時候按「是」。會開出一個
  PowerShell 視窗，顯示 `=== maplebot elevated worker ONLINE ===` 和 `admin: True`。
  `admin: False` 表示沒拿到提權，送進去的按鍵一樣會被擋。
- **關**：直接關掉那個 PowerShell 視窗。沒有其他停止方式，也沒有逾時自己結束。

## 信任模型：視窗開著的時候，等於把管理員權限開放出去

這個視窗**不驗證任何東西**：不檢查是誰寫的 `cmd.ps1`，沒有簽章、也沒有白名單，
看到檔案就用管理員權限執行。`%TEMP%` 是你自己的使用者資料夾，所以：

**視窗開著的這段時間，任何能寫入 `%TEMP%\maplebot_worker\cmd.ps1` 的程式，
都能拿到系統管理員權限**——包括你用一般權限跑的任何程式、下載下來執行的腳本。
等於這段時間 UAC 對你這個帳號形同關掉。

所以：

- 用完就關，不要整晚開著、不要人離開電腦還開著。
- 不要把它設成開機自動啟動或排程工作。
- 不要用 `-QueueDir` 指到別的帳號也寫得進去的資料夾。

## 為什麼這兩支檔案只用英文

Windows PowerShell 5.1 會用系統字碼頁（繁中 Windows 是 cp950）讀沒有 BOM 的
`.ps1`，`cmd` 讀 `.bat` 也是用主控台字碼頁。用 UTF-8 寫的中文註解在那裡會被讀成
亂碼，嚴重時連帶弄壞下一行，所以 `admin_worker.*` 的註解和訊息刻意維持 ASCII，
說明都寫在這份 README。
