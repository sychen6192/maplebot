"""執行卡、儀表、健康數據列與日誌的 view-model（maplebot/gui/live.py）。

全部拿手捏的 Status 餵進去，不開 bot、不開視窗：凌晨三點停機、早上七點
回來看到什麼，這裡就能定下來。
"""
import logging
import typing

import pytest

from maplebot.brain import fsm
from maplebot.diagnostics import doctor
from maplebot.gui import live, settings
from maplebot.gui.live import Context, Limits, LogLine, Monitor
from maplebot.runner import Status

LIM = Limits(hp_potion=0.5, mp_potion=0.3, critical=0.25)
ALIVE = Context(thread_alive=True)


def _running(**kw) -> Status:
    base = dict(running=True, preflight="ok", started_at=100.0, ticks=10,
                action="Move", hp=0.82, mp=0.45, exp=0.591, player=(95, 11),
                target_fps=8.0, fps=7.9)
    base.update(kw)
    return Status(**base)


def _view(st, ctx=ALIVE, now=200.0, lim=LIM):
    return Monitor().view(st, ctx, lim, now)


# ---- 待命、啟動中 ----

def test_idle_card_offers_to_start():
    v = _view(Status(), Context())
    assert (v.phase, v.state, v.tone) == ("idle", "待命", "muted")
    assert v.pill == "● 待命"
    assert v.primary == ("start", "▶ 開始打怪", True)
    assert v.can_stop is False and v.settings_locked is False
    assert "F9 暫停/繼續、F12 停止" in v.detail
    assert [m.text for m in v.meters] == ["—", "—", "—"]
    v = _view(Status(), Context(dry_choice=True))
    assert v.primary == ("start", "▶ 開始預演", True) and v.dry_tag


def test_starting_until_the_preflight_passes():
    v = _view(Status(preflight="checking"))
    assert (v.phase, v.state, v.tone) == ("starting", "啟動中", "info")
    assert v.primary == ("start", "啟動中…", False)
    assert v.can_stop and v.settings_locked
    assert "執行中" in v.lock_text
    # 自檢過了、主迴圈還沒開始（帶回前景、啟動背景執行緒）也還是啟動中
    assert _view(Status(preflight="ok")).phase == "starting"


# ---- 執行中、讓過中、畫面沒更新 ----

def test_running_shows_the_decision():
    v = _view(_running(action="Attack"))
    assert (v.phase, v.state, v.tone) == ("running", "執行中", "ok")
    assert v.detail == "決策：攻擊"
    assert v.primary == ("pause", "‖ 暫停", True)
    assert v.frozen == "" and v.meters[0].tone == "ok"


def test_a_long_wait_turns_into_waiting():
    m = Monitor()
    reason = "小地圖出現 1 位其他玩家，暫停動作"
    st = _running(action="Wait", reason=reason, ticks=1)
    assert m.view(st, ALIVE, LIM, 0.0).phase == "running"
    v = m.view(_running(action="Wait", reason=reason, ticks=2), ALIVE, LIM, 1.0)
    assert v.state == "執行中"                         # 1 秒的讓過不閃
    v = m.view(_running(action="Wait", reason=reason, ticks=3), ALIVE, LIM, 3.5)
    assert (v.phase, v.state, v.tone) == ("waiting", "讓過中", "warn")
    assert v.detail == f"{reason}（已 3 秒）"
    v = m.view(_running(action="Move", ticks=4), ALIVE, LIM, 3.7)
    assert v.phase == "running"


def test_frozen_ticks_turn_into_stale_until_a_tick_arrives():
    m = Monitor()
    assert m.view(_running(ticks=10), ALIVE, LIM, 0.0).phase == "running"
    v = m.view(_running(ticks=10), ALIVE, LIM, 6.0)
    assert (v.phase, v.state, v.tone) == ("stale", "畫面沒更新", "warn")
    assert v.detail.startswith("已 6 秒沒有新的 tick")
    assert m.view(_running(ticks=11), ALIVE, LIM, 6.2).phase == "running"


def test_a_long_pause_is_not_mistaken_for_stale():
    m = Monitor()
    m.view(_running(ticks=10), ALIVE, LIM, 0.0)
    m.view(_running(ticks=10, paused=True, pause_reason="x", paused_by_user=True),
           ALIVE, LIM, 3600.0)
    assert m.view(_running(ticks=10), ALIVE, LIM, 3601.0).phase == "running"


def test_elapsed_and_remaining_time():
    lim = Limits(max_runtime=120 * 60)
    v = _view(_running(started_at=100.0), now=100.0 + 5025, lim=lim)
    assert v.run_time == "1:23:45"
    assert v.elapsed == "已跑 1:23:45・剩 36:15"


# ---- 暫停 ----

def test_a_user_pause_offers_resume():
    v = _view(_running(paused=True, paused_by_user=True, pause_reason="你按了 F9"))
    assert (v.phase, v.state, v.tone) == ("paused_user", "已暫停", "warn")
    assert v.primary == ("resume", "▶ 繼續", True)
    assert v.detail == "你按了 F9。按 ▶ 繼續 或 F9"
    assert v.action is None


def test_a_watchdog_pause_says_why_and_freezes_the_numbers():
    reason = "連續 10 分鐘沒有經驗進帳"
    v = _view(_running(paused=True, pause_reason=reason))
    assert (v.phase, v.state, v.tone) == ("paused_auto", "自動暫停", "bad")
    assert reason in v.detail
    assert v.action == ("anomalies", "開啟異常畫面")
    assert v.frozen == "（暫停前）"
    assert all(m.tone == "muted" for m in v.meters)
    assert all(tone == "muted" for _text, tone in v.lines)
    assert v.meters[0].text == "82%"                   # 數值留著，只是灰掉


# ---- 收工 ----

@pytest.mark.parametrize("reason, state, tone, detail, action", [
    ("PANIC：HP 見底", "已停機", "bad", "HP 見底", "anomalies"),
    ("達到最長運行時間（120 分鐘）", "已收工", "ok", "達到最長運行時間（120 分鐘）", None),
    ("你按了 F12", "已停止", "muted", "你按了 F12", None),
    ("你在控制台按了停止", "已停止", "muted", "你在控制台按了停止", None),
    ("遊戲行程結束", "已停機", "bad", "遊戲行程結束（當掉、被關掉或被踢下線）", None),
])
def test_how_a_run_ended(reason, state, tone, detail, action):
    st = _running(running=False, stop_reason=reason, ended_at=100.0 + 5025,
                  exp_gained=0.042, report_path="/abs/report.md")
    v = _view(st, Context())
    assert (v.phase, v.state, v.tone, v.detail) == ("ended", state, tone, detail)
    assert (v.action[0] if v.action else None) == action
    assert v.summary == "這一場 1:23:45・經驗 +4.2%"
    assert v.report == "/abs/report.md"
    assert v.frozen == "（停止時）"
    assert v.primary == ("start", "▶ 開始打怪", True)


def test_a_crash_is_shown_until_the_next_start():
    v = _view(_running(running=False, ended_at=150.0),
              Context(crash="ZeroDivisionError: division by zero"))
    assert (v.phase, v.state, v.tone) == ("crashed", "程式出錯", "bad")
    assert v.detail == "ZeroDivisionError: division by zero。詳細內容在日誌"


def test_a_failed_preflight_names_the_fix():
    err = ("開場就讀到 HP 0%——角色真的沒血你也不會現在開 bot，所以幾乎確定是 "
           "regions.hp_bar 框錯了。再重跑 tools/calibrate.py 只框紅色血條本體")
    v = _view(Status(preflight="fail", started_at=1.0, ended_at=2.0),
              Context(last_error=err))
    assert (v.phase, v.state, v.tone) == ("not_started", "沒開始", "bad")
    assert v.detail == "開場自檢沒過：開場就讀到 HP 0%"
    assert v.action == ("calibrate", "去校正 →")
    assert v.summary == ""                             # 沒跑起來，不算一場


def test_a_tool_run_hides_the_previous_ending():
    st = _running(running=False, stop_reason="你按了 F12", ended_at=150.0)
    assert _view(st, Context(show_run=False)).phase == "idle"


def test_a_settings_file_that_cannot_load():
    v = _view(Status(), Context(loaded=False, load_path="C:\\maps\\mine.yaml",
                                load_error="設定檔 mine.yaml 格式錯誤: while parsing\nline 3"))
    assert (v.phase, v.state, v.tone) == ("not_loaded", "設定讀不到", "bad")
    assert v.detail == "mine.yaml：設定檔 mine.yaml 格式錯誤: while parsing。修好之前不能儲存"
    assert v.action == ("notepad", "用記事本開啟")
    assert v.primary[2] is False


# ---- 工具與錄製 ----

def test_tools_and_recording_lock_the_settings():
    v = _view(Status(), Context(tool_label="校正"))
    assert (v.phase, v.state, v.tone) == ("tool", "校正中…", "tool")
    assert v.settings_locked and v.primary[2] is False and not v.can_stop
    assert v.lock_text == "！校正中：等它的視窗關掉再改設定"
    v = _view(Status(), Context(recording=True, record_info=(42.4, 300, 330)))
    assert (v.phase, v.state, v.tone) == ("recording", "錄製中", "info")
    assert "（42 秒、300/330 幀看得到角色）" in v.detail


def test_a_failed_or_cancelled_tool_offers_a_retry():
    v = _view(Status(), Context(tool_failed=("辨識自檢", "OSError: 寫不進 logs/check.png")))
    assert v.phase == "failed" and v.tone == "bad"
    assert v.detail == "辨識自檢沒有完成：OSError: 寫不進 logs/check.png"
    assert v.action == ("vision", "再試一次")
    v = _view(Status(), Context(tool_cancelled="截角色名牌"))
    assert v.phase == "cancelled"
    assert v.detail == "沒有存到名牌（框選視窗直接按了 c 或被關掉）"
    assert v.action == ("nametag", "再截一次")


# ---- 儀表與數據列 ----

def test_meter_tones_follow_the_potion_and_panic_lines():
    lim = Limits(hp_potion=0.5, critical=0.25)
    assert live.meter(0.2, "hp", lim).tone == "bad"
    assert live.meter(0.25, "hp", lim).tone == "bad"
    assert live.meter(0.4, "hp", lim).tone == "warn"
    assert live.meter(0.8, "hp", lim).tone == "ok"
    assert live.meter(0.8, "hp", lim).marks == ((0.5, "potion"), (0.25, "critical"))
    assert live.meter(None, "hp", lim).text == "讀不到"
    assert live.meter(None, "hp", lim).ratio is None
    assert live.meter(0.2, "mp", Limits(mp_potion=0.3)).tone == "warn"
    assert live.meter(0.2, "mp", Limits()).marks == ()
    exp = live.meter(0.591304, "exp", lim)
    assert (exp.text, exp.tone) == ("59.1%", "plain")


def test_limits_come_from_the_config_and_profile():
    from maplebot.config import AppCfg, PotionCfg, Profile
    cfg, prof = AppCfg(), Profile()
    prof.potions["hp"] = PotionCfg(key="pageup", below_ratio=0.5)
    prof.potions["mp"] = PotionCfg(key="", below_ratio=0.3)   # 沒綁鍵 = 不會喝
    lim = live.limits_from(cfg, prof)
    assert (lim.hp_potion, lim.mp_potion) == (0.5, 0.0)
    assert lim.critical == cfg.safety.critical_hp_ratio
    assert (lim.pause_key, lim.stop_key) == ("F9", "F12")
    assert live.limits_from(None, None) == Limits()


def test_health_lines():
    st = _running(mobs=3, others=0, followers=1, exp_gained=0.042, exp_per_hour=0.031,
                  game_mem_mb=812.4, ticks=5791)
    assert live.scene_line(st) == ("角色 (95, 11)・怪物 3・其他玩家 0・跟隨物 1", "plain")
    assert live.scene_line(_running(others=1))[1] == "warn"
    assert live.scene_line(_running(player=None))[0].startswith("找不到角色黃點")
    assert live.exp_line(st) == ("經驗 +4.2%・每小時約 +3.1%", "plain")
    assert live.exp_line(Status()) == ("經驗 —", "muted")
    assert live.perf_line(st) == ("tick 5791・7.9 / 8 FPS・遊戲 812 MB", "ok")
    assert live.perf_line(_running(fps=5.0))[1] == "warn"
    assert live.perf_line(_running(fps=3.0))[1] == "bad"
    assert live.perf_line(Status(target_fps=8.0)) == ("tick 0・-- / 8 FPS", "muted")
    assert live.perf_line(Status()) == ("tick 0・-- FPS", "muted")    # 還沒有 runner


def test_duration():
    assert live.duration(42) == "0:42"
    assert live.duration(5025) == "1:23:45"
    assert live.duration(-3) == "0:00"


def test_end_summary_needs_a_whole_run():
    assert live.end_summary(Status()) == ""
    st = Status(started_at=10.0, ended_at=52.0)
    assert live.end_summary(st) == "這一場 0:42・經驗 —"


# ---- 用詞 ----

def test_every_fsm_action_has_a_label():
    names = {cls.__name__ for cls in typing.get_args(fsm.Action)}
    assert names <= set(live.ACTION_LABELS)
    assert live.action_text("Wait", "x") == "讓過（x）"
    assert live.action_text("Attack") == "攻擊"
    assert live.action_text("-") == "—"


@pytest.mark.parametrize("text, action", [
    ("開場就讀到 HP 0%——…再重跑 tools/calibrate.py 只框紅色血條本體。"
     "已存一張畫面到 logs/anomalies/ 方便對照", "calibrate"),
    ("讀不到 HP：regions.hp_bar 超出畫面範圍。請重跑 tools/calibrate.py", "calibrate"),
    ("找不到遊戲視窗「新楓之谷」。遊戲要先開，並用視窗模式", "window"),
    ("先開遊戲並用視窗模式；標題不同的話改 config 的 window.title", "window"),
    ("Windows 的 UIPI 會默默丟掉…請用「以系統管理員身分執行」開啟終端機或 GUI 再跑",
     "elevate"),
    ("異常狀況（卡住），畫面已存到 logs/anomalies/20260101_030000.png", "anomalies"),
    ("一切正常", None),
])
def test_problem_for_picks_the_next_step(text, action):
    got = live.problem_for(text)
    assert (got[0] if got else None) == action


def test_problem_for_knows_the_doctor_nametag_fix(tmp_path):
    fix = doctor.check_nametag(str(tmp_path)).fix
    assert live.problem_for(fix)[0] == "nametag"
    assert live.gui_text(fix).startswith("準備 › 3 截角色名牌。")


def test_gui_text_rewrites_cli_advice():
    assert live.gui_text("python tools/calibrate.py --write") == "準備 › 2 校正"
    assert live.gui_text("重跑 tools/calibrate.py 再試") == "重跑 準備 › 2 校正 再試"
    assert live.gui_text("先跑 python tools/debug_view.py --snapshot check.png 看框在哪") \
        == "先跑 準備 › 4 辨識自檢 看框在哪"
    assert live.gui_text("或用 gui.py 的錄製功能走一趟") == "或用 巡邏與 buff › 錄製路線走一趟"
    assert live.gui_text("所有 ROI 都會錯位") == "所有 region 都會錯位"


def test_headline_keeps_the_symptom():
    assert live.headline("讀不到 HP：超出畫面。請重跑") == "讀不到 HP：超出畫面"
    assert live.headline("症狀——原因") == "症狀"
    assert live.headline("x" * 70) == "x" * 60 + "…"
    assert live.headline("") == ""


# ---- 視窗標題 ----

def test_title_follows_the_run():
    v = _view(_running(), Context(thread_alive=True), now=100.0 + 5025)
    t = live.title_text(v, "弓箭手訓練場一", dirty=False)
    assert t == "▶ 執行中 1:23:45｜HP 82%｜弓箭手訓練場一｜mapleai"
    v = _view(_running(dry_run=True), now=101.0)
    assert live.title_text(v, "m", False).startswith("▶ 預演・執行中 0:01｜")
    v = _view(_running(paused=True, pause_reason="黑屏持續 30 秒（斷線、換頻道或讀圖）"))
    assert live.title_text(v, "m", False) == "‖ 自動暫停｜m｜mapleai"
    v = _view(_running(running=False, ended_at=150.0, stop_reason="PANIC：HP 見底"), Context())
    assert live.title_text(v, "m", False) == "■ 已停機｜m｜mapleai"
    v = _view(Status(), Context())
    assert live.title_text(v, "m", True) == "mapleai 控制台 — m *"


# ---- 符號白名單 ----

def _strings(v):
    yield from (v.state, v.pill, v.detail, v.lock_text, v.elapsed, v.summary, v.primary[1])
    if v.action:
        yield v.action[1]
    yield from (m.text for m in v.meters)
    yield from (text for text, _tone in v.lines)
    yield live.title_text(v, "地圖", True)


def _every_view():
    ctxs = [Context(), Context(dry_choice=True), ALIVE, Context(thread_alive=True, stopping=True),
            Context(tool_label="校正"), Context(recording=True, record_info=(1.0, 1, 2)),
            Context(tool_failed=("校正", "x")), Context(tool_cancelled="截角色名牌"),
            Context(loaded=False, load_path="a.yaml", load_error="壞了"),
            Context(crash="E"), Context(start_error="找不到遊戲視窗「X」"),
            Context(last_error="讀不到 HP：regions.hp_bar")]
    sts = [Status(), Status(preflight="checking"), Status(preflight="fail"), _running(),
           _running(paused=True, pause_reason="r"), _running(paused=True, paused_by_user=True),
           _running(running=False, ended_at=150.0, stop_reason="PANIC：x"),
           _running(running=False, ended_at=150.0, stop_reason="達到最長運行時間（1 分鐘）")]
    for ctx in ctxs:
        for st in sts:
            yield _view(st, ctx, lim=Limits(max_runtime=60))


def test_every_view_string_stays_within_the_glyph_whitelist():
    for v in _every_view():
        assert v.phase in live.PHASES
        assert v.tone in ("ok", "warn", "bad", "info", "tool", "muted")
        for s in _strings(v):
            assert live.symbols_ok(s), s


def test_copy_tables_stay_within_the_glyph_whitelist():
    texts = list(live.ACTION_LABELS.values())
    texts += [t for pair in live.DIALOGS.values() for t in pair]
    texts += [s.label for s in settings.SPECS.values()] + [s.help for s in settings.SPECS.values()]
    texts += [label for opts in settings.CHOICES.values() for _v, label in opts]
    for t in texts:
        assert live.symbols_ok(t), t


def test_tk_safe_strips_emoji_variation_and_astral_chars():
    assert live.tk_safe("⚠️ 注意") == "！ 注意"
    assert live.tk_safe("✅ ok ❌") == "√ ok ×"
    assert live.tk_safe("a😀b") == "a?b"
    assert live.symbols_ok(live.tk_safe(doctor.MARK[doctor.WARN]))
    assert not live.symbols_ok("⚠️")


# ---- 日誌 ----

def test_log_lines_are_strings_with_a_level_and_kind():
    line = LogLine("[00:00:00] x", "warn", "tool")
    assert line == "[00:00:00] x" and isinstance(line, str)
    assert (line.level, line.kind) == ("warn", "tool")
    assert LogLine("y").level == "info" and LogLine("y").kind == "normal"


def test_classify_reads_the_level_and_the_extras():
    rec = logging.LogRecord("m", logging.ERROR, __file__, 1, "x", (), None)
    assert live.classify(rec) == ("error", "normal")
    rec = logging.LogRecord("m", logging.INFO, __file__, 1, "x", (), None)
    rec.tick = True
    assert live.classify(rec) == ("info", "tick")
    rec = logging.LogRecord("m", logging.WARNING, __file__, 1, "x", (), None)
    rec.tool = True
    assert live.classify(rec) == ("warn", "tool")


def test_problems_only_filter():
    assert live.visible(LogLine("a", "info"), problems_only=False)
    assert not live.visible(LogLine("a", "info"), problems_only=True)
    assert live.visible(LogLine("a", "warn"), problems_only=True)
    assert live.visible(LogLine("a", "error"), problems_only=True)


# ---- 表單問題、自檢判讀 ----

def test_problem_line_lists_errors_in_form_order():
    probs = {"buff3": settings.Problem("error", "buff 3：要填間隔秒數，不然不會施放"),
             "attack_range": settings.Problem("error", "水平距離要填 10–2000 的整數"),
             "fps": settings.Problem("warn", "迴圈頻率一般是 1–60")}
    assert live.problem_line(probs) == "× 2 個欄位要修正才能儲存：水平距離、buff 3"
    assert live.problem_list(probs) == ("・水平距離要填 10–2000 的整數\n"
                                        "・buff 3：要填間隔秒數，不然不會施放")
    assert live.problem_line({"fps": probs["fps"]}) == ""
    assert live.field_label("buffs") == "循環 buff"


def test_verdict_rows_carry_glyphs_and_buttons():
    rows = live.verdict_rows([
        ("ok", "小地圖上找到角色 (1, 2)（綠圈）", None),
        ("fail", "讀不到 HP：HP 條的 region 跑到畫面外了", "calibrate"),
        ("warn", "這張畫面比不中名牌（被擋住、換了角色或解析度）", "nametag"),
        ("warn", "沒框到任何怪物", "tab:vision"),
    ])
    assert rows[0] == ("√", "ok", "小地圖上找到角色 (1, 2)（綠圈）", None)
    assert rows[1][:2] == ("×", "bad") and rows[1][3] == ("calibrate", "重新校正")
    assert rows[2][3] == ("nametag", "重截名牌")
    assert rows[3][3] == ("tab:vision", "到辨識分頁")


def test_a_dry_run_probe_failure_is_not_blamed_on_the_keys():
    """預演不按鍵，探邊一定量不到——不能顯示成紅色停機、叫人去查系統管理員權限。"""
    reason = "PANIC：自動探邊失敗：重試 2 次，量到的可走範圍都只有 0px"
    state, tone, detail, action = live._ended(reason, dry=True)
    assert (state, tone, action) == ("已停止", "muted", None)
    assert "預演" in detail and "系統管理員" not in detail
    assert live._ended(reason, dry=False)[1] == "bad"
