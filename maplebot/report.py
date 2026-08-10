"""收工報告：JSON（機器讀）+ Markdown（人讀）+ PNG 曲線（選配）。

log 檔回答不了「這三小時到底怎麼樣」——它是一萬行的流水帳，而且是給
「當下在看」的人寫的。掛機的人隔天早上想知道的只有四件事：

  賺了多少、出過幾次事、慢在哪、遊戲有沒有掛掉。

所以報告是**摘要**不是紀錄。組資料（`build`）與寫檔（`save`）分開，
前者是純函式所以測得到，後者只負責 I/O。

圖表用 matplotlib，**沒裝就跳過**、不會讓一次成功的掛機在最後一步炸掉。
圖上的字一律用英文：matplotlib 預設字型沒有中文字符，標題寫中文會變成
一排豆腐框——那比英文難讀得多。
"""
import json
import os
import time
from typing import List, Optional

REPORT_DIR = os.path.join("logs", "reports")


def _fmt_hms(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _ascii_only(text: str, fallback: str = "profile") -> str:
    """丟掉非 ASCII 字元，給 matplotlib 的標題用（見 write_chart 的說明）。"""
    kept = "".join(c for c in text if c.isascii() and c.isprintable()).strip()
    return kept or fallback


def build(profile_name: str, started_wall: float, ended_wall: float, now: float,
          stats, exp, metrics, alerts, sysmon, series,
          dry_run: bool = False, stop_reason: str = "") -> dict:
    """把跑完一場的各個統計物件組成一份 dict。純函式，不碰檔案系統。

    `now` 是 monotonic 時鐘（給 ExpTracker 算效率用），`started_wall` /
    `ended_wall` 是 time.time()（給人看日期）。兩種時鐘不能混：monotonic
    的原點是開機時間，印出來會是 1970 年。
    """
    duration = max(ended_wall - started_wall, 0.0)
    exp_rate = exp.per_hour(now)
    return {
        "profile": profile_name,
        "dry_run": dry_run,
        "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_wall)),
        "ended": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ended_wall)),
        "duration_seconds": round(duration, 1),
        "stop_reason": stop_reason,
        "progress": {
            "exp_now": exp.last_exp,
            "gained_levels": round(exp.gained(), 4),
            "levels_per_hour": None if exp_rate is None else round(exp_rate, 3),
            "level_ups": exp.levels,
            "exp_drops": exp.deaths,
        },
        "actions": {
            "ticks": stats.ticks,
            "attacks": stats.attacks,
            "buffs": stats.buffs,
            "potions_hp": stats.potions_hp,
            "potions_mp": stats.potions_mp,
            "loots": stats.loots,
            "climbs": stats.climbs,
            "escapes": stats.escapes,
            # 每分鐘攻擊次數比總次數有用：可以跨場次比較「這張地圖值不值得掛」
            "attacks_per_min": round(stats.attacks / (duration / 60), 1) if duration >= 60 else None,
        },
        "performance": metrics.snapshot(),
        "performance_advice": metrics.advice(),
        "system": sysmon.as_dict(),
        "alerts": {
            "counts": alerts.counts(),
            "dropped": alerts.dropped,
            "records": [r.as_dict() for r in alerts.records],
        },
        "series": series.rows(),
        "series_dropped": series.dropped,
    }


def render_markdown(data: dict) -> str:
    """人讀版。刻意短——長到要捲的摘要就不是摘要了。"""
    p, a, perf = data["progress"], data["actions"], data["performance"]
    rate = p["levels_per_hour"]
    rate_text = "—" if rate is None else f"{rate:.2f} 等/小時"
    attack_text = f"{a['attacks']} 次"
    if a["attacks_per_min"]:
        attack_text += f"（{a['attacks_per_min']}/分）"
    lines = [
        f"# 掛機報告 — {data['profile']}",
        "",
        f"- 期間：{data['started']} → {data['ended']}（{_fmt_hms(data['duration_seconds'])}）",
        f"- 結束原因：{data['stop_reason'] or '正常結束'}"
        + ("（dry-run，沒有真的送出按鍵）" if data["dry_run"] else ""),
        "",
        "## 收穫",
        "",
        "| 項目 | 數字 |",
        "|---|---|",
        f"| 累積經驗 | {p['gained_levels']:+.2%} 等 |",
        f"| 效率 | {rate_text} |",
        f"| 升級 | {p['level_ups']} 次 |",
        f"| 經驗倒退（死亡） | {p['exp_drops']} 次 |",
        f"| 攻擊 | {attack_text} |",
        f"| 補血 / 補魔 | {a['potions_hp']} / {a['potions_mp']} |",
        f"| buff / 撿物 / 爬繩 / 脫困 | "
        f"{a['buffs']} / {a['loots']} / {a['climbs']} / {a['escapes']} |",
        "",
        "## 效能",
        "",
        f"- 實際 {perf['actual_fps']:.1f} FPS（目標 {perf['target_fps']:g}），"
        f"{perf['ticks']} tick，超支 {perf['overrun_ratio']:.0%}",
    ]
    for name, ms in perf["stages_ms"].items():
        lines.append(f"  - `{name}` 平均 {ms['avg']}ms、p95 {ms['p95']}ms、最慢 {ms['max']}ms")
    if data["performance_advice"]:
        lines += ["", f"> {data['performance_advice']}"]

    sysd = data["system"]
    lines += ["", "## 系統"]
    if not sysd["available"]:
        lines.append("- 未啟用"
                     + ("（`pip install psutil` 後可監看遊戲行程與資源）"
                        if not sysd["psutil"] else ""))
    else:
        last = sysd.get("last") or {}
        lines.append(f"- 遊戲行程：{'存活' if last.get('game_alive') else '已結束'}"
                     + (f"（PID {last['game_pid']}，"
                        f"執行 {_fmt_hms(last.get('game_uptime') or 0)}）"
                        if last.get("game_pid") else ""))
        if sysd.get("peak_game_mem_mb"):
            lines.append(f"- 遊戲記憶體峰值：{sysd['peak_game_mem_mb']:.0f} MB")
        if sysd.get("peak_sys_cpu"):
            lines.append(f"- 系統 CPU 峰值：{sysd['peak_sys_cpu']:.0f}%")

    al = data["alerts"]
    lines += ["", "## 警報", ""]
    if not al["counts"]:
        lines.append("期間沒有任何警報。")
    else:
        lines.append("、".join(f"{k} {v} 次" for k, v in sorted(al["counts"].items())))
        lines += ["", "最近幾筆："]
        for r in al["records"][-10:]:
            lines.append(f"- `{r['time']}` **{r['kind']}** {r['message']}")
    return "\n".join(lines) + "\n"


def write_chart(data: dict, path: str) -> Optional[str]:
    """HP/MP/EXP、怪物數、FPS、記憶體四張小圖。matplotlib 沒裝就回 None。"""
    rows = data.get("series") or []
    if len(rows) < 2:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")            # 沒有顯示裝置也要能出圖
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    mins = [r["t"] / 60.0 for r in rows]

    def col(name):
        return [r.get(name) for r in rows]

    fig, axes = plt.subplots(2, 2, figsize=(12, 7))
    # 標題只放 ASCII：matplotlib 預設字型（DejaVu Sans）沒有中文字符，
    # profile 名字叫「楓之谷一轉練功」的話整行會變成一排豆腐框。
    # 中文摘要在 .md 裡，圖只負責曲線
    fig.suptitle(f"maplebot session — {_ascii_only(data['profile'])} "
                 f"({_fmt_hms(data['duration_seconds'])})")

    ax = axes[0][0]
    for key, colour in (("hp", "tab:red"), ("mp", "tab:blue"), ("exp", "tab:orange")):
        vals = [None if v is None else v * 100 for v in col(key)]
        if any(v is not None for v in vals):
            ax.plot(mins, vals, color=colour, label=key.upper(), linewidth=1.2)
    ax.set_title("HP / MP / EXP (%)")
    ax.set_ylim(0, 105)
    ax.legend(loc="lower left", fontsize=8)

    axes[0][1].plot(mins, col("mobs"), color="tab:green", linewidth=1.0)
    axes[0][1].set_title("mobs in view")

    axes[1][0].plot(mins, col("fps"), color="tab:purple", linewidth=1.0)
    axes[1][0].axhline(data["performance"]["target_fps"], color="grey",
                       linestyle="--", linewidth=0.8)
    axes[1][0].set_title("loop FPS (dashed = target)")

    mem = col("mem_mb")
    if any(v is not None for v in mem):
        axes[1][1].plot(mins, mem, color="tab:brown", linewidth=1.0)
        axes[1][1].set_title("game memory (MB)")
    else:
        axes[1][1].set_title("game memory — needs psutil")
        axes[1][1].set_axis_off()

    for row in axes:
        for ax in row:
            ax.set_xlabel("minutes")
            ax.grid(alpha=0.3)
    fig.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def save(data: dict, directory: str = REPORT_DIR, chart: bool = True) -> List[str]:
    """寫出報告，回傳實際產生的檔案路徑。"""
    os.makedirs(directory, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    base = os.path.join(directory, f"session_{stamp}")
    written = []

    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    written.append(base + ".json")

    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(render_markdown(data))
    written.append(base + ".md")

    if chart:
        made = write_chart(data, base + ".png")
        if made:
            written.append(made)
    return written
