import os
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


@pytest.fixture(autouse=True)
def isolated_ui_templates(tmp_path, monkeypatch):
    """把 UI 模板目錄指到空的暫存資料夾。

    預設值指向 data/templates/ui，那是**使用者自己**截的模板（角色名牌、
    小地圖角落）。開發機上一旦有那些檔案，測試就會拿真人的模板去比對合成
    畫面，結果隨誰的機器而異。測試要跑在乾淨的環境裡。
    """
    monkeypatch.setattr("maplebot.config.UI_TEMPLATES_DIR", str(tmp_path / "ui"))


@pytest.fixture(autouse=True)
def isolated_logs(tmp_path, monkeypatch):
    """把 bot 寫 logs/ 的地方指到暫存資料夾，測試不在 repo 裡留檔。

    這些路徑都相對於工作目錄，pytest 在 repo 根目錄跑，就寫進使用者自己的
    logs/。實測跑一次全套，logs/anomalies 多 11 張合成畫面（578／828 bytes，
    來自 test_runner_guards 的死亡與黑屏測試）。控制台「開啟異常畫面」開的
    就是這個資料夾，真正的異常畫面會被一次次的測試殘渣埋掉。

    doctor.check_writable 的預設值 "logs" 在定義時就綁死，改 log.LOG_DIR 擋
    不到，所以另外包一層換掉預設，明確給路徑的呼叫照舊。控制台的就緒檢查
    （Controller.refresh_readiness → doctor.run_all）會跑到它，在 repo 建出
    logs/，再寫入、刪掉一個探針檔。

    不用 tmp_path/logs：有幾個測試自己在那裡放 check.png、check.json 再檢查，
    分開放才不必顧慮兩邊撞在一起。
    """
    from maplebot.diagnostics import doctor

    logs = tmp_path / "isolated_logs"
    monkeypatch.setattr("maplebot.log.LOG_DIR", str(logs))
    monkeypatch.setattr("maplebot.safety.ANOMALY_DIR", str(logs / "anomalies"))
    real_check_writable = doctor.check_writable
    monkeypatch.setattr(doctor, "check_writable",
                        lambda path=str(logs): real_check_writable(path))


@pytest.fixture(scope="session")
def fixture_frame() -> np.ndarray:
    """MapleSaga 800x600 視窗實拍截圖：HP 100%、MP 100%、EXP 59.89%。"""
    path = os.path.join(FIXTURE_DIR, "mapleaga_800x600.jpg")
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    assert img is not None, f"讀不到測試截圖 {path}"
    return img
