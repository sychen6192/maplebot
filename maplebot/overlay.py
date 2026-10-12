"""把辨識結果疊回畫面上——偵錯視窗與掛機時的即時預覽共用同一份繪圖。

原本這段只長在 tools/debug_view.py 裡，於是「掛機的時候想看框」只能另外
開一個 debug_view：兩個行程各自擷取、各自辨識，看到的東西不保證一樣，
CPU 也多一份。搬到這裡之後，`main.py --preview` 直接畫**主迴圈這一幀**，
所見即所判。

畫在原尺寸畫面上，座標讀值才對得起來；要縮小是顯示端的事。
"""
from typing import Optional

import cv2

GREEN = (0, 255, 0)
RED = (0, 0, 255)
YELLOW = (0, 255, 255)
WHITE = (255, 255, 255)
GRAY = (150, 150, 150)      # 跟隨物（寵物）：偵測到但不攻擊
MAGENTA = (255, 0, 255)     # 角色定位（名牌或組隊紅條）量到的位置
CYAN = (255, 200, 0)        # 攻擊範圍框
ORANGE = (0, 165, 255)      # 效能警示；也是後退距離（keep_away_px）
SKY = (255, 160, 120)       # 追擊範圍（chase_px）
DIM = (90, 90, 90)          # 角色排除範圍（outline_player_box）：框內的偵測當成角色自己
SEARCH = (200, 200, 200)    # 找怪範圍（mob_search_box）

FONT = cv2.FONT_HERSHEY_SIMPLEX


def pct(v) -> str:
    return f"{v:.0%}" if v is not None else "?"


def draw_attack_box(canvas, cfg, profile, screen_xy=None) -> None:
    """把攻擊範圍框畫出來——「range_px 該設多少」用看的比用猜的快。

    短劍砍不到卻一直揮、或是明明構得到卻不打，看這個框跟怪的相對位置就知道。
    """
    from .brain import fsm

    if "playfield" not in cfg.regions:
        return
    fx, fy, fw, fh = cfg.regions["playfield"]
    if screen_xy:                       # 角色定位量到的真實位置（名牌 -> 組隊紅條）
        cx, cy = fx + screen_xy[0], fy + screen_xy[1]
        cv2.drawMarker(canvas, (cx, cy), MAGENTA, cv2.MARKER_CROSS, 22, 2)
        cv2.putText(canvas, "player", (cx + 10, cy - 8), FONT, 0.4, MAGENTA, 1)
    else:
        cx, cy = fx + fw // 2, fy + fh // 2
    s = fsm.attack_scale(fw, profile.attack_auto_scale)
    base = fsm.attack_scale(fw, True)        # Config 的距離一律照 790 基準縮放
    _draw_reach_context(canvas, cfg, profile, (cx, cy), (fx, fy, fw, fh), s, base,
                        located=bool(screen_xy))
    for sk in profile.active_skills():
        rx, ry = int(sk.range_px * s), int(sk.vertical_range_px * s)
        cv2.rectangle(canvas, (cx - rx, cy - ry), (cx + rx, cy + ry), CYAN, 1)
        cv2.putText(canvas, f"{sk.key} range {sk.range_px}x{sk.vertical_range_px}"
                    + (f" (x{s:.1f}={rx})" if abs(s - 1) > 0.05 else ""),
                    (cx - rx + 4, cy - ry + 14), FONT, 0.4, CYAN, 1)


def _draw_reach_context(canvas, cfg, profile, center, playfield, s, base,
                        located: bool) -> None:
    """攻擊框以外、會決定「打不打、追不追、退不退」的幾個範圍。

    它們分散在 Profile 與 Config，單看數字都合理、湊在一起才會互相抵銷
    （例如後退距離比角色排除範圍還小，貼身的怪早就被濾掉、永遠不會退）。
    畫在同一張圖上，一眼就看得出哪個框套住了哪個。doctor 的「射程設定」
    檢查的就是這幾個框的大小關係。
    """
    cx, cy = center
    fx, fy, fw, fh = playfield
    # 角色排除範圍畫在 perception 真的會挖掉的地方：量得到角色就在角色身上；
    # 量不到時只有描邊偵測會照畫面中央挖（Perceiver 自己那道過濾不做）
    if located or cfg.vision.mob_detector == "outline":
        ex, ey = (int(v * base) // 2 for v in cfg.vision.outline_player_box)
        cv2.rectangle(canvas, (cx - ex, cy - ey), (cx + ex, cy + ey), DIM, 1)
        cv2.putText(canvas, "self" if located else "self (center)",
                    (cx - ex + 2, cy + ey - 4), FONT, 0.35, DIM, 1)
    ry = int(profile.active_skills()[0].vertical_range_px * s)
    if profile.keep_away_px > 0:
        kx = int(profile.keep_away_px * s)
        for x in (cx - kx, cx + kx):
            cv2.line(canvas, (x, cy - ry), (x, cy + ry), ORANGE, 1)
        cv2.putText(canvas, f"keep away {profile.keep_away_px}", (cx - kx + 2, cy + ry - 4),
                    FONT, 0.35, ORANGE, 1)
    if profile.chase_px > 0:
        hx = int(profile.chase_px * s)
        cv2.rectangle(canvas, (cx - hx, cy - ry), (cx + hx, cy + ry), SKY, 1)
        cv2.putText(canvas, f"chase {profile.chase_px}", (cx - hx + 4, cy + ry - 4),
                    FONT, 0.4, SKY, 1)
    box = cfg.vision.mob_search_box
    if box:
        # 與 Perceiver._search_roi 同一套裁切：跟著角色、但不超出 playfield
        bw, bh = min(box[0], fw), min(box[1], fh)
        x0 = fx + min(max(cx - fx - bw // 2, 0), fw - bw)
        y0 = fy + min(max(cy - fy - bh // 2, 0), fh - bh)
        cv2.rectangle(canvas, (x0, y0), (x0 + bw, y0 + bh), SEARCH, 1)
        cv2.putText(canvas, "search", (x0 + 4, y0 + bh - 4), FONT, 0.4, SEARCH, 1)


def annotate(frame, state, action, cfg, fps=None, followers=(), profile=None,
             extra: str = ""):
    """把辨識結果畫上去（畫在原尺寸畫面，座標讀值才準）。"""
    canvas = frame.copy()
    if profile is not None:
        draw_attack_box(canvas, cfg, profile, state.screen_xy)

    for name, (x, y, w, h) in cfg.regions.items():
        cv2.rectangle(canvas, (x, y), (x + w, y + h), WHITE, 1)
        cv2.putText(canvas, name, (x, max(y - 3, 10)), FONT, 0.4, WHITE, 1)

    if "minimap" in cfg.regions:
        mx, my = cfg.regions["minimap"][:2]
        if state.minimap_xy:
            px, py = state.minimap_xy
            cv2.circle(canvas, (mx + px, my + py), 4, GREEN, 2)
            cv2.putText(canvas, f"({px},{py})", (mx + px + 6, my + py),
                        FONT, 0.4, GREEN, 1)
        for ox, oy in state.other_players:
            cv2.circle(canvas, (mx + ox, my + oy), 4, RED, 2)

    if "playfield" in cfg.regions:
        fx, fy = cfg.regions["playfield"][:2]
        for mob in state.mobs:
            x1 = fx + mob.cx - mob.w // 2
            y1 = fy + mob.cy - mob.h // 2
            cv2.rectangle(canvas, (x1, y1), (x1 + mob.w, y1 + mob.h), YELLOW, 2)
            cv2.putText(canvas, f"{mob.name} {mob.score:.2f}", (x1, max(y1 - 4, 10)),
                        FONT, 0.4, YELLOW, 1)
        # 跟著角色跑的（寵物）畫灰框，代表看得到但不會打
        for mob in followers:
            x1 = fx + mob.cx - mob.w // 2
            y1 = fy + mob.cy - mob.h // 2
            cv2.rectangle(canvas, (x1, y1), (x1 + mob.w, y1 + mob.h), GRAY, 1)
            cv2.putText(canvas, "follower", (x1, max(y1 - 4, 10)), FONT, 0.4, GRAY, 1)

    header = (f"HP {pct(state.hp)} | MP {pct(state.mp)} | EXP {pct(state.exp)} | "
              f"mobs {len(state.mobs)}")
    if fps is not None:
        header += f" | {fps:.0f} FPS"
    header += f" | -> {type(action).__name__}"
    cv2.putText(canvas, header, (8, 20), FONT, 0.55, GREEN, 2)
    if extra:
        cv2.putText(canvas, extra, (8, 42), FONT, 0.45, ORANGE, 1)
    return canvas


class Preview:
    """掛機時的即時預覽視窗。

    只在使用者明講 `--preview` 時才開：imshow + waitKey 每幀要幾毫秒到十幾
    毫秒，掛整晚不需要付這個成本。看不到 cv2 顯示後端（headless）時自動
    關掉自己，不要因為「想看框」而讓整支 bot 掛掉。
    """

    WINDOW = "maplebot preview"
    MAX_W = 1280

    def __init__(self, cfg, profile, logger=None, max_width: int = MAX_W):
        self.cfg = cfg
        self.profile = profile
        self.log = logger
        self.max_width = max_width
        self.enabled = True

    def show(self, frame, state, action, fps=None, followers=(), extra="") -> None:
        if not self.enabled:
            return
        try:
            canvas = annotate(frame, state, action, self.cfg, fps=fps,
                              followers=followers, profile=self.profile, extra=extra)
            h, w = canvas.shape[:2]
            if w > self.max_width:
                canvas = cv2.resize(canvas,
                                    (self.max_width, int(h * self.max_width / w)))
            cv2.imshow(self.WINDOW, canvas)
            cv2.waitKey(1)
        except cv2.error as e:
            self.enabled = False
            if self.log:
                self.log.warning("預覽視窗開不起來，已關閉預覽（bot 繼續跑）: %s", e)

    def close(self) -> None:
        if not self.enabled:
            return
        try:
            cv2.destroyWindow(self.WINDOW)
        except cv2.error:
            pass


def display_size(capture_size, max_width: int = Preview.MAX_W):
    """大解析度畫面縮到看得下的尺寸。"""
    w, h = capture_size
    if w <= max_width:
        return w, h
    return max_width, int(h * max_width / w)
