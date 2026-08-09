"""mapleai 控制台（tkinter，Python 內建，不用裝額外套件）。

版面照著市面上那種輔助工具排：左邊狀態與控制、中間打怪/循環按鍵/安全設定、
右邊小地圖預覽與系統日誌。所有邏輯都在 controller.py，這一層只有畫面。

熱鍵沿用習慣：F9 暫停/繼續、F12 停止、錄製用畫面上的按鈕（錄製時你的手要
在遊戲上，用畫面按鈕比較不會誤觸）。
"""
import tkinter as tk
from tkinter import messagebox, ttk

from . import settings
from .controller import Controller

REFRESH_MS = 200        # UI 更新頻率（bot 的迴圈頻率是另一回事）
PAD = 6


class App(tk.Tk):
    def __init__(self, controller: Controller):
        super().__init__()
        self.ctl = controller
        self.title("mapleai 控制台")
        self.minsize(960, 620)
        self.vars = {}
        self.buff_vars = []
        self._build()
        if self.ctl.load():
            self._fill(self.ctl.values(), self.ctl.buff_rows())
        else:
            messagebox.showerror("設定錯誤", self.ctl.error)
        self.after(REFRESH_MS, self._tick)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ---- 版面 ----

    def _build(self) -> None:
        root = ttk.Frame(self, padding=PAD)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=0)
        root.columnconfigure(1, weight=0)
        root.columnconfigure(2, weight=1)
        root.rowconfigure(0, weight=1)

        left = ttk.Frame(root)
        left.grid(row=0, column=0, sticky="ns", padx=(0, PAD))
        mid = ttk.Frame(root)
        mid.grid(row=0, column=1, sticky="ns", padx=(0, PAD))
        right = ttk.Frame(root)
        right.grid(row=0, column=2, sticky="nsew")
        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)

        self._build_status(left)
        self._build_control(left)
        self._build_record(left)
        self._build_attack(mid)
        self._build_buffs(mid)
        self._build_safety(mid)
        self._build_window(right)
        self._build_position(right)
        self._build_log(right)

    def _labeled(self, parent, row, text, key, width=10, kind=str):
        ttk.Label(parent, text=text).grid(row=row, column=0, sticky="w", pady=1)
        var = tk.StringVar()
        self.vars[key] = var
        ttk.Entry(parent, textvariable=var, width=width).grid(
            row=row, column=1, sticky="w", pady=1)
        return var

    def _checkbox(self, parent, row, text, key):
        var = tk.BooleanVar()
        self.vars[key] = var
        ttk.Checkbutton(parent, text=text, variable=var).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=1)
        return var

    def _build_status(self, parent) -> None:
        box = ttk.LabelFrame(parent, text="狀態", padding=PAD)
        box.pack(fill="x", pady=(0, PAD))
        self.hp_bar = ttk.Progressbar(box, length=200, maximum=100)
        self.hp_bar.grid(row=0, column=0, columnspan=2, sticky="we")
        self.hp_text = ttk.Label(box, text="HP --")
        self.hp_text.grid(row=1, column=0, columnspan=2, sticky="w")
        self.mp_bar = ttk.Progressbar(box, length=200, maximum=100)
        self.mp_bar.grid(row=2, column=0, columnspan=2, sticky="we", pady=(4, 0))
        self.mp_text = ttk.Label(box, text="MP --")
        self.mp_text.grid(row=3, column=0, columnspan=2, sticky="w")
        self.exp_text = ttk.Label(box, text="EXP --")
        self.exp_text.grid(row=4, column=0, columnspan=2, sticky="w")
        self.state_text = ttk.Label(box, text="未執行", foreground="#666")
        self.state_text.grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _build_control(self, parent) -> None:
        box = ttk.LabelFrame(parent, text="控制", padding=PAD)
        box.pack(fill="x", pady=(0, PAD))
        ttk.Label(box, text="地圖 profile").grid(row=0, column=0, sticky="w")
        self.profile_var = tk.StringVar(value=self.ctl.profile_path)
        self.profile_box = ttk.Combobox(box, textvariable=self.profile_var,
                                        values=self.ctl.profiles(), width=28)
        self.profile_box.grid(row=1, column=0, columnspan=2, sticky="we", pady=(0, 4))
        self.profile_box.bind("<<ComboboxSelected>>", self._switch_profile)

        self.dry_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="預演模式（不送出按鍵）",
                        variable=self.dry_var).grid(row=2, column=0, columnspan=2,
                                                    sticky="w")
        self.start_btn = ttk.Button(box, text="開始打怪", command=self._start)
        self.start_btn.grid(row=3, column=0, sticky="we", pady=(4, 0))
        self.stop_btn = ttk.Button(box, text="停止", command=self._stop, state="disabled")
        self.stop_btn.grid(row=3, column=1, sticky="we", pady=(4, 0))
        self.pause_btn = ttk.Button(box, text="暫停 / 繼續", command=self._pause,
                                    state="disabled")
        self.pause_btn.grid(row=4, column=0, columnspan=2, sticky="we")
        ttk.Label(box, text="遊戲中也可按 F9 暫停、F12 停止",
                  foreground="#666").grid(row=5, column=0, columnspan=2, sticky="w")
        ttk.Button(box, text="儲存設定", command=self._save).grid(
            row=6, column=0, sticky="we", pady=(6, 0))
        ttk.Button(box, text="重新載入", command=self._reload).grid(
            row=6, column=1, sticky="we", pady=(6, 0))
        ttk.Separator(box).grid(row=7, column=0, columnspan=2, sticky="we", pady=6)
        ttk.Button(box, text="重新校正 ROI", command=self._calibrate).grid(
            row=8, column=0, sticky="we")
        ttk.Button(box, text="辨識自檢", command=self._check).grid(
            row=8, column=1, sticky="we")
        ttk.Label(box, text="換過遊戲視窗大小就要重新校正",
                  foreground="#666").grid(row=9, column=0, columnspan=2, sticky="w")

    def _build_record(self, parent) -> None:
        box = ttk.LabelFrame(parent, text="錄製巡邏路線", padding=PAD)
        box.pack(fill="x", pady=(0, PAD))
        ttk.Label(box, text="按下錄製後照平常走一趟，\n程式會把路線壓成巡邏點",
                  foreground="#666", justify="left").grid(row=0, column=0, columnspan=2,
                                                          sticky="w")
        self.rec_btn = ttk.Button(box, text="開始錄製", command=self._record)
        self.rec_btn.grid(row=1, column=0, columnspan=2, sticky="we", pady=(4, 0))
        self.rec_text = ttk.Label(box, text="", foreground="#666")
        self.rec_text.grid(row=2, column=0, columnspan=2, sticky="w")

    def _build_attack(self, parent) -> None:
        box = ttk.LabelFrame(parent, text="打怪設定", padding=PAD)
        box.pack(fill="x", pady=(0, PAD))
        self._labeled(box, 0, "攻擊按鍵", "attack_key")
        ttk.Label(box, text="技能型態").grid(row=1, column=0, sticky="w")
        self.vars["attack_type"] = tk.StringVar()
        ttk.Combobox(box, textvariable=self.vars["attack_type"], width=12,
                     values=["directional", "aoe"], state="readonly").grid(
            row=1, column=1, sticky="w")
        self._labeled(box, 2, "攻擊時間（秒）", "attack_seconds")
        self._labeled(box, 3, "打怪距離（像素）", "attack_range")
        self._labeled(box, 4, "垂直距離（像素）", "attack_vrange")
        self._labeled(box, 5, "連發次數", "attack_repeat")
        self._labeled(box, 6, "撿物按鍵", "loot_key")
        self._labeled(box, 7, "跳躍按鍵", "jump_key")
        self._labeled(box, 8, "上爬 / 下爬", "climb_up_key")
        self._labeled(box, 9, "　（下）", "climb_down_key")
        ttk.Label(box, text="巡邏點（auto = 開場自己量）").grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.vars["waypoints"] = tk.StringVar()
        ttk.Entry(box, textvariable=self.vars["waypoints"], width=30).grid(
            row=11, column=0, columnspan=2, sticky="we")

    def _build_buffs(self, parent) -> None:
        box = ttk.LabelFrame(parent, text="循環按鍵（buff）", padding=PAD)
        box.pack(fill="x", pady=(0, PAD))
        for i in range(settings.BUFF_SLOTS):
            key = tk.StringVar()
            every = tk.StringVar()
            self.buff_vars.append((key, every))
            col = (i % 2) * 3
            row = i // 2
            ttk.Entry(box, textvariable=key, width=5).grid(row=row, column=col, pady=1)
            ttk.Label(box, text="每").grid(row=row, column=col + 1)
            ttk.Entry(box, textvariable=every, width=5).grid(row=row, column=col + 2,
                                                             padx=(0, 8))

    def _build_safety(self, parent) -> None:
        box = ttk.LabelFrame(parent, text="藥水與安全", padding=PAD)
        box.pack(fill="x")
        self._labeled(box, 0, "HP 藥按鍵", "hp_key")
        self._labeled(box, 1, "低於 % 時吃", "hp_below")
        self._labeled(box, 2, "MP 藥按鍵", "mp_key")
        self._labeled(box, 3, "低於 % 時吃", "mp_below")
        self._labeled(box, 4, "危險血量 %", "critical_hp")
        self._labeled(box, 5, "連續幾幀才停機", "critical_hp_frames")
        self._labeled(box, 6, "幾分沒經驗就停", "exp_stall_minutes")
        self._labeled(box, 7, "回城卷按鍵", "panic_return_key")
        self._checkbox(box, 8, "有其他玩家時暫停", "pause_when_players")
        self._checkbox(box, 9, "危險事件嗶聲", "sound_alerts")
        self._checkbox(box, 10, "濾掉寵物（需鏡頭會捲動）", "filter_followers")

    def _build_window(self, parent) -> None:
        box = ttk.LabelFrame(parent, text="遊戲視窗與辨識", padding=PAD)
        box.grid(row=0, column=0, sticky="we", pady=(0, PAD))
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="視窗標題").grid(row=0, column=0, sticky="w")
        self.vars["window_title"] = tk.StringVar()
        ttk.Entry(box, textvariable=self.vars["window_title"]).grid(
            row=0, column=1, columnspan=5, sticky="we", padx=(4, 0))
        ttk.Label(box, text="（子字串比對；找不到時系統日誌會列出開著的視窗）",
                  foreground="#666").grid(row=1, column=0, columnspan=6, sticky="w")

        for col, (text, key) in enumerate([
            ("每秒 tick", "fps"), ("偵測間隔(秒)", "mob_interval"),
            ("描邊門檻", "outline_black_level"), ("最小面積", "outline_min_area"),
        ]):
            ttk.Label(box, text=text).grid(row=2 + col // 2, column=(col % 2) * 3,
                                           sticky="w", pady=1)
            self.vars[key] = tk.StringVar()
            ttk.Entry(box, textvariable=self.vars[key], width=8).grid(
                row=2 + col // 2, column=(col % 2) * 3 + 1, sticky="w", padx=(4, 16))
        ttk.Label(box, text="抓不到怪 → 描邊門檻調高（8→15）；框到背景 → 調低（8→4）",
                  foreground="#666").grid(row=4, column=0, columnspan=6, sticky="w")

    def _build_position(self, parent) -> None:
        box = ttk.LabelFrame(parent, text="即時偵測", padding=PAD)
        box.grid(row=1, column=0, sticky="we", pady=(0, PAD))
        self.pos_text = ttk.Label(box, text="當前位置: --", font=("", 11))
        self.pos_text.pack(anchor="w")
        self.mob_text = ttk.Label(box, text="怪物: --｜其他玩家: --｜寵物: --")
        self.mob_text.pack(anchor="w")
        self.tick_text = ttk.Label(box, text="tick 0", foreground="#666")
        self.tick_text.pack(anchor="w")

    def _build_log(self, parent) -> None:
        box = ttk.LabelFrame(parent, text="系統日誌", padding=PAD)
        box.grid(row=2, column=0, sticky="nsew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        self.logbox = tk.Text(box, height=20, wrap="none", state="disabled")
        self.logbox.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(box, command=self.logbox.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.logbox["yscrollcommand"] = bar.set

    # ---- 資料進出 ----

    def _fill(self, values, buff_rows) -> None:
        for key, var in self.vars.items():
            if key in values:
                var.set(values[key])
        for (kv, ev), (key, every) in zip(self.buff_vars, buff_rows):
            kv.set(key)
            ev.set(every)

    def _collect(self):
        return ({k: v.get() for k, v in self.vars.items()},
                [(k.get(), e.get()) for k, e in self.buff_vars])

    def _save(self) -> None:
        values, buffs = self._collect()
        if self.ctl.save(values, buffs):
            self._fill(self.ctl.values(), self.ctl.buff_rows())
        else:
            messagebox.showerror("存檔失敗", self.ctl.error)

    def _reload(self) -> None:
        if self.ctl.load():
            self._fill(self.ctl.values(), self.ctl.buff_rows())

    def _switch_profile(self, _event=None) -> None:
        self.ctl.profile_path = self.profile_var.get()
        self._reload()

    # ---- 按鈕 ----

    def _start(self) -> None:
        values, buffs = self._collect()
        if not self.ctl.save(values, buffs):      # 先存再跑，避免「改了沒生效」
            messagebox.showerror("存檔失敗", self.ctl.error)
            return
        if not self.ctl.start(dry_run=self.dry_var.get()):
            messagebox.showerror("無法開始", self.ctl.error or "已經在執行中")

    def _stop(self) -> None:
        self.ctl.stop()

    def _pause(self) -> None:
        self.ctl.toggle_pause()

    def _calibrate(self) -> None:
        if self.ctl.calibrate():
            messagebox.showinfo(
                "校正", "接下來會依序跳出五個框選視窗：\n"
                "拖曳出範圍後按 Enter 確認，按 c 跳過。\n\n"
                "順序：小地圖 → HP → MP → EXP → 主畫面\n"
                "完成後會自動寫回設定檔，不用手動複製。")
        else:
            messagebox.showwarning("無法校正", self.ctl.error or "執行中，請先停止")

    def _check(self) -> None:
        if self.ctl.check_vision():
            messagebox.showinfo("辨識自檢",
                                "已擷取一張畫面並標註辨識結果，存到 logs/check.png。\n"
                                "打開來確認：血條 % 對不對、怪有沒有被框到。")
        else:
            messagebox.showwarning("無法自檢", self.ctl.error or "執行中，請先停止")

    def _record(self) -> None:
        if self.ctl.recording:
            text = self.ctl.stop_record()
            self.rec_btn["text"] = "開始錄製"
            if text:
                self.vars["waypoints"].set(text)
                self.rec_text["text"] = ("已填入巡邏點，記得按「儲存設定」\n"
                                         "（路線內容看系統日誌）")
            else:
                self.rec_text["text"] = "沒錄到有效座標——先按「辨識自檢」看小地圖"
            return
        if self.ctl.start_record():
            self.rec_btn["text"] = "停止錄製"
            self.rec_text["text"] = "錄製中…走一趟你的路線"
        else:
            messagebox.showerror("無法錄製", self.ctl.error or "已經在執行中")

    # ---- 更新 ----

    def _tick(self) -> None:
        st = self.ctl.status()
        self.hp_bar["value"] = (st.hp or 0) * 100
        self.mp_bar["value"] = (st.mp or 0) * 100
        self.hp_text["text"] = f"HP {_pct(st.hp)}"
        self.mp_text["text"] = f"MP {_pct(st.mp)}"
        self.exp_text["text"] = f"EXP {_pct(st.exp)}"
        self.pos_text["text"] = f"當前位置: {st.player if st.player else '--'}"
        self.mob_text["text"] = (f"怪物: {st.mobs}｜其他玩家: {st.others}"
                                 f"｜寵物: {st.followers}")
        self.tick_text["text"] = f"tick {st.ticks}｜決策 {st.action} {st.reason}"

        running, recording = self.ctl.running, self.ctl.recording
        if recording:
            self.state_text["text"] = "錄製中"
            self.state_text["foreground"] = "#b26b00"
        elif running:
            self.state_text["text"] = "暫停中" if st.paused else "執行中"
            self.state_text["foreground"] = "#b26b00" if st.paused else "#0a7a24"
        else:
            self.state_text["text"] = "未執行"
            self.state_text["foreground"] = "#666"
        self.start_btn["state"] = "disabled" if (running or recording) else "normal"
        self.stop_btn["state"] = "normal" if running else "disabled"
        self.pause_btn["state"] = "normal" if running else "disabled"
        self.rec_btn["state"] = "disabled" if running else "normal"

        lines = self.ctl.drain_logs()
        if lines:
            self.logbox["state"] = "normal"
            self.logbox.insert("end", "\n".join(lines) + "\n")
            self.logbox.see("end")
            self.logbox["state"] = "disabled"
        self.after(REFRESH_MS, self._tick)

    def _close(self) -> None:
        self.ctl.stop()
        self.destroy()


def _pct(v):
    return f"{v:.0%}" if v is not None else "--"


def main(config="config/default.yaml", profile="config/profiles/example.yaml") -> int:
    from .. import log
    App(Controller(config, profile, logger=log.setup())).mainloop()
    return 0
