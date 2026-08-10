"""收工報告：組資料、渲染 Markdown、寫檔。

報告是「跑完之後才會執行一次」的程式碼——正是最容易帶著錯誤上線的那種。
所以這裡連 f-string 排版都測，不然真的壞掉時人已經睡了。
"""
import json
import time

from maplebot import report
from maplebot.alerts import Alerts
from maplebot.config import MonitorCfg
from maplebot.executor import Stats
from maplebot.metrics import LoopMetrics, Series
from maplebot.progress import ExpTracker
from maplebot.sysmon import SystemMonitor


def _pieces(duration=3600.0):
    stats = Stats(ticks=1000, attacks=420, buffs=12, potions_hp=8, potions_mp=3,
                  loots=30, climbs=5, escapes=2)
    exp = ExpTracker()
    exp.update(0.10, 0.0)
    exp.update(0.60, duration)
    metrics = LoopMetrics(8.0)
    metrics.record("perceive", 0.030)
    metrics.record("execute", 0.400)
    metrics.tick(0.430, 0.030)
    alerts = Alerts(enabled=False)
    sysmon = SystemMonitor(MonitorCfg(enabled=False))
    series = Series(interval=1.0)
    for i in range(5):
        series.maybe_add(float(i), 0.0, hp=1.0 - i * 0.1, mp=0.5, exp=0.1 * i, mobs=i)
    return stats, exp, metrics, alerts, sysmon, series


def _build(**kw):
    stats, exp, metrics, alerts, sysmon, series = _pieces()
    now = kw.pop("now", 3600.0)
    return report.build("測試地圖", 1_700_000_000.0, 1_700_003_600.0, now,
                        stats, exp, metrics, alerts, sysmon, series, **kw)


def test_build_summarises_the_session():
    data = _build()
    assert data["profile"] == "測試地圖"
    assert data["duration_seconds"] == 3600.0
    assert data["actions"]["attacks"] == 420
    assert data["progress"]["gained_levels"] == 0.5


def test_exp_rate_uses_the_monotonic_clock():
    """牆上時鐘給人看日期，monotonic 給效率算分母。兩者混用會算出 1970 年
    到現在的時數，效率變成 0。"""
    data = _build(now=3600.0)
    assert data["progress"]["levels_per_hour"] == 0.5


def test_attacks_per_minute_is_none_for_short_runs():
    """跑 20 秒就外推「每分鐘幾次」只會得到一個騙人的數字。"""
    stats, exp, metrics, alerts, sysmon, series = _pieces()
    data = report.build("短", 1_700_000_000.0, 1_700_000_020.0, 20.0,
                        stats, exp, metrics, alerts, sysmon, series)
    assert data["actions"]["attacks_per_min"] is None


def test_alerts_are_carried_into_the_report():
    stats, exp, metrics, alerts, sysmon, series = _pieces()
    alerts.ping("warn", "血條讀值閃了一下")
    alerts.ping("panic", "HP 低於危險線")
    data = report.build("x", 0.0, 60.0, 60.0, stats, exp, metrics, alerts,
                        sysmon, series)
    assert data["alerts"]["counts"] == {"warn": 1, "panic": 1}
    assert "血條" in data["alerts"]["records"][0]["message"]


def test_report_is_json_serialisable():
    json.dumps(_build())


def test_markdown_renders_every_section():
    md = report.render_markdown(_build())
    for heading in ("# 掛機報告", "## 收穫", "## 效能", "## 系統", "## 警報"):
        assert heading in md


def test_markdown_shows_a_dash_when_there_is_no_exp_rate():
    stats, exp, metrics, alerts, sysmon, series = _pieces()
    fresh = ExpTracker()                       # 完全沒讀到 EXP
    data = report.build("x", 0.0, 60.0, 60.0, stats, fresh, metrics, alerts,
                        sysmon, series)
    assert "| 效率 | — |" in report.render_markdown(data)


def test_markdown_flags_dry_run():
    assert "dry-run" in report.render_markdown(_build(dry_run=True))


def test_markdown_states_the_stop_reason():
    md = report.render_markdown(_build(stop_reason="遊戲行程結束"))
    assert "遊戲行程結束" in md


def test_markdown_tells_you_how_to_get_process_stats():
    """psutil 沒裝時要講「裝了會多什麼」，不是只印個空欄位。"""
    data = _build()
    data["system"] = {"available": False, "psutil": False, "game_lost": False}
    assert "pip install psutil" in report.render_markdown(data)


def test_markdown_does_not_nag_when_psutil_is_present():
    """psutil 有裝、只是使用者把 monitor 關掉——這時叫他去裝 psutil 是雜訊。"""
    data = _build()
    data["system"] = {"available": False, "psutil": True, "game_lost": False}
    assert "pip install psutil" not in report.render_markdown(data)


def test_save_writes_json_and_markdown(tmp_path):
    paths = report.save(_build(), str(tmp_path), chart=False)
    assert len(paths) == 2
    assert json.loads(open(paths[0], encoding="utf-8").read())["profile"] == "測試地圖"
    assert open(paths[1], encoding="utf-8").read().startswith("# 掛機報告")


def test_chart_is_skipped_rather_than_crashing_without_data(tmp_path):
    data = _build()
    data["series"] = []
    assert report.write_chart(data, str(tmp_path / "x.png")) is None


def test_chart_renders_when_matplotlib_is_available(tmp_path, recwarn):
    import pytest
    pytest.importorskip("matplotlib")
    out = report.write_chart(_build(), str(tmp_path / "chart.png"))
    assert out is not None
    assert (tmp_path / "chart.png").stat().st_size > 1000
    # profile 名字是中文，但 matplotlib 預設字型沒有中文字符——標題若直接
    # 帶中文，圖上會是一排豆腐框，而 matplotlib 只會發 warning 不會失敗
    assert not [w for w in recwarn if "missing from font" in str(w.message)]


def test_chart_title_strips_non_ascii():
    assert report._ascii_only("測試地圖 map-3") == "map-3"
    assert report._ascii_only("純中文") == "profile"


def test_duration_formatting():
    assert report._fmt_hms(0) == "0:00:00"
    assert report._fmt_hms(3661) == "1:01:01"
    assert report._fmt_hms(-5) == "0:00:00"


def test_filenames_are_sortable_by_time(tmp_path):
    a = report.save(_build(), str(tmp_path), chart=False)[0]
    time.sleep(1.05)          # 檔名精度到秒，要真的跨一秒才驗得到
    b = report.save(_build(), str(tmp_path), chart=False)[0]
    assert a < b
