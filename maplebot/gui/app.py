"""mapleai 控制台（tkinter，Python 內建，不用裝額外套件）。

這一層只有畫面。什麼時候該顯示什麼字、按鈕能不能按、欄位有沒有改過或填錯，
全部在不碰 tkinter 的模組裡算好：

- controller.py：開關 bot、存讀設定、工具、poll() 給出整張執行卡
- live.py：執行卡／儀表／日誌的 view-model（View、Meter、LogLine、對話框文案）
- setup.py：準備清單的 7 個步驟、環境檢查摘要
- settings.py：欄位規格表（標籤、單位、範圍、說明）、驗證
- look.py：配色、字型、DPI 換算

所以這裡的每個方法都只是「把算好的東西貼到元件上」，或「把使用者的點擊轉成
controller 呼叫」。

版面：上面是地圖與存檔狀態，左邊是準備清單與設定分頁，右邊是執行卡、即時
狀態與日誌，底下寫明「儲存」會寫進哪兩個檔。熱鍵沿用 F9 暫停/繼續、F12 停止
（由 runner 輪詢，遊戲在前景也有效）。
"""
import os
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, simpledialog, ttk
from typing import Dict, List, Optional, Tuple

from .. import window as winmod
from . import keys, live, look, settings, setup
from .controller import Controller

REFRESH_MS = 200        # UI 更新頻率（bot 的迴圈頻率是另一回事）
STEPS_EVERY = 3.0       # 準備清單多久重算一次（視窗有沒有開著）
TITLE_EVERY = 1.0       # 視窗標題一秒最多改一次，不然工作列會閃
LOG_CAP = 2000          # 日誌框最多留幾行（完整的在 logs/run_*.log）
CLOSE_WAIT = 5.0        # 關閉時最多等 bot 放開按鍵幾秒
CAPTURE_PROMPT = "按一個鍵…"
# 準備清單說明文字的折行寬度（邏輯 px）。寫死而不跟著視窗寬：跟著寬度折行的話
# 左欄會要多寬就撐多寬，右邊的執行卡被擠到按鈕字都看不到
STEP_WRAP = 340

P = look.PALETTE
SETUP_TAB = "準備"
TABS = [SETUP_TAB, "打怪", settings.BUFF_TAB, "藥水與安全", "辨識"]


def _wrap(text: str) -> str:
    """給會自動折行的 Label：空白換成不斷行空白。

    Tk 折行只認空白，中文整句被當成一個「字」——「框到 2 隻怪物（黃框）…」
    會在「2」後面斷開，剩下一長串再硬切，看起來像排版壞掉。
    """
    return live.tk_safe(text).replace(" ", "\u00a0")


def _fg(tone: str) -> str:
    if tone == "plain":
        return P["text"]
    if tone == "muted":
        return P["muted"]
    return look.TONES.get(tone, look.TONES["muted"])[0]


class Tooltip:
    """滑過 450ms 才出現的提示。取代畫面上一行行的灰字說明。

    text_fn 每次顯示時才呼叫，所以「已修改（檔案裡是 320）」這種會變的內容
    不用另外通知。
    """

    DELAY_MS = 450

    def __init__(self, widget, text_fn, wrap: int):
        self.widget, self.text_fn, self.wrap = widget, text_fn, wrap
        self.tip = None
        self.job = None
        widget.bind("<Enter>", self._enter, add="+")
        for ev in ("<Leave>", "<ButtonPress>", "<FocusOut>"):
            widget.bind(ev, self._hide, add="+")

    def _enter(self, _e=None):
        self._cancel()
        self.job = self.widget.after(self.DELAY_MS, self._show)

    def _cancel(self):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None

    def _show(self):
        self.job = None
        text = live.tk_safe(self.text_fn() or "")
        if not text or not self.widget.winfo_viewable():
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tk.Label(tip, text=text, justify="left", wraplength=self.wrap,
                 background=P["text"], foreground=P["bg"], padx=8, pady=5).pack()
        tip.update_idletasks()
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        w, h = tip.winfo_reqwidth(), tip.winfo_reqheight()
        x = max(0, min(x, tip.winfo_screenwidth() - w - 4))
        if y + h > tip.winfo_screenheight():
            y = self.widget.winfo_rooty() - h - 4
        tip.wm_geometry(f"+{x}+{y}")
        self.tip = tip

    def _hide(self, _e=None):
        self._cancel()
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class MeterBar(tk.Canvas):
    """HP/MP/EXP 色條，附喝藥線與停機線刻度。

    ttk.Progressbar 改不了顏色、也畫不了門檻刻度；而「HP 離喝藥線還有多遠」
    正是從房間另一頭瞄一眼要看的東西。比例沒變就不重畫（每秒 5 次 poll）。
    """

    def __init__(self, parent, kind: str, height: int):
        super().__init__(parent, height=height, highlightthickness=0, bd=0,
                         background=P["card"])
        self.fill_color, self.trough = look.METERS[kind]
        self._state = None
        self.bind("<Configure>", lambda _e: self._draw())

    def show(self, m: live.Meter) -> None:
        ratio = None if m.ratio is None else round(m.ratio, 3)
        state = (ratio, m.marks, m.tone == "muted")
        if state != self._state:
            self._state = state
            self._draw()

    def _draw(self) -> None:
        if self._state is None:
            return
        ratio, marks, muted = self._state
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2:
            return
        self.delete("all")
        pad = 3                                    # 刻度比條子高出的那 3px
        self.create_rectangle(0, pad, w, h - pad, fill=self.trough, width=0)
        if ratio is not None:
            self.create_rectangle(0, pad, ratio * w, h - pad, width=0,
                                  fill="#9CA3AF" if muted else self.fill_color)
        for r, kind in marks:
            x = max(1, min(w - 1, r * w))
            self.create_rectangle(x - 1, 0, x + 1, h, fill=look.MARKS[kind], width=0)


class ScrollFrame(ttk.Frame):
    """分頁內容放不下時才出現捲軸。

    最小視窗（900x600）時準備清單與巡邏分頁都比可用高度高；直接截掉的話
    最下面的「預演一次」與環境檢查會看不到。內容是畫在 Canvas 上的 inner。
    """

    def __init__(self, parent, padding):
        super().__init__(parent, style="Card.TFrame")
        self.canvas = tk.Canvas(self, background=P["card"], highlightthickness=0, bd=0)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, style="Card.TFrame", padding=padding)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self._scrolled)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.bar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.inner.bind("<Configure>", self._inner_changed)
        self.canvas.bind("<Configure>", self._canvas_changed)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind(seq, self._wheel)
        self.bind("<Enter>", lambda _e: self._grab_wheel(True))
        self.bind("<Leave>", lambda _e: self._grab_wheel(False))

    def _inner_changed(self, _e=None) -> None:
        # 分頁的寬度照內容要求（notebook 取所有分頁最寬的那個）
        self.canvas.configure(width=self.inner.winfo_reqwidth(),
                              height=self.inner.winfo_reqheight())
        self._canvas_changed()

    def _canvas_changed(self, _e=None) -> None:
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w > 1:
            # 放得下時把 inner 撐滿，底下那行說明才會貼在分頁最下面
            self.canvas.itemconfigure(self._win, width=w,
                                      height=max(h, self.inner.winfo_reqheight()))
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _scrolled(self, first, last) -> None:
        self.bar.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.bar.grid_remove()
        else:
            self.bar.grid()

    def _grab_wheel(self, on: bool) -> None:
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            if on:
                self.bind_all(seq, self._wheel)
            else:
                self.unbind_all(seq)

    def _wheel(self, event) -> None:
        if isinstance(event.widget, tk.Text) or not self.bar.winfo_ismapped():
            return                            # 巡邏點文字框自己會捲
        if event.num == 4 or getattr(event, "delta", 0) > 0:
            self.canvas.yview_scroll(-1, "units")
        else:
            self.canvas.yview_scroll(1, "units")


class App(tk.Tk):
    def __init__(self, controller: Controller):
        super().__init__()
        self.withdraw()                     # 版面排好再出現，不閃一個空視窗
        self.ctl = controller
        self.s = self.winfo_fpixels("1i") / 96.0
        self._setup_look()
        # Tk 8.6 的下拉選單會被滑鼠滾輪換值（還會發 <<ComboboxSelected>>）：
        # 捲動頁面時滑過地圖選單，就默默切到另一張地圖。整個關掉
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_class("TCombobox", seq, lambda _e: "break")

        self.vars: Dict[str, tk.Variable] = {}
        self.inputs: Dict[str, tk.Widget] = {}        # 欄位 -> 輸入元件
        self.labels: Dict[str, ttk.Label] = {}        # 欄位 -> 標籤（標髒用）
        self.glyphs: Dict[str, ttk.Label] = {}        # 欄位 -> ×／！
        self.buff_vars: List[Tuple[tk.StringVar, tk.StringVar]] = []
        self.lockable: List[tk.Widget] = []           # 執行中要鎖住的東西
        self.tab_frames: Dict[str, ttk.Frame] = {}
        self._filling = False
        self._refresh_pending = False
        self._capture = None          # (欄位, 變數, 原本的鍵)：正在等使用者按鍵
        self._changes: List[str] = []
        self._problems: Dict[str, settings.Problem] = {}
        self._buff_base: List[Tuple[str, str]] = []
        self._locked_fields: Dict[str, str] = {}
        self._locked: Optional[bool] = None
        self._gen = -1
        self._steps_at = 0.0
        self._title_at = 0.0
        self._title = ""
        self._readiness = object()
        self._view: Optional[live.View] = None
        self._choices: List[Tuple[str, str]] = []
        self._vision_win = None
        self._closing = False

        self._build()
        loaded = self.ctl.load()
        if loaded:
            self._fill()
        else:
            self._refresh_form()              # 標頭顯示「設定讀不到」、表單鎖住
            self._update_footer()
        self._refresh_choices()
        self._refresh_steps()
        self.ctl.refresh_readiness()
        self._restore_window()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind_all("<Control-s>", self._save_shortcut)
        self.bind_all("<Control-S>", self._save_shortcut)
        self.deiconify()
        self._tick_job = self.after(REFRESH_MS, self._tick)

    # ------------------------------------------------------------------
    # 外觀
    # ------------------------------------------------------------------

    def px(self, n: float) -> int:
        return look.scale(n, self.s)

    def _setup_look(self) -> None:
        family = look.pick_font(tkfont.families(self))
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                     "TkCaptionFont", "TkTooltipFont"):
            f = tkfont.nametofont(name)
            if family:
                f.configure(family=family)
            f.configure(size=10)
        base = tkfont.nametofont("TkDefaultFont").actual()
        fam = base["family"]
        self.f_bold = tkfont.Font(self, family=fam, size=10, weight="bold")
        self.f_small = tkfont.Font(self, family=fam, size=9)
        self.f_pill = tkfont.Font(self, family=fam, size=11, weight="bold")
        self.f_icon = tkfont.Font(self, family=fam, size=12, weight="bold")
        self.f_head = tkfont.Font(self, family=fam, size=11, weight="bold")
        # Combobox 的下拉清單是 Tk 的 Listbox，不吃 ttk 樣式
        self.option_add("*TCombobox*Listbox.font", "TkDefaultFont")
        self.option_add("*TCombobox*Listbox.selectBackground", P["primary"])

        self.configure(background=P["bg"])
        st = ttk.Style(self)
        st.theme_use("clam")
        px = self.px
        st.configure(".", background=P["bg"], foreground=P["text"], bordercolor=P["border"],
                     lightcolor=P["bg"], darkcolor=P["bg"], troughcolor=P["border"],
                     focuscolor=P["primary"], selectbackground=P["primary"],
                     selectforeground="#FFFFFF", fieldbackground=P["card"],
                     insertcolor=P["text"], font="TkDefaultFont")
        st.map(".", foreground=[("disabled", "#9CA3AF")])
        for name in ("Card.TFrame", "Card.TLabel", "Card.TCheckbutton", "Card.TRadiobutton"):
            st.configure(name, background=P["card"])
        st.map("Card.TCheckbutton", background=[("active", P["card"])],
               indicatorcolor=[("selected", P["primary"]), ("disabled", P["bg"])])
        st.map("Card.TRadiobutton", background=[("active", P["card"])],
               indicatorcolor=[("selected", P["primary"]), ("disabled", P["bg"])])
        st.configure("Dirty.TCheckbutton", background=P["card"], foreground=P["dirty"])
        st.map("Dirty.TCheckbutton", background=[("active", P["card"])],
               indicatorcolor=[("selected", P["primary"]), ("disabled", P["bg"])])
        st.configure("Muted.TLabel", foreground=P["muted"])
        st.configure("CardMuted.TLabel", background=P["card"], foreground=P["muted"],
                     font=self.f_small)
        st.configure("CardBold.TLabel", background=P["card"], font=self.f_bold)
        st.configure("Group.TLabel", background=P["card"], font=self.f_head)
        st.configure("TSeparator", background=P["border"])

        st.configure("TButton", padding=(px(10), px(4)), background=P["card"],
                     bordercolor=P["border"], lightcolor=P["card"], darkcolor=P["card"],
                     focuscolor=P["card"])
        st.map("TButton",
               background=[("disabled", P["bg"]), ("pressed", "#E5E7EB"),
                           ("active", "#F3F4F6")],
               bordercolor=[("focus", P["primary"])])
        st.configure("Primary.TButton", padding=(px(14), px(5)), background=P["primary"],
                     foreground=P["primary_text"], bordercolor=P["primary"],
                     lightcolor=P["primary"], darkcolor=P["primary"], font=self.f_bold,
                     focuscolor=P["primary"])
        st.map("Primary.TButton",
               background=[("disabled", P["primary_disabled"]),
                           ("pressed", P["primary_hover"]), ("active", P["primary_hover"])],
               foreground=[("disabled", "#FFFFFF")],
               lightcolor=[("active", P["primary_hover"])],
               darkcolor=[("active", P["primary_hover"])])
        st.configure("Strong.TButton", padding=(px(14), px(5)), background=P["strong_bg"],
                     lightcolor=P["strong_bg"], darkcolor=P["strong_bg"], font=self.f_bold,
                     focuscolor=P["strong_bg"])
        st.map("Strong.TButton", background=[("active", "#D1D5DB"), ("pressed", "#D1D5DB")])
        st.configure("Danger.TButton", padding=(px(12), px(5)), foreground=P["danger"],
                     bordercolor=P["danger_border"])
        st.map("Danger.TButton", foreground=[("disabled", "#D1A3A3")],
               bordercolor=[("disabled", P["border"])])
        st.configure("Link.TButton", padding=(px(6), px(2)), background=P["card"],
                     bordercolor=P["card"], lightcolor=P["card"], darkcolor=P["card"],
                     foreground=P["primary"])

        st.configure("TEntry", padding=px(3), fieldbackground=P["card"],
                     bordercolor=P["border"], lightcolor=P["card"], darkcolor=P["card"])
        st.map("TEntry",
               fieldbackground=[("invalid", P["invalid_bg"]), ("disabled", P["bg"])],
               bordercolor=[("invalid", P["invalid_border"]), ("focus", P["primary"])],
               lightcolor=[("focus", P["primary"])])
        st.configure("Key.TEntry", fieldbackground=P["key_bg"])
        st.map("Key.TEntry",
               fieldbackground=[("invalid", P["invalid_bg"]), ("disabled", P["bg"]),
                                ("readonly", P["key_bg"])],
               bordercolor=[("invalid", P["invalid_border"]), ("focus", P["primary"])])
        st.configure("Capture.TEntry", fieldbackground=P["key_capture_bg"],
                     bordercolor=P["primary"])
        st.map("Capture.TEntry", fieldbackground=[("readonly", P["key_capture_bg"])])
        st.configure("TCombobox", padding=px(3), fieldbackground=P["card"],
                     arrowcolor=P["muted"], bordercolor=P["border"],
                     lightcolor=P["card"], darkcolor=P["card"])
        st.map("TCombobox",
               fieldbackground=[("disabled", P["bg"]), ("readonly", P["card"])],
               bordercolor=[("invalid", P["invalid_border"]), ("focus", P["primary"])],
               selectbackground=[("readonly", P["card"])],
               selectforeground=[("readonly", P["text"])])

        st.configure("TNotebook", background=P["bg"], borderwidth=0,
                     tabmargins=(0, 0, 0, 0))
        st.configure("TNotebook.Tab", padding=(px(12), px(5)), background=P["bg"],
                     bordercolor=P["border"], lightcolor=P["bg"], foreground=P["muted"])
        st.map("TNotebook.Tab",
               background=[("selected", P["card"])], lightcolor=[("selected", P["card"])],
               foreground=[("selected", P["text"])], font=[("selected", self.f_bold)],
               expand=[("selected", (0, 0, 0, 0))])
        st.configure("Vertical.TScrollbar", background=P["bg"], troughcolor=P["log_bg"],
                     bordercolor=P["border"], arrowcolor=P["muted"])

    def _card(self, parent, **grid) -> tk.Frame:
        """白底細框的卡片。用 tk.Frame 才有 1px 外框（ttk 的框在 clam 是立體的）。"""
        card = tk.Frame(parent, background=P["card"], highlightthickness=1,
                        highlightbackground=P["border"], highlightcolor=P["border"])
        if grid:
            card.grid(**grid)
        return card

    def _tone_label(self, parent, tone: str, text: str = "", font=None, **kw) -> tk.Label:
        fg, bg = look.TONES[tone]
        return tk.Label(parent, text=text, foreground=fg, background=bg,
                        font=font or self.f_bold, **kw)

    # ------------------------------------------------------------------
    # 版面
    # ------------------------------------------------------------------

    def _build(self) -> None:
        pad = self.px(12)
        root = ttk.Frame(self, padding=(pad, self.px(10), pad, self.px(6)))
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        self._build_header(root)
        left = ttk.Frame(root)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, self.px(10)))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        right = ttk.Frame(root)
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        self._build_left(left)
        self._build_run_card(right)
        self._build_status_card(right)
        self._build_log(right)
        self._build_footer(root)

    def _build_header(self, root) -> None:
        bar = ttk.Frame(root)
        bar.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, self.px(10)))
        bar.columnconfigure(3, weight=1)
        ttk.Label(bar, text="地圖", font=self.f_bold).grid(row=0, column=0,
                                                          padx=(0, self.px(6)))
        self.profile_var = tk.StringVar()
        self.profile_box = ttk.Combobox(bar, textvariable=self.profile_var, state="readonly",
                                        width=30)
        self.profile_box.grid(row=0, column=1, sticky="w")
        self.profile_box.bind("<<ComboboxSelected>>", self._switch_profile)
        Tooltip(self.profile_box, lambda: "一張地圖（角色＋練功地點）一份設定。"
                "標「（範本）」的是共用範例，改了要另存成你自己的地圖", self.px(320))
        self.save_as_btn = ttk.Button(bar, text="另存為我的地圖…", command=self._save_as)
        self.save_as_btn.grid(row=0, column=2, padx=(self.px(6), 0))
        self.dirty_label = ttk.Label(bar, text="", style="Muted.TLabel")
        self.dirty_label.grid(row=0, column=4, padx=(0, self.px(10)))
        Tooltip(self.dirty_label, self._dirty_tooltip, self.px(320))
        self.revert_btn = ttk.Button(bar, text="還原", command=self._revert)
        self.revert_btn.grid(row=0, column=5, padx=(0, self.px(6)))
        self.save_btn = ttk.Button(bar, text="儲存  Ctrl+S", command=self._save)
        self.save_btn.grid(row=0, column=6)

    def _build_footer(self, root) -> None:
        bar = ttk.Frame(root)
        bar.grid(row=2, column=0, columnspan=2, sticky="we", pady=(self.px(6), 0))
        bar.columnconfigure(0, weight=1)
        self.footer_files = ttk.Label(bar, text="", style="Muted.TLabel", font=self.f_small)
        self.footer_files.grid(row=0, column=0, sticky="w")
        self.footer_keys = ttk.Label(bar, text="", style="Muted.TLabel", font=self.f_small)
        self.footer_keys.grid(row=0, column=1, sticky="e")

    # ---- 左邊：準備清單與設定分頁 ----

    def _build_left(self, parent) -> None:
        fg, bg = P["lock_fg"], P["lock_bg"]
        self.lock_strip = tk.Label(parent, text="", foreground=fg, background=bg,
                                   anchor="w", padx=self.px(8), pady=self.px(3),
                                   font=self.f_small)
        self.lock_strip.grid(row=0, column=0, sticky="we", pady=(0, self.px(6)))
        self.lock_strip.grid_remove()

        self.nb = ttk.Notebook(parent)
        self.nb.grid(row=1, column=0, sticky="nsew")
        self.tab_bodies: Dict[str, ttk.Frame] = {}
        for tab in TABS:
            outer = ScrollFrame(self.nb, padding=(self.px(14), self.px(10)))
            outer.inner.columnconfigure(1, weight=1)
            self.nb.add(outer, text=tab)
            self.tab_frames[tab] = outer
            self.tab_bodies[tab] = outer.inner
        self._build_setup_tab(self.tab_bodies[SETUP_TAB])
        for tab in TABS[1:]:
            self._build_form_tab(tab)

        line = ttk.Frame(parent)
        line.grid(row=2, column=0, sticky="we", pady=(self.px(6), 0))
        line.columnconfigure(0, weight=1)
        self.problem_text = tk.Label(line, text="", anchor="w", background=P["bg"],
                                     foreground=_fg("bad"), font=self.f_small)
        self.problem_text.grid(row=0, column=0, sticky="we")
        self.problem_btn = ttk.Button(line, text="前往", command=self._goto_first_error)
        self.problem_btn.grid(row=0, column=1)
        self.problem_btn.grid_remove()

    def _group_header(self, parent, row: int, title: str, scope: Optional[str]) -> int:
        top = self.px(14) if row else 0
        head = ttk.Frame(parent, style="Card.TFrame")
        head.grid(row=row, column=0, columnspan=4, sticky="we", pady=(top, self.px(4)))
        head.columnconfigure(2, weight=1)
        ttk.Label(head, text=title, style="Group.TLabel").grid(row=0, column=0, sticky="w")
        if scope:
            text, fg, bg = look.SCOPES[scope]
            badge = tk.Label(head, text=text, foreground=fg, background=bg,
                             font=self.f_small, padx=self.px(6))
            badge.grid(row=0, column=1, padx=(self.px(8), 0))
            Tooltip(badge, lambda s=scope: (
                "存進這張地圖的設定檔（config/profiles/）" if s == "profile"
                else "存進這台電腦的 config/local.yaml：換電腦或改視窗大小才要動"),
                self.px(320))
        ttk.Separator(head).grid(row=1, column=0, columnspan=3, sticky="we",
                                 pady=(self.px(3), 0))
        return row + 1

    def _build_form_tab(self, tab: str) -> None:
        frame = self.tab_bodies[tab]
        frame.columnconfigure(1, weight=0)
        frame.columnconfigure(3, weight=1)
        row = 0
        for gtab, group, fields in settings.GROUPS:
            if gtab != tab:
                continue
            row = self._group_header(frame, row, group, settings.scope(fields[0]))
            if group == "攻擊":
                self.skills_note = ttk.Label(frame, text="", style="CardMuted.TLabel",
                                             wraplength=self.px(420))
                self.skills_note.grid(row=row, column=0, columnspan=4, sticky="w")
                self.skills_note.grid_remove()
                row += 1
            for field in fields:
                if field == "waypoints":
                    row = self._build_waypoints(frame, row)
                else:
                    row = self._field_row(frame, row, field)
        if tab == settings.BUFF_TAB:
            row = self._build_buffs(frame, row)
        # 說明都收進 tooltip 了，至少要讓人知道有這回事
        frame.rowconfigure(row, weight=1)
        ttk.Label(frame, text="滑鼠停在欄位上會顯示說明與建議範圍；改過還沒存的欄位標籤會變成琥珀色",
                  style="CardMuted.TLabel").grid(row=row + 1, column=0, columnspan=4,
                                                 sticky="w", pady=(self.px(8), 0))

    def _field_row(self, parent, row: int, field: str) -> int:
        spec = settings.SPECS[field]
        pady = self.px(2)
        tip = lambda f=field: self._tooltip(f)       # noqa: E731
        if spec.kind == "bool":
            var = tk.BooleanVar()
            cell = ttk.Frame(parent, style="Card.TFrame")
            cell.grid(row=row, column=1, columnspan=3, sticky="w", pady=pady)
            w = ttk.Checkbutton(cell, text=spec.label, variable=var,
                                style="Card.TCheckbutton")
            w.pack(side="left")
            self.labels[field] = w
        else:
            lbl = ttk.Label(parent, text=spec.label, style="Card.TLabel", anchor="e")
            lbl.grid(row=row, column=0, sticky="e", padx=(0, self.px(8)), pady=pady)
            self.labels[field] = lbl
            Tooltip(lbl, tip, self.px(320))
            var = tk.StringVar()
            # 輸入框和單位放同一格：單位要緊貼數字，不能被同欄最寬的下拉選單推走
            cell = ttk.Frame(parent, style="Card.TFrame")
            cell.grid(row=row, column=1, sticky="w", pady=pady)
            if spec.kind == "key":
                w = self._key_entry(cell, var, width=10)
            elif spec.kind == "choice":
                labels = [label for _v, label in settings.CHOICES[field]]
                w = ttk.Combobox(cell, textvariable=var, values=labels,
                                 state="readonly", width=22)
            else:
                w = ttk.Entry(cell, textvariable=var, width=9, justify="right")
            w.pack(side="left")
            if spec.unit:
                ttk.Label(cell, text=spec.unit, style="CardMuted.TLabel").pack(
                    side="left", padx=(self.px(5), 0))
        Tooltip(w, tip, self.px(320))
        # ×／！ 緊貼在欄位後面，一眼就知道是哪一格
        glyph = ttk.Label(cell, text="", style="Card.TLabel", font=self.f_bold, width=2)
        glyph.pack(side="left", padx=(self.px(4), 0))
        self.glyphs[field] = glyph
        self.vars[field] = var
        self.inputs[field] = w
        self.lockable.append(w)
        var.trace_add("write", self._schedule_refresh)
        return row + 1

    def _key_entry(self, parent, var, width=10) -> ttk.Entry:
        """按鍵欄位：點一下（或 Enter／空白鍵），然後按下你要綁的那個鍵。

        原本要自己打字（"pageup"、"ctrl"），打錯了不會有人告訴你，
        要等 bot 跑起來按鍵沒反應才發現。
        """
        ent = ttk.Entry(parent, textvariable=var, width=width, state="readonly",
                        cursor="hand2", justify="center", style="Key.TEntry")
        ent.bind("<Button-1>", lambda _e: self._start_capture(ent, var))
        # 一個 <Key> 處理全部：另外綁 <Return>／<space> 的話它們比 <Key> 具體，
        # 擷取中按 Enter、空白就永遠綁不上去
        ent.bind("<Key>", lambda e: self._key_event(ent, var, e))
        # 點到別處＝放棄這次擷取。不收掉的話欄位會一直顯示「按一個鍵…」，
        # 存檔就把這幾個字當成按鍵名稱寫進設定檔
        ent.bind("<FocusOut>", lambda _e: self._cancel_capture(ent), add="+")
        ent.bind("<Button-3>", lambda _e: self._clear_key(ent, var))
        return ent

    def _clear_key(self, widget, var) -> None:
        if not self._can_edit() or widget.instate(["disabled"]):
            return
        self._cancel_capture()
        var.set("")

    def _start_capture(self, widget, var):
        if self._capture is not None:
            if self._capture[0] is widget:
                return "break"
            self._cancel_capture()
        # Tk 在 disabled 的 Entry 上照樣觸發 <Button-1>；執行中更不能讓
        # bot 自己送出的按鍵被綁進欄位
        if not self._can_edit() or widget.instate(["disabled"]):
            return "break"
        self._capture = (widget, var, var.get())
        widget.configure(style="Capture.TEntry")
        var.set(CAPTURE_PROMPT)
        widget.focus_set()
        return "break"

    def _key_event(self, widget, var, event):
        cap = self._capture
        if cap is None or cap[0] is not widget:
            if event.keysym in ("Return", "KP_Enter", "space"):
                return self._start_capture(widget, var)
            return None                          # Tab 照常換欄位
        if event.keysym == "Escape":             # 取消，保留原本綁的鍵
            self._cancel_capture()
            return "break"
        name = keys.keysym_to_key(event.keysym)
        if name is None:
            self._cancel_capture()
            self.ctl.log.warning(
                "這個鍵送不進遊戲（%s），沒有綁定。數字鍵盤與 Caps/Num Lock "
                "這類鍵不支援", event.keysym)
        else:
            self._finish_capture(name)
        return "break"

    def _finish_capture(self, value: str) -> None:
        widget, var, _previous = self._capture
        self._capture = None
        widget.configure(style="Key.TEntry")
        var.set(value)

    def _cancel_capture(self, widget=None) -> None:
        """收掉進行中的擷取、填回原本的鍵。widget 有給時只收那一格的。"""
        cap = self._capture
        if cap is not None and (widget is None or cap[0] is widget):
            self._finish_capture(cap[2])

    def _build_waypoints(self, parent, row: int) -> int:
        """巡邏點：自動探邊或指定清單。錄出來的多層路線有好幾百字，單行欄位放不下。"""
        self.wp_mode = tk.StringVar(value="auto")
        modes = ttk.Frame(parent, style="Card.TFrame")
        modes.grid(row=row, column=0, columnspan=4, sticky="w", pady=(0, self.px(4)))
        for i, (value, text) in enumerate((
                ("auto", "自動探邊（開場往左右走到撞牆，自己量範圍）"),
                ("list", "指定巡邏點"))):
            rb = ttk.Radiobutton(modes, text=text, value=value, variable=self.wp_mode,
                                 style="Card.TRadiobutton", command=self._wp_mode_changed)
            rb.grid(row=i, column=0, sticky="w")
            self.lockable.append(rb)
        row += 1
        box = ttk.Frame(parent, style="Card.TFrame")
        box.grid(row=row, column=0, columnspan=4, sticky="we")
        box.columnconfigure(0, weight=1)
        self.wp_text = tk.Text(box, height=5, width=46, wrap="word", undo=True,
                               font="TkTextFont", relief="flat", highlightthickness=1,
                               highlightbackground=P["border"], highlightcolor=P["primary"],
                               background=P["card"], foreground=P["text"],
                               padx=self.px(6), pady=self.px(4))
        self.wp_text.grid(row=0, column=0, sticky="we")
        sb = ttk.Scrollbar(box, command=self.wp_text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.wp_text["yscrollcommand"] = sb.set
        self.wp_text.bind("<<Modified>>", self._wp_modified)
        Tooltip(self.wp_text, lambda: self._tooltip("waypoints"), self.px(320))
        row += 1
        foot = ttk.Frame(parent, style="Card.TFrame")
        foot.grid(row=row, column=0, columnspan=4, sticky="we", pady=(self.px(4), 0))
        foot.columnconfigure(0, weight=1)
        self.wp_summary = tk.Label(foot, text="", anchor="w", justify="left",
                                   background=P["card"], font=self.f_small,
                                   wraplength=self.px(330))
        self.wp_summary.grid(row=0, column=0, sticky="we")
        self.rec_btn = ttk.Button(foot, text="● 錄製路線", command=self._record)
        self.rec_btn.grid(row=0, column=1, padx=(self.px(8), 0))
        Tooltip(self.rec_btn, lambda: "按下後在遊戲裡照平常走一趟（包含上下樓），"
                "走完回來按停止。方向鍵不會錄進去，被怪打歪也能自己回到路線上",
                self.px(320))
        self.labels["waypoints"] = None
        self.inputs["waypoints"] = self.wp_text
        self.glyphs["waypoints"] = None
        return row + 1

    def _build_buffs(self, parent, row: int) -> int:
        row = self._group_header(parent, row, "循環 buff", "profile")
        grid = ttk.Frame(parent, style="Card.TFrame")
        grid.grid(row=row, column=0, columnspan=4, sticky="w")
        self.buff_glyphs: List[ttk.Label] = []
        self.buff_labels: List[ttk.Label] = []
        for i in range(settings.BUFF_SLOTS):
            key, every = tk.StringVar(), tk.StringVar()
            self.buff_vars.append((key, every))
            r, c = i % 4, (i // 4) * 6
            num = ttk.Label(grid, text=f"{i + 1}", style="CardMuted.TLabel", width=2,
                            anchor="e")
            num.grid(row=r, column=c, padx=(0 if c == 0 else self.px(16), self.px(4)),
                     pady=self.px(2))
            self.buff_labels.append(num)
            ke = self._key_entry(grid, key, width=8)
            ke.grid(row=r, column=c + 1)
            ttk.Label(grid, text="每", style="Card.TLabel").grid(row=r, column=c + 2,
                                                                padx=self.px(4))
            ee = ttk.Entry(grid, textvariable=every, width=5, justify="right")
            ee.grid(row=r, column=c + 3)
            ttk.Label(grid, text="秒", style="CardMuted.TLabel").grid(
                row=r, column=c + 4, padx=(self.px(3), 0))
            glyph = ttk.Label(grid, text="", style="Card.TLabel", font=self.f_bold, width=2)
            glyph.grid(row=r, column=c + 5)
            self.buff_glyphs.append(glyph)
            for w in (ke, ee):
                self.lockable.append(w)
                Tooltip(w, lambda k=f"buff{i + 1}": self._tooltip(k), self.px(320))
            for var in (key, every):
                var.trace_add("write", self._schedule_refresh)
            self.inputs[f"buff{i + 1}"] = ee
        self.buff_count = ttk.Label(parent, text="", style="CardMuted.TLabel")
        self.buff_count.grid(row=row + 1, column=0, columnspan=4, sticky="w",
                             pady=(self.px(4), 0))
        return row + 2

    # ---- 準備分頁 ----

    def _build_setup_tab(self, frame) -> None:
        frame.columnconfigure(1, weight=1)
        self.step_rows: Dict[str, dict] = {}
        self.group_labels: Dict[str, ttk.Label] = {}
        row = 0
        for group in ("machine", "map"):
            lbl = ttk.Label(frame, text=setup.GROUP_TITLES[group], style="CardMuted.TLabel")
            lbl.grid(row=row, column=0, columnspan=3, sticky="w",
                     pady=(self.px(10) if row else 0, self.px(2)))
            row += 1
            ids = ("window", "calibrate", "nametag", "vision") if group == "machine" \
                else ("profile", "route", "dry")
            for sid in ids:
                row = self._step_row(frame, row, sid)
        ttk.Separator(frame).grid(row=row, column=0, columnspan=3, sticky="we",
                                  pady=(self.px(10), self.px(6)))
        row += 1
        head = ttk.Frame(frame, style="Card.TFrame")
        head.grid(row=row, column=0, columnspan=3, sticky="we")
        head.columnconfigure(0, weight=1)
        self.ready_summary = tk.Label(head, text="", anchor="w", background=P["card"],
                                      font=self.f_bold)
        self.ready_summary.grid(row=0, column=0, sticky="w")
        ttk.Button(head, text="詳細", style="Link.TButton",
                   command=self._show_readiness).grid(row=0, column=1, sticky="e")
        self.ready_rows = []
        for i in range(2):
            r = ttk.Frame(frame, style="Card.TFrame")
            r.grid(row=row + 1 + i, column=0, columnspan=3, sticky="we")
            r.columnconfigure(1, weight=1)
            g = tk.Label(r, text="", background=P["card"], font=self.f_bold, width=2)
            g.grid(row=0, column=0, sticky="nw")
            t = tk.Label(r, text="", background=P["card"], anchor="w", justify="left",
                         font=self.f_small, foreground=P["muted"],
                         wraplength=self.px(STEP_WRAP))
            t.grid(row=0, column=1, sticky="we")
            b = ttk.Button(r, text="", style="Link.TButton")
            b.grid(row=0, column=2, sticky="e")
            self.ready_rows.append((r, g, t, b))

    def _step_row(self, frame, row: int, sid: str) -> int:
        icon = tk.Label(frame, text="○", background=P["card"], font=self.f_icon,
                        width=2, anchor="n")
        icon.grid(row=row, column=0, rowspan=2, sticky="n", pady=(self.px(4), 0))
        title = ttk.Label(frame, text="", style="CardBold.TLabel")
        title.grid(row=row, column=1, sticky="w", pady=(self.px(5), 0))
        detail = ttk.Label(frame, text="", style="CardMuted.TLabel", justify="left",
                           wraplength=self.px(STEP_WRAP))
        detail.grid(row=row + 1, column=1, sticky="w")
        right = ttk.Frame(frame, style="Card.TFrame")
        right.grid(row=row, column=2, rowspan=2, sticky="e", padx=(self.px(8), 0))
        widgets = dict(icon=icon, title=title, detail=detail, right=right)
        if sid == "window":
            var = tk.StringVar()
            self.vars["window_title"] = var
            # 選單放在標題那一行：放右邊按鈕欄的話整個左欄被撐寬，執行卡就擠了。
            # 下拉時才列舉視窗（postcommand），清單永遠是最新的
            box = ttk.Combobox(frame, textvariable=var, width=24,
                               postcommand=self._fill_window_titles)
            box.grid(row=row, column=1, sticky="e")
            var.trace_add("write", self._schedule_refresh)
            Tooltip(box, lambda: self._tooltip("window_title"), self.px(320))
            self.inputs["window_title"] = box
            self.labels["window_title"] = title
            self.glyphs["window_title"] = None
            self.lockable.append(box)
        else:
            btn = ttk.Button(right, text="", command=lambda s=sid: self._step_action(s))
            btn.grid(row=0, column=0)
            widgets["button"] = btn
        self.step_rows[sid] = widgets
        return row + 2

    # ---- 右邊：執行卡、即時狀態、日誌 ----

    def _build_run_card(self, parent) -> None:
        card = self._card(parent, row=0, column=0, sticky="we")
        card.columnconfigure(1, weight=1)
        self.stripe = tk.Frame(card, width=self.px(6), background=_fg("muted"))
        self.stripe.grid(row=0, column=0, sticky="ns")
        body = tk.Frame(card, background=P["card"], padx=self.px(12), pady=self.px(10))
        body.grid(row=0, column=1, sticky="nsew")
        body.columnconfigure(0, weight=1)

        top = tk.Frame(body, background=P["card"])
        top.grid(row=0, column=0, columnspan=2, sticky="we")
        top.columnconfigure(2, weight=1)
        self.pill = self._tone_label(top, "muted", "● 待命", font=self.f_pill,
                                     padx=self.px(10), pady=self.px(2))
        self.pill.grid(row=0, column=0, sticky="w")
        self.dry_tag = self._tone_label(top, "tool", "預演", font=self.f_small,
                                        padx=self.px(6))
        self.dry_tag.grid(row=0, column=1, padx=(self.px(6), 0))
        self.dry_tag.grid_remove()
        self.elapsed = tk.Label(top, text="", background=P["card"], foreground=P["muted"],
                                font=self.f_pill)
        self.elapsed.grid(row=0, column=2, sticky="e")

        self.detail = tk.Label(body, text="", background=P["card"], anchor="w",
                               justify="left", foreground=P["text"])
        self.detail.grid(row=1, column=0, sticky="we", pady=(self.px(8), 0))
        self.action_btn = ttk.Button(body, text="", command=self._run_action)
        self.action_btn.grid(row=1, column=1, sticky="ne", pady=(self.px(6), 0),
                             padx=(self.px(8), 0))
        self.action_btn.grid_remove()
        self._card_width = 0
        body.bind("<Configure>", self._rewrap_detail)

        self.summary_row = tk.Frame(body, background=P["card"])
        self.summary_row.grid(row=2, column=0, columnspan=2, sticky="we",
                              pady=(self.px(4), 0))
        self.summary_row.columnconfigure(0, weight=1)
        self.summary = tk.Label(self.summary_row, text="", background=P["card"],
                                foreground=P["muted"], anchor="w")
        self.summary.grid(row=0, column=0, sticky="w")
        self.report_btn = ttk.Button(self.summary_row, text="開啟收工報告",
                                     style="Link.TButton",
                                     command=lambda: self.ctl.open_action("report"))
        self.report_btn.grid(row=0, column=1, sticky="e")
        self.summary_row.grid_remove()

        modes = tk.Frame(body, background=P["card"])
        modes.grid(row=3, column=0, columnspan=2, sticky="w", pady=(self.px(10), 0))
        self.dry_var = tk.BooleanVar(value=self.ctl.dry_choice)
        self.mode_btns = []
        for i, (val, text) in enumerate(((True, "預演（不送按鍵）"), (False, "實戰"))):
            rb = ttk.Radiobutton(modes, text=text, value=val, variable=self.dry_var,
                                 style="Card.TRadiobutton", command=self._mode_changed)
            rb.grid(row=0, column=i, padx=(0, self.px(16)))
            self.mode_btns.append(rb)
        Tooltip(modes, lambda: "預演：照常看畫面、做決策、寫日誌，但一個鍵都不按。"
                "第一次跑一張新地圖先預演，確認開場自檢會過、決策看起來合理", self.px(320))

        btns = tk.Frame(body, background=P["card"])
        btns.grid(row=4, column=0, columnspan=2, sticky="we", pady=(self.px(8), 0))
        btns.columnconfigure(0, weight=1)
        self.primary_btn = ttk.Button(btns, text="▶ 開始預演", style="Primary.TButton",
                                      command=self._primary)
        self.primary_btn.grid(row=0, column=0, sticky="we")
        self.stop_btn = ttk.Button(btns, text="■ 停止…", style="Danger.TButton",
                                   command=self._stop)
        # 跟主按鈕隔開一段：暫停和停止不能一手滑就按錯
        self.stop_btn.grid(row=0, column=1, padx=(self.px(16), 0))

    def _rewrap_detail(self, event=None) -> None:
        if event is not None:
            self._card_width = event.width
        room = self._card_width - self.px(28)
        if self.action_btn.winfo_ismapped() or (self._view and self._view.action):
            room -= self.action_btn.winfo_reqwidth() + self.px(8)
        self.detail.configure(wraplength=max(self.px(160), room))

    def _build_status_card(self, parent) -> None:
        card = self._card(parent, row=1, column=0, sticky="we", pady=(self.px(8), 0))
        body = tk.Frame(card, background=P["card"], padx=self.px(12), pady=self.px(8))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        head = tk.Frame(body, background=P["card"])
        head.grid(row=0, column=0, columnspan=3, sticky="we", pady=(0, self.px(4)))
        tk.Label(head, text="即時狀態", background=P["card"], font=self.f_bold).pack(side="left")
        self.frozen = tk.Label(head, text="", background=P["card"], foreground=P["muted"],
                               font=self.f_small)
        self.frozen.pack(side="left", padx=(self.px(6), 0))
        self.meters = []
        for i, (kind, name) in enumerate((("hp", "HP"), ("mp", "MP"), ("exp", "EXP"))):
            tk.Label(body, text=name, background=P["card"], font=self.f_bold, width=4,
                     anchor="w").grid(row=1 + i, column=0, sticky="w")
            bar = MeterBar(body, kind, self.px(18))
            bar.grid(row=1 + i, column=1, sticky="we", pady=self.px(2))
            pct = tk.Label(body, text="—", background=P["card"], width=7, anchor="e",
                           font=self.f_bold)
            pct.grid(row=1 + i, column=2, sticky="e")
            self.meters.append((bar, pct))
        Tooltip(self.meters[0][0], lambda: "細線：琥珀色是喝藥線、深紅色是停機線",
                self.px(320))
        self.lines = []
        for i in range(3):
            lbl = tk.Label(body, text="", background=P["card"], anchor="w",
                           font=self.f_small)
            lbl.grid(row=4 + i, column=0, columnspan=3, sticky="we",
                     pady=(self.px(4) if i == 0 else 0, 0))
            self.lines.append(lbl)

    def _build_log(self, parent) -> None:
        card = self._card(parent, row=2, column=0, sticky="nsew", pady=(self.px(8), 0))
        card.rowconfigure(1, weight=1)
        card.columnconfigure(0, weight=1)
        bar = tk.Frame(card, background=P["card"], padx=self.px(10), pady=self.px(4))
        bar.grid(row=0, column=0, columnspan=2, sticky="we")
        tk.Label(bar, text="系統日誌", background=P["card"], font=self.f_bold).pack(side="left")
        self.problems_only = tk.BooleanVar(value=False)
        self.ticks_var = tk.BooleanVar(value=self.ctl.keep_ticks)
        ttk.Checkbutton(bar, text="只看問題", variable=self.problems_only,
                        style="Card.TCheckbutton",
                        command=self._rerender_log).pack(side="left", padx=(self.px(12), 0))
        cb = ttk.Checkbutton(bar, text="每個 tick", variable=self.ticks_var,
                             style="Card.TCheckbutton", command=self._ticks_changed)
        cb.pack(side="left", padx=(self.px(8), 0))
        Tooltip(cb, lambda: "預演時每個 tick 都會寫一行決策。平常收起來，"
                "要追「為什麼一直讓過」時再打開（完整紀錄一律在 logs/run_*.log）",
                self.px(320))
        ttk.Button(bar, text="開資料夾", style="Link.TButton",
                   command=lambda: self.ctl.open_action("logs")).pack(side="right")
        ttk.Button(bar, text="複製", style="Link.TButton",
                   command=self._copy_log).pack(side="right")
        self.logbox = tk.Text(card, height=8, wrap="char", state="disabled",
                              relief="flat", background=P["log_bg"], foreground=P["text"],
                              font=self.f_small, padx=self.px(8), pady=self.px(4),
                              highlightthickness=0, borderwidth=0)
        self.logbox.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(card, command=self.logbox.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.logbox["yscrollcommand"] = sb.set
        # 續行縮排到時間戳後面，換行的長訊息才看得出是同一筆
        indent = self.f_small.measure("[00:00:00] ")
        self.logbox.tag_configure("line", lmargin2=indent)
        self.logbox.tag_configure("warn", foreground=P["log_warn"])
        self.logbox.tag_configure("error", foreground=P["log_error"], font=(
            self.f_small.actual()["family"], 9, "bold"))
        self.logbox.tag_configure("tool", foreground=P["log_dim"])
        self.logbox.tag_configure("tick", foreground=P["log_dim"])

    # ------------------------------------------------------------------
    # 欄位 <-> 設定
    # ------------------------------------------------------------------

    def _fill(self) -> None:
        """把 controller 讀到的值填進欄位。baseline 在 load() 時就定好了，
        所以填完不會一閃變成「已修改」。"""
        self._cancel_capture()
        values = self.ctl.values()
        rows = self.ctl.buff_rows()
        self._filling = True
        try:
            for field, var in self.vars.items():
                if field not in values:
                    continue
                v = values[field]
                if isinstance(var, tk.BooleanVar):
                    var.set(bool(v))
                else:
                    var.set(settings.display(field, v))
            wp = str(values.get("waypoints", "auto"))
            self.wp_mode.set("auto" if wp.strip().lower() in ("", "auto") else "list")
            self.wp_text.configure(state="normal")
            self.wp_text.delete("1.0", "end")
            if self.wp_mode.get() == "list":
                self.wp_text.insert("1.0", wp)
            self.wp_text.edit_modified(False)
            self.wp_text.edit_reset()
            for (kv, ev), (key, every) in zip(self.buff_vars, rows):
                kv.set(key)
                ev.set(settings.normalize("attack_seconds", every) if every else "")
            self._buff_base = [self._norm_row(k, e) for k, e in rows]
        finally:
            self._filling = False
        self._locked_fields = self.ctl.locked()
        note = next(iter(self._locked_fields.values()), "")
        if note:
            self.skills_note.configure(text=_wrap(note))
            self.skills_note.grid()
        else:
            self.skills_note.grid_remove()
        self._locked = None                       # 重新套一次鎖定狀態
        self._wp_mode_changed(refresh=False)
        self._refresh_form()
        self._update_footer()

    def _collect(self):
        values = {}
        capturing = self._capture[1] if self._capture else None
        previous = self._capture[2] if self._capture else ""
        for field, var in self.vars.items():
            v = previous if var is capturing else var.get()
            if field in settings.CHOICES:
                v = {label: val for val, label in settings.CHOICES[field]}.get(v, v)
            values[field] = v
        values["waypoints"] = ("auto" if self.wp_mode.get() == "auto"
                               else self.wp_text.get("1.0", "end").strip())
        buffs = [(previous if k is capturing else k.get(), e.get())
                 for k, e in self.buff_vars]
        return values, buffs

    @staticmethod
    def _norm_row(key, every) -> Tuple[str, str]:
        key = str(key or "").strip().lower()
        return key, settings.normalize("attack_seconds", every) if str(every).strip() else ""

    def _schedule_refresh(self, *_args) -> None:
        if self._filling or self._refresh_pending:
            return
        self._refresh_pending = True
        self.after_idle(self._refresh_form)

    def _refresh_form(self) -> None:
        """改過哪些、哪裡填錯：標籤、符號、分頁標記、問題列、存檔按鈕一次更新。"""
        self._refresh_pending = False
        values, buffs = self._collect()
        if self.ctl.loaded_ok:
            self._changes = self.ctl.changes(values, buffs)
            self._problems = self.ctl.problems(values, buffs)
        else:
            self._changes, self._problems = [], {}
        changed = set(self._changes)

        for field, lbl in self.labels.items():
            if lbl is None or field == "window_title":
                continue
            spec = settings.SPECS[field]
            dirty = field in changed
            text = ("• " if dirty else "") + spec.label
            if str(lbl.cget("text")) != text:
                lbl.configure(text=text)
            if isinstance(lbl, ttk.Checkbutton):      # ttk 勾選框沒有 foreground 選項
                lbl.configure(style="Dirty.TCheckbutton" if dirty else "Card.TCheckbutton")
            else:
                lbl.configure(foreground=P["dirty"] if dirty else P["text"])
        for field, glyph in self.glyphs.items():
            if glyph is None:
                continue
            self._set_glyph(glyph, self._problems.get(field))
            w = self.inputs[field]
            bad = field in self._problems and self._problems[field].level == "error"
            w.state(["invalid"] if bad else ["!invalid"])
        for i, glyph in enumerate(self.buff_glyphs):
            key = f"buff{i + 1}"
            self._set_glyph(glyph, self._problems.get(key))
            k, e = buffs[i]
            dirty = (i < len(self._buff_base) and self._norm_row(k, e) != self._buff_base[i])
            self.buff_labels[i].configure(text=("•" if dirty else "") + str(i + 1),
                                          foreground=P["dirty"] if dirty else P["muted"])
        used = sum(1 for k, _e in buffs if str(k).strip())
        extra = self.ctl.buff_overflow
        self.buff_count.configure(text=f"已設定 {used}／{settings.BUFF_SLOTS} 組" + (
            f"。這份地圖另外還有 {extra} 組 buff 沒顯示在這裡，存檔不會動到它們"
            if extra else ""))

        # 巡邏點摘要（錄完路線那句提示優先）
        if not getattr(self, "_wp_note", ""):
            p = settings.describe_waypoints(values["waypoints"])
            if "waypoints" in self._problems and self._problems["waypoints"].level == "error":
                p = settings.Problem("error", self._problems["waypoints"].text)
            tone = {"ok": "ok", "info": "muted", "warn": "warn", "error": "bad"}[p.level]
            self.wp_summary.configure(text=_wrap(f"{settings.GLYPHS[p.level]} {p.text}"),
                                      foreground=_fg(tone))
        wp_bad = "waypoints" in self._problems and self._problems["waypoints"].level == "error"
        self.wp_text.configure(highlightbackground=P["invalid_border"] if wp_bad
                               else P["border"])

        # 藥水沒綁鍵就不會喝，門檻填了也沒用——灰掉讓人一眼看懂
        for key_f, pct_f in (("hp_key", "hp_below"), ("mp_key", "mp_below")):
            w = self.inputs[pct_f]
            empty = not str(values.get(key_f, "")).strip()
            if self._can_edit() and pct_f not in self._locked_fields:
                w.state(["disabled"] if empty else ["!disabled"])

        # 分頁標記：× 有錯、• 有改
        for tab, frame in self.tab_frames.items():
            if tab == SETUP_TAB:
                continue
            keys_in = [k for k in list(changed) + list(self._problems)
                       if self._tab_of(k) == tab]
            mark = ""
            if any(self._problems.get(k) and self._problems[k].level == "error"
                   for k in keys_in):
                mark = " ×"
            elif any(k in changed for k in keys_in):
                mark = " •"
            self.nb.tab(frame, text=tab + mark)

        line = live.problem_line(self._problems)
        self.problem_text.configure(text=line)
        (self.problem_btn.grid if line else self.problem_btn.grid_remove)()

        n = len(self._changes)
        if not self.ctl.loaded_ok:
            self.dirty_label.configure(text="設定讀不到", foreground=_fg("bad"))
        elif n:
            self.dirty_label.configure(text=f"● {n} 項未儲存", foreground=P["dirty"])
        else:
            self.dirty_label.configure(text="已儲存", foreground=P["muted"])
        self._update_header_buttons()

    def _tab_of(self, key: str) -> Optional[str]:
        try:
            return settings.tab_of(key)
        except KeyError:
            return None

    def _set_glyph(self, glyph, problem) -> None:
        if problem is None:
            glyph.configure(text="")
        else:
            glyph.configure(text="×" if problem.level == "error" else "！",
                            foreground=_fg("bad" if problem.level == "error" else "warn"))

    def _tooltip(self, key: str) -> str:
        dirty = key in self._changes
        base = self.ctl.baseline_value(key) if dirty else None
        text = settings.tooltip_text(key, base, self._problems.get(key))
        if key in self._locked_fields:
            text = self._locked_fields[key] + "\n" + text
        return text

    def _dirty_tooltip(self) -> str:
        if not self.ctl.loaded_ok:
            return self.ctl.load_error
        if not self._changes:
            return "欄位都跟檔案裡一樣"
        values, buffs = self._collect()
        return "改過：" + self.ctl.describe_changes(values, buffs, limit=8)

    def _can_edit(self) -> bool:
        return self.ctl.can_edit()

    def _update_header_buttons(self) -> None:
        editable = self._can_edit()
        dirty = bool(self._changes)
        self.save_btn.configure(style="Primary.TButton" if dirty and editable else "TButton")
        self.save_btn.state(["!disabled"] if dirty and editable else ["disabled"])
        self.revert_btn.state(["!disabled"] if (dirty or not self.ctl.loaded_ok)
                              and not self.ctl.busy else ["disabled"])
        self.save_as_btn.state(["!disabled"] if editable else ["disabled"])
        self.profile_box.state(["disabled"] if self.ctl.busy else ["!disabled", "readonly"])

    def _apply_lock(self, locked: bool, lock_text: str) -> None:
        """執行中、錄製中、工具開著，或設定讀不到時，表單整片鎖住。

        設定在按下開始時就讀進去了，執行中改了也不會生效；鎖住才不會讓人以為
        改了有用，也擋掉 bot 自己送出的按鍵掉進欄位裡。
        """
        disabled = locked or not self.ctl.loaded_ok
        if disabled != self._locked:
            self._locked = disabled
            for w in self.lockable:
                if isinstance(w, ttk.Combobox):
                    w.state(["disabled"] if disabled else (
                        ["!disabled"] + (["readonly"] if w is not self.inputs.get(
                            "window_title") else ["!readonly"])))
                else:
                    w.state(["disabled"] if disabled else ["!disabled"])
            for field in self._locked_fields:
                if field in self.inputs:
                    self.inputs[field].state(["disabled"])
            self.wp_text.configure(state="disabled" if disabled or
                                   self.wp_mode.get() == "auto" else "normal",
                                   foreground=P["muted"] if disabled or
                                   self.wp_mode.get() == "auto" else P["text"])
            self.rec_btn.state(["disabled"] if (disabled and not self.ctl.recording)
                               else ["!disabled"])
            if not disabled:
                self._refresh_form()          # 藥水門檻的灰掉狀態要重算
        if lock_text:
            self.lock_strip.configure(text=lock_text)
            self.lock_strip.grid()
        else:
            self.lock_strip.grid_remove()
        self._update_header_buttons()

    def _update_footer(self) -> None:
        def rel(p: str) -> str:
            if not p:
                return "—"
            try:
                return os.path.relpath(p)
            except ValueError:               # Windows：地圖檔在別的磁碟（D:\）
                return p
        local = rel(os.path.join(os.path.dirname(self.ctl.config_path), "local.yaml"))
        self.footer_files.configure(
            text=f"儲存寫入　地圖：{rel(self.ctl.profile_path)}　這台電腦：{local}")
        if self.ctl.cfg is not None:
            sf = self.ctl.cfg.safety
            self.footer_keys.configure(
                text=f"遊戲中：{sf.pause_key.upper()} 暫停／繼續・{sf.stop_key.upper()} 停止")

    # ---- 巡邏點 ----

    def _wp_mode_changed(self, refresh: bool = True) -> None:
        auto = self.wp_mode.get() == "auto"
        editable = self._can_edit()
        self.wp_text.configure(state="normal" if (editable and not auto) else "disabled",
                               foreground=P["muted"] if auto else P["text"])
        self._wp_note = ""
        if refresh:
            self._schedule_refresh()

    def _wp_modified(self, _e=None) -> None:
        if self.wp_text.edit_modified():
            self.wp_text.edit_modified(False)
            if not self._filling:
                self._wp_note = ""
                self._schedule_refresh()

    def _record(self) -> None:
        if self.ctl.recording:
            text = self.ctl.stop_record()
            self.rec_btn.configure(text="● 錄製路線")
            if text:
                self.wp_mode.set("list")
                self._wp_mode_changed(refresh=False)
                self.wp_text.delete("1.0", "end")
                self.wp_text.insert("1.0", text)
                # 不清掉的話排在後面的 <<Modified>> 會把下面那句「按 Ctrl+S 儲存」蓋掉
                self.wp_text.edit_modified(False)
                n = len([ln for ln in text.splitlines() if ln.strip()]) \
                    if "\n" in text else len(text.split(","))
                self._wp_note = f"√ 錄到 {n} 個巡邏點——按 Ctrl+S 儲存"
                self.wp_summary.configure(text=self._wp_note, foreground=_fg("ok"))
            else:
                self._wp_note = "× 沒錄到有效座標——先到「準備 › 4 辨識自檢」看小地圖"
                self.wp_summary.configure(text=self._wp_note, foreground=_fg("bad"))
            self._schedule_refresh()
            return
        if self.ctl.start_record():
            self.rec_btn.configure(text="■ 停止錄製")
        else:
            messagebox.showerror("無法錄製", self.ctl.error or "已經在執行中", parent=self)

    # ------------------------------------------------------------------
    # 存檔、還原、切換地圖
    # ------------------------------------------------------------------

    def _save_shortcut(self, _e=None):
        if self._capture is not None:
            return None
        self._save()
        return "break"

    def _save(self) -> bool:
        """儲存目前的修改。成功（或根本沒改）回 True。"""
        self._cancel_capture()
        if not self._can_edit():
            self.bell()
            return False
        values, buffs = self._collect()
        if not self.ctl.changes(values, buffs):
            return True
        if settings.first_error(self.ctl.problems(values, buffs)):
            self.bell()
            self._goto_first_error()
            return False
        if self.ctl.save(values, buffs):
            self._fill()
            self._refresh_steps()
            return True
        if self.ctl.error_code == "TEMPLATE_READONLY":
            ok = self._template_flow(values, buffs)
            if not ok:
                # Config 那一半已經存進 local.yaml 並重新載入：未儲存的數量要重算
                self._refresh_form()
            return ok
        messagebox.showerror("存檔失敗", self.ctl.error, parent=self)
        self._refresh_form()                  # 只存了一半時 controller 已重新載入
        return False

    def _template_flow(self, values, buffs) -> bool:
        """範本不能直接改：問要不要另存成自己的地圖。"""
        title, body = live.DIALOGS["template"]
        if messagebox.askyesno(title, body.format(name=self.ctl.map_name), parent=self):
            return self._save_as(values, buffs)
        return False

    def _save_as(self, values=None, buffs=None) -> bool:
        if not self.ctl.loaded_ok or self.ctl.busy:
            self.bell()
            return False
        if values is None:
            values, buffs = self._collect()
        title, prompt = live.DIALOGS["save_as"]
        name = simpledialog.askstring(title, prompt, parent=self)
        if not name or not name.strip():
            return False
        path = self.ctl.save_as(name.strip(), values, buffs)
        if not path:
            messagebox.showerror("另存失敗", self.ctl.error, parent=self)
            return False
        self._fill()
        self._refresh_choices()
        self._refresh_steps()
        return True

    def _revert(self) -> None:
        n = len(self._changes)
        if n:
            title, body = live.DIALOGS["revert"]
            if not messagebox.askyesno(title, body.format(n=n), parent=self):
                return
        if self.ctl.load():
            self._fill()
        else:
            self._refresh_form()
            self._update_footer()
        self._refresh_steps()

    def _ask_unsaved(self, verb: str) -> Optional[bool]:
        """有改沒存時問一次。回 True = 繼續（存好了或不存）、False = 取消。"""
        if not self._changes or not self.ctl.loaded_ok:
            return True
        values, buffs = self._collect()
        title, body = live.DIALOGS["unsaved"]
        ans = messagebox.askyesnocancel(title, body.format(
            name=self.ctl.map_name, n=len(self._changes),
            fields=self.ctl.describe_changes(values, buffs), verb=verb), parent=self)
        if ans is None:
            return False
        if ans:
            return self._save()
        return True

    def _refresh_choices(self) -> None:
        self._choices = self.ctl.profile_choices()
        self.profile_box.configure(values=[label for label, _p in self._choices])
        cur = os.path.normcase(os.path.abspath(self.ctl.profile_path))
        for label, path in self._choices:
            if os.path.normcase(os.path.abspath(path)) == cur:
                self.profile_var.set(label)
                break

    def _switch_profile(self, _e=None) -> None:
        label = self.profile_var.get()
        path = dict(self._choices).get(label)
        cur = os.path.normcase(os.path.abspath(self.ctl.profile_path))
        if not path or os.path.normcase(os.path.abspath(path)) == cur:
            return
        if not self._ask_unsaved("切換"):
            self._refresh_choices()              # 選單退回原本那張
            return
        self.ctl.switch_profile(path)
        if self.ctl.loaded_ok:
            self._fill()
        else:
            self._refresh_form()
        self._refresh_choices()
        self._refresh_steps()
        self._update_footer()

    def _goto_first_error(self) -> None:
        key = settings.first_error(self._problems)
        if not key:
            return
        tab = self._tab_of(key)
        if tab:
            self.nb.select(self.tab_frames[tab])
        w = self.inputs.get(key)
        if w is not None:
            w.focus_set()

    # ------------------------------------------------------------------
    # 執行
    # ------------------------------------------------------------------

    def _mode_changed(self) -> None:
        self.ctl.dry_choice = bool(self.dry_var.get())

    def _primary(self) -> None:
        cmd = self._view.primary[0] if self._view else "start"
        if cmd == "start":
            self._start()
        elif cmd == "pause":
            self.ctl.pause()
        elif cmd == "resume":
            self.ctl.resume()

    def _start(self) -> None:
        if self.ctl.busy:
            return
        values, buffs = self._collect()
        if settings.first_error(self.ctl.problems(values, buffs)):
            title, _ = live.DIALOGS["not_ready"]
            messagebox.showwarning(title, live.problem_list(self._problems), parent=self)
            self._goto_first_error()
            return
        if not self._save():                  # 先存再跑，避免「改了沒生效」
            return
        dry = bool(self.dry_var.get())
        if not dry:
            blockers = setup.start_blockers(self.ctl.steps(), self.ctl.readiness, True)
            if blockers:
                title, body = live.DIALOGS["start_gate"]
                items = "\n".join("・" + b for b in blockers)
                if not messagebox.askyesno(title, body.format(items=items), parent=self,
                                           icon="warning", default="no"):
                    self.nb.select(self.tab_frames[SETUP_TAB])
                    return
        if not self.ctl.start(dry_run=dry) and not self.ctl.start_error:
            if self.ctl.error_code == "INVALID":
                messagebox.showwarning(live.DIALOGS["not_ready"][0], self.ctl.error,
                                       parent=self)
            elif self.ctl.error:
                messagebox.showerror("無法開始", self.ctl.error, parent=self)

    def _confirm_while_paused(self, key: str) -> bool:
        """執行中跳確認對話框：先暫停再問，按取消就繼續。

        SendInput 送給前景視窗；對話框開著時前景是它，取消後前景是控制台——
        不暫停的話 bot 這段時間的按鍵全打進對話框和控制台，遊戲一個都沒收到。
        繼續會走 runner 的「暫停 -> 繼續」那條路，先把遊戲視窗帶回前景。
        原本就暫停著（使用者或看門狗）的話 pause() 回 False，取消時也不會幫它解除。
        """
        title, body = live.DIALOGS[key]
        paused = self.ctl.pause()
        ok = messagebox.askokcancel(title, body, icon="warning", default="cancel",
                                    parent=self)
        if not ok and paused and self.ctl.running:
            self.ctl.resume()
        return ok

    def _stop(self) -> None:
        if not self.ctl.running:
            return
        if not self._confirm_while_paused("stop"):
            return
        if self.ctl.running:                  # F12 可能在對話框開著時已經停了
            self.ctl.stop()

    def _run_action(self) -> None:
        if self._view and self._view.action:
            self._dispatch(self._view.action[0])

    def _dispatch(self, action: str) -> None:
        handlers = {
            "window": lambda: self._focus_step("window"),
            "calibrate": self._calibrate,
            "nametag": self._grab_nametag,
            "vision": self._check_vision,
            "save_as": self._save_as,
            "notepad": lambda: self.ctl.open_action("notepad"),
            "anomalies": lambda: self.ctl.open_action("anomalies"),
            "report": lambda: self.ctl.open_action("report"),
            "tab:patrol": lambda: self.nb.select(self.tab_frames[settings.BUFF_TAB]),
            "tab:vision": lambda: self.nb.select(self.tab_frames["辨識"]),
            "dry_run": self._dry_run,
            "elevate": self._explain_elevate,
        }
        fn = handlers.get(action)
        if fn:
            fn()

    def _focus_step(self, sid: str) -> None:
        self.nb.select(self.tab_frames[SETUP_TAB])
        if sid == "window":
            self.inputs["window_title"].focus_set()

    def _dry_run(self) -> None:
        self.dry_var.set(True)
        self._mode_changed()
        self._start()

    def _explain_elevate(self) -> None:
        # .py 檔的右鍵選單沒有「以系統管理員身分執行」，要從提權的終端機開
        messagebox.showinfo(
            "以系統管理員身分執行",
            "遊戲是用系統管理員身分開的，Windows 會默默丟掉一般權限程式送給它的按鍵。\n\n"
            "關掉控制台，在開始功能表搜尋「命令提示字元」→ 按右鍵 →"
            "「以系統管理員身分執行」，再輸入：\n\n"
            f"cd /d \"{os.getcwd()}\"\npython gui.py", parent=self)

    # ---- 工具 ----

    def _before_tool(self) -> bool:
        """工具從磁碟讀設定，所以先把畫面上的修改存進去。"""
        if self.ctl.busy:
            messagebox.showwarning("請稍等", self.ctl.error or "執行中，請先停止", parent=self)
            return False
        if not self.ctl.loaded_ok:
            return True     # 地圖設定讀不到時表單是鎖的、沒東西可存；校正、截名牌只要 Config
        return self._save()

    def _calibrate(self) -> None:
        if not self._before_tool():
            return
        title, body = live.DIALOGS["calibrate"]
        messagebox.showinfo(title, body, parent=self)
        if not self.ctl.calibrate():
            messagebox.showwarning("無法校正", self.ctl.error, parent=self)

    def _grab_nametag(self) -> None:
        if not self._before_tool():
            return
        title, body = live.DIALOGS["nametag"]
        messagebox.showinfo(title, body, parent=self)
        if not self.ctl.grab_nametag():
            messagebox.showwarning("無法截圖", self.ctl.error, parent=self)

    def _check_vision(self) -> None:
        if not self._before_tool():
            return
        if not self.ctl.check_vision():
            messagebox.showwarning("無法自檢", self.ctl.error, parent=self)

    # ------------------------------------------------------------------
    # 準備清單
    # ------------------------------------------------------------------

    def _step_action(self, sid: str) -> None:
        for step in self._steps:
            if step.id == sid and step.action:
                self._dispatch(step.action)

    def _refresh_steps(self) -> None:
        self._steps_at = time.monotonic()
        self._steps = self.ctl.steps()
        for step in self._steps:
            w = self.step_rows.get(step.id)
            if w is None:
                continue
            glyph, tone = setup.STEP_ICON[step.state]
            w["icon"].configure(text=glyph, foreground=_fg(tone))
            w["title"].configure(text=f"{step.n}  {step.title}")
            w["detail"].configure(text=_wrap(step.detail))
            btn = w.get("button")
            if btn is not None:
                if step.button:
                    btn.configure(text=step.button,
                                  style="Primary.TButton" if step.primary else "TButton")
                    btn.grid()
                    btn.state(["disabled"] if self.ctl.busy else ["!disabled"])
                else:
                    btn.grid_remove()
        self.nb.tab(self.tab_frames[SETUP_TAB], text=setup.tab_title(self._steps))
        self._refresh_readiness_rows(force=True)

    def _refresh_readiness_rows(self, force: bool = False) -> None:
        ready = self.ctl.readiness
        if ready is self._readiness and not force:
            return
        self._readiness = ready
        text, tone = setup.readiness_summary(ready)
        self.ready_summary.configure(text=live.tk_safe(text), foreground=_fg(tone))
        rows = setup.readiness_rows(ready) if ready is not None else []
        for i, (frame, g, t, b) in enumerate(self.ready_rows):
            if i < len(rows):
                glyph, rtone, rtext, button = rows[i]
                g.configure(text=glyph, foreground=_fg(rtone))
                t.configure(text=_wrap(rtext))
                if button:
                    b.configure(text=button[1], command=lambda a=button[0]: self._dispatch(a))
                    b.grid()
                else:
                    b.grid_remove()
                frame.grid()
            else:
                frame.grid_remove()

    def _fill_window_titles(self) -> None:
        self.inputs["window_title"].configure(values=self.ctl.window_titles())

    def _show_readiness(self) -> None:
        win = tk.Toplevel(self)
        win.title("環境檢查")
        win.configure(background=P["card"])
        win.transient(self)
        text = tk.Text(win, width=80, height=24, wrap="word", relief="flat",
                       background=P["card"], padx=self.px(12), pady=self.px(10))
        text.pack(fill="both", expand=True)
        for tone, line in setup.readiness_lines(self.ctl.readiness):
            text.insert("end", live.tk_safe(line) + "\n", tone)
            text.tag_configure(tone, foreground=_fg(tone))
        if self.ctl.readiness is None:
            text.insert("end", "環境檢查還在跑，等一下再開")
        text.configure(state="disabled")
        ttk.Button(win, text="關閉", command=win.destroy).pack(pady=self.px(8))

    # ------------------------------------------------------------------
    # 辨識自檢結果
    # ------------------------------------------------------------------

    def _show_vision(self) -> None:
        report = self.ctl.vision_result
        if report is None:
            return
        if self._vision_win is not None and self._vision_win.winfo_exists():
            self._vision_win.destroy()
        win = tk.Toplevel(self, background=P["card"])
        self._vision_win = win
        win.title(live.DIALOGS["vision"][0])
        win.transient(self)
        body = tk.Frame(win, background=P["card"], padx=self.px(14), pady=self.px(12))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        try:
            img = tk.PhotoImage(file=report.preview)
        except (tk.TclError, OSError):
            img = None
        pic = tk.Label(body, background=P["card"], text="" if img else "（預覽圖讀不到）")
        if img is not None:
            pic.configure(image=img)
            pic.image = img                      # 只有區域變數抓著的話會被回收
        pic.grid(row=0, column=0, sticky="n", padx=(0, self.px(14)))
        rows = tk.Frame(body, background=P["card"])
        rows.grid(row=0, column=1, sticky="nwe")
        rows.columnconfigure(1, weight=1)
        verdicts = live.verdict_rows(self.ctl.vision_verdicts())
        for i, (glyph, tone, text, button) in enumerate(verdicts):
            tk.Label(rows, text=glyph, background=P["card"], foreground=_fg(tone),
                     font=self.f_bold, width=2, anchor="n").grid(row=i, column=0, sticky="n")
            tk.Label(rows, text=_wrap(text), background=P["card"], anchor="w", justify="left",
                     wraplength=self.px(330)).grid(row=i, column=1, sticky="w",
                                                   pady=self.px(3))
            if button:
                ttk.Button(rows, text=button[1], style="Link.TButton",
                           command=lambda a=button[0]: (win.destroy(), self._dispatch(a))
                           ).grid(row=i, column=2, sticky="e")
        tk.Label(body, text=_wrap(live.DIALOGS["vision"][1]), background=P["card"],
                 foreground=P["muted"], font=self.f_small, anchor="w", justify="left",
                 wraplength=self.px(900)).grid(row=1, column=0, columnspan=2, sticky="we",
                                               pady=(self.px(10), 0))
        btns = tk.Frame(body, background=P["card"])
        btns.grid(row=2, column=0, columnspan=2, sticky="we", pady=(self.px(10), 0))
        btns.columnconfigure(1, weight=1)
        ttk.Button(btns, text="用看圖程式開啟原圖",
                   command=lambda: self.ctl.open_path(report.image)).grid(row=0, column=0)
        ttk.Button(btns, text="不對，重新校正",
                   command=lambda: (win.destroy(), self._calibrate())).grid(
            row=0, column=2, padx=(0, self.px(8)))
        ok = ttk.Button(btns, text="看起來都對 √", style="Primary.TButton",
                        command=lambda: (self.ctl.confirm_vision(), win.destroy()))
        ok.grid(row=0, column=3)
        if any(tone == "bad" for _g, tone, _t, _b in verdicts):
            ok.state(["disabled"])
            Tooltip(ok, lambda: "有 × 的項目要先處理", self.px(240))

    # ------------------------------------------------------------------
    # 每 200ms 一次：把 controller 的狀態貼到畫面上
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        if self._closing:
            return
        try:
            view, lines = self.ctl.poll()
            self._apply_view(view)
            self._append_logs(lines)
            now = time.monotonic()
            if view.generation != self._gen:
                self._gen = view.generation
                self.ctl.refresh_readiness()
                self._refresh_steps()
            elif now - self._steps_at >= STEPS_EVERY:
                self._refresh_steps()
            self._refresh_readiness_rows()
            if view.open_vision:
                self._show_vision()
            if now - self._title_at >= TITLE_EVERY:
                self._title_at = now
                title = live.tk_safe(live.title_text(view, self.ctl.map_name,
                                                     bool(self._changes)))
                if title != self._title:
                    self._title = title
                    self.title(title)
        finally:
            if not self._closing:
                self._tick_job = self.after(REFRESH_MS, self._tick)

    def _apply_view(self, v: live.View) -> None:
        prev, self._view = self._view, v
        fg, bg = look.TONES.get(v.tone, look.TONES["muted"])
        if prev is None or (prev.tone, prev.pill) != (v.tone, v.pill):
            self.pill.configure(text=v.pill, foreground=fg, background=bg)
            self.stripe.configure(background=fg)
        (self.dry_tag.grid if v.dry_tag else self.dry_tag.grid_remove)()
        self.elapsed.configure(text=v.elapsed)
        self.detail.configure(text=_wrap(v.detail))
        if v.action:
            self.action_btn.configure(text=v.action[1])
            self.action_btn.grid()
        else:
            self.action_btn.grid_remove()
        if prev is None or bool(prev.action) != bool(v.action):
            self._rewrap_detail()
        if v.summary:
            self.summary.configure(text=v.summary)
            self.report_btn.state(["!disabled"] if v.report else ["disabled"])
            self.summary_row.grid()
        else:
            self.summary_row.grid_remove()

        cmd, text, enabled = v.primary
        self.primary_btn.configure(text=text, style="Strong.TButton" if cmd == "pause"
                                   else "Primary.TButton")
        self.primary_btn.state(["!disabled"] if enabled else ["disabled"])
        self.stop_btn.state(["!disabled"] if v.can_stop else ["disabled"])
        for rb in self.mode_btns:
            rb.state(["disabled"] if v.settings_locked else ["!disabled"])
        if self.dry_var.get() != self.ctl.dry_choice:
            self.dry_var.set(self.ctl.dry_choice)

        for (bar, pct), m in zip(self.meters, v.meters):
            bar.show(m)
            pct.configure(text=m.text, foreground=_fg(m.tone))
        for lbl, (text, tone) in zip(self.lines, v.lines):
            lbl.configure(text=live.tk_safe(text), foreground=_fg(tone))
        self.frozen.configure(text=v.frozen)

        if self.ctl.recording:
            self.rec_btn.configure(text=f"■ 停止錄製（{self.ctl.record_seconds:.0f} 秒）")
        elif str(self.rec_btn.cget("text")).startswith("■"):
            self.rec_btn.configure(text="● 錄製路線")
        self._apply_lock(v.settings_locked, v.lock_text)

    # ---- 日誌 ----

    def _append_logs(self, lines) -> None:
        shown = [ln for ln in lines if live.visible(ln, self.problems_only.get())]
        if not shown:
            return
        box = self.logbox
        at_end = box.yview()[1] >= 0.999
        box.configure(state="normal")
        for ln in shown:
            tags = ("line",)
            if ln.level in ("warn", "error"):
                tags += (ln.level,)
            elif ln.kind in ("tool", "tick"):
                tags += (ln.kind,)
            box.insert("end", live.tk_safe(ln) + "\n", tags)
        count = int(box.index("end-1c").split(".")[0])
        if count > LOG_CAP:
            box.delete("1.0", f"{count - LOG_CAP + 200}.0")
        box.configure(state="disabled")
        if at_end:
            box.see("end")

    def _rerender_log(self) -> None:
        self.logbox.configure(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.configure(state="disabled")
        self._append_logs(self.ctl.history_lines()[-LOG_CAP:])
        self.logbox.see("end")

    def _ticks_changed(self) -> None:
        self.ctl.keep_ticks = bool(self.ticks_var.get())

    def _copy_log(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.logbox.get("1.0", "end-1c"))

    # ------------------------------------------------------------------
    # 開關視窗
    # ------------------------------------------------------------------

    def _restore_window(self) -> None:
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.minsize(*look.min_size(sw, sh, self.s))
        geo = (self.ctl.state or {}).get("geometry", "")
        if geo and look.fits(geo, winmod.virtual_screen()):
            self.geometry(geo)
        else:
            self.geometry(look.initial_geometry(sw, sh, self.s))
        unfinished = any(s.state != "done" for s in self._steps)
        last = (self.ctl.state or {}).get("last_tab", "")
        tab = SETUP_TAB if unfinished or last not in self.tab_frames else last
        self.nb.select(self.tab_frames[tab])

    def _close(self) -> None:
        if self._closing:                     # 正在等 bot 放鍵時又按了一次關閉
            return
        self._cancel_capture()
        if self.ctl.running:
            if not self._confirm_while_paused("close_busy"):
                return
            self.ctl.stop()
        else:
            if self.ctl.recording:
                # 先收掉錄製（路線填進表單）：錄製中表單是鎖的，下面問要不要存
                # 時選「是」只會嗶一聲，視窗也關不掉
                self._record()
            if not self._ask_unsaved("關閉"):
                return
        try:
            sel = self.nb.select()
            tab = next((t for t, f in self.tab_frames.items() if str(f) == sel), SETUP_TAB)
            self.ctl.remember(geometry=self.geometry(), last_tab=tab)
        except tk.TclError:
            pass
        self._closing = True
        self.pill.configure(text="● 正在停止…")
        deadline = time.monotonic() + CLOSE_WAIT

        # runner 是 daemon 執行緒：馬上 destroy 的話它可能來不及走到 finally 的
        # release_all，方向鍵就一直按在遊戲裡
        def wait():
            if self.ctl.running and time.monotonic() < deadline:
                self.after(100, wait)
            else:
                self.destroy()
        wait()


    def destroy(self) -> None:
        # 還排著的 _tick 在視窗沒了之後觸發會印「invalid command name」
        try:
            self.after_cancel(self._tick_job)
        except (tk.TclError, AttributeError, ValueError):
            pass
        super().destroy()


def main(config="config/default.yaml", profile="config/profiles/example.yaml") -> int:
    from .. import log
    App(Controller(config, profile, logger=log.setup())).mainloop()
    return 0
