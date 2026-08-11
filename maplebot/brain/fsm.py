"""決策核心：純函式的優先權狀態機。

decide() 不碰鍵盤、不碰螢幕、不看時鐘（now 由外部傳入），
所以整個打怪邏輯可以用單元測試完整驗證。

優先權（高到低）：
  1. 視覺異常          -> Wait（交給 runner 的 watchdog 計時）
  2. HP 低於危險線      -> Panic（停止程式並截圖；可設定先按回城卷）
  3. HP/MP 低於補藥線   -> DrinkPotion
  4. 小地圖出現其他玩家  -> Wait（禮貌暫停，pause_when_players 可關）
  5. Buff 到期          -> CastBuff（垂直移動途中跳過）
  6. 攻擊範圍內有怪      -> Attack（多技能時依序挑第一個放得出來又划算的；
                          directional 先轉向 / aoe 原地放；垂直移動途中跳過。
                          打很久位置卻沒變會強制讓路給巡邏，見 _attack_stalled）
  6b. 看得到怪但構不到   -> Chase 走過去（chase_px 設 0 = 關掉）
  7. 自動巡邏未校正      -> Probe（waypoints: auto 時左右撞牆量出可走範圍）
  8. 站得比巡邏層高      -> Climb 往下跳（descend: jump 時**不必**先對準 x：
                          從哪裡跳下去都會落到下面那層，而站上小平台之後
                          常常也走不動，等 x 對準等於永遠不會處理）
  9. 巡邏中卡住          -> Escape（跳躍 + 換方向，參考 MapleStoryAutoLevelUp）
 10. x 到位但 y 沒到     -> Climb（爬繩上樓 / 下繩）
 11. 其他               -> Move 往下一個巡邏點；抵達時執行該點的 keys

在繩子上按方向鍵或技能鍵會掉下來，所以第 5、6 項在垂直移動途中會讓路——
補血補魔不受影響（那是保命）。

楓谷的鏡頭永遠跟著角色，所以「角色在 playfield 的位置」直接用
playfield 中心近似，怪物距離就是與中心的距離。

小地圖座標的 y 是往下增加的，所以「往上爬」是 y 變小。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

from ..config import REFERENCE_WIDTH, AppCfg, PatrolCfg, Profile, Waypoint
from .state import GameState


@dataclass
class Wait:
    reason: str
    seconds: float = 0.4


@dataclass
class Panic:
    reason: str
    # 停機前是否按 profile.panic_return_key 回城。HP 見底要（人可能回不來），
    # 但設定/校正錯誤不要——那只是燒掉一張回城卷。
    return_home: bool = True


@dataclass
class DrinkPotion:
    kind: str
    key: str


@dataclass
class CastBuff:
    index: int
    key: str
    cast_seconds: float


@dataclass
class Attack:
    direction: int  # -1 左 / 1 右
    key: str
    cast_seconds: float
    repeat: int
    aoe: bool = False
    index: int = 0        # profile.active_skills() 裡的序號，用來記各自的冷卻


@dataclass
class Move:
    direction: int
    seconds: float
    target_x: int


@dataclass
class Loot:
    """打完怪撿掉落物。"""
    key: str
    taps: int


@dataclass
class RunKeys:
    """抵達巡邏點時要敲的按鍵序列（跳上平台、放置型技能等）。"""
    keys: List[str]


@dataclass
class Chase:
    """走向怪（追擊）或走離怪（拉開距離）。

    追擊：看得到怪但打不到就往牠走。沒有這個的話，只要怪不在攻擊框裡就會
    照原定巡邏路線走掉——畫面上明明站著五隻卻一隻都沒打。

    拉開距離：遠程職業站樁輸出，怪貼到臉上就該退開再打（近戰不需要，
    keep_away_px 設 0 關掉）。兩者都只往水平方向走、只看同一層的怪。
    """
    direction: int
    seconds: float
    away: bool = False


@dataclass
class Escape:
    """卡住脫困：往 direction 跳一下。"""
    direction: int
    jump_key: str


@dataclass
class Probe:
    """自動巡邏校正：往 direction 走一小步，試探地圖邊界在哪。"""
    direction: int
    seconds: float


@dataclass
class Climb:
    """垂直移動：direction=-1 爬繩上樓、direction=1 下降。

    jump_key 非空 = 按住方向鍵再跳（下跳平台）；空的話就是抓著繩子上下。
    """
    direction: int
    key: str
    seconds: float
    jump_key: str = ""


Action = Union[Wait, Panic, DrinkPotion, CastBuff, Attack, Move, RunKeys, Escape, Loot,
               Probe, Climb, Chase]


@dataclass
class AutoPatrol:
    """waypoints: auto 的探邊進度（撞到左右兩邊的牆就能算出巡邏點）。"""
    direction: int = 1
    prev_x: Optional[int] = None
    stall_time: float = 0.0             # 累積多久沒有前進（不含戰鬥中斷）
    last_probe_at: Optional[float] = None
    attempts: int = 0                   # 量到不合理範圍後重試了幾次
    left: Optional[int] = None
    right: Optional[int] = None
    waypoints: Optional[List[Waypoint]] = None


@dataclass
class Runtime:
    """跨 tick 的決策記憶：冷卻時間、巡邏進度、卡住偵測、垂直移動進度。"""
    wp_index: int = 0
    last_buff: Dict[int, float] = field(default_factory=dict)
    last_potion: Dict[str, float] = field(default_factory=dict)
    last_attack: float = 0.0
    low_hp_streak: int = 0              # 連續幾幀讀到低於危險線
    low_hp_since: Optional[float] = None  # 這一段低血是什麼時候開始的
    last_skill: Dict[int, float] = field(default_factory=dict)   # 每個技能各自的冷卻
    last_loot: float = 0.0
    stuck_pos: Optional[Tuple[int, int]] = None
    stuck_since: float = 0.0
    escape_direction: int = 1
    attack_pos: Optional[Tuple[int, int]] = None   # 這一串連續攻擊的起點
    attack_since: float = 0.0
    attack_break_until: float = 0.0     # 強制讓路給巡邏到這個時間
    attack_breaks: int = 0              # 觸發過幾次（runner 用來提示使用者）
    auto: AutoPatrol = field(default_factory=AutoPatrol)
    climbing: bool = False              # 正在垂直移動 -> 讓路給攻擊與 buff
    climb_prev_y: Optional[int] = None
    climb_stalls: int = 0
    climb_retries: int = 0

    def note_buff(self, index: int, now: float) -> None:
        self.last_buff[index] = now

    def note_potion(self, kind: str, now: float) -> None:
        self.last_potion[kind] = now

    def note_skill(self, index: int, now: float) -> None:
        self.last_skill[index] = now
        self.last_attack = now

    def pause_climb(self) -> None:
        """離開繩子去重新對位：清掉這一趟的 y 觀測，但保留重試次數，
        免得「爬不動 -> 脫困 -> 位置跑掉 -> 重新對位」把計數歸零變成無限迴圈。"""
        self.climbing = False
        self.climb_prev_y = None
        self.climb_stalls = 0

    def next_waypoint(self, total: int) -> None:
        self.wp_index = (self.wp_index + 1) % max(total, 1)
        self.stuck_pos = None
        self.pause_climb()
        self.climb_retries = 0


def attack_scale(playfield_width: int, enabled: bool = True) -> float:
    """攻擊距離的解析度倍率。

    range_px 是**畫面**像素，但同一個世界距離在大視窗會佔更多像素：1920 視窗
    的 320px 只等於 800x600 時代的 133px。不縮放的話，同一個 profile 換個
    視窗大小攻擊範圍就整個變了（面積門檻也踩過同一個坑）。
    """
    if not enabled or playfield_width <= 0:
        return 1.0
    return playfield_width / REFERENCE_WIDTH


def _in_attack_box(mob, center_x: int, center_y: int,
                   range_px: float, vertical_range_px: float) -> bool:
    """怪的**身體**有沒有碰到攻擊範圍框。

    原本比的是怪的中心點，體型大的怪（中心在框外、身體壓在框裡）會被當成
    打不到而放過——實際上打得到。改成框對框相交，參考 MapleStoryAutoLevelUp
    用交集面積判定的作法（他們比的是重疊面積，我們只要有碰到就算：
    這裡的框本來就是「攻擊得到的範圍」，碰到就是打得到）。
    """
    return (abs(mob.cx - center_x) <= range_px + mob.w // 2
            and abs(mob.cy - center_y) <= vertical_range_px + mob.h // 2)


def _mp_below(state: GameState, threshold: float) -> bool:
    """MP 低於門檻。讀不到 MP 時回 False——寧可照常施放，也不要因為辨識
    失敗就整個停擺。"""
    return threshold > 0 and state.mp is not None and state.mp < threshold


def _potion_due(rt: Runtime, kind: str, cooldown: float, now: float) -> bool:
    return now - rt.last_potion.get(kind, 0.0) >= cooldown


def resolve_waypoint_x(wp: Waypoint, minimap_size: Optional[Tuple[int, int]]) -> int:
    """<=1 的值代表佔小地圖寬度的比例（參考 auto-maple 的相對座標），
    小地圖尺寸改變時巡邏點不用重寫。"""
    if wp.x <= 1.0 and minimap_size:
        return int(round(wp.x * minimap_size[0]))
    return int(round(wp.x))


def resolve_waypoint_y(wp: Waypoint, minimap_size: Optional[Tuple[int, int]]) -> Optional[int]:
    """同 resolve_waypoint_x，但比例是佔小地圖**高度**。None = 這個點不管 y。"""
    if wp.y is None:
        return None
    if wp.y <= 1.0 and minimap_size:
        return int(round(wp.y * minimap_size[1]))
    return int(round(wp.y))


def _bounds_to_waypoints(left: int, right: int, margin: int) -> List[Waypoint]:
    """把量到的左右邊界往內縮，避免巡邏點正好貼在牆上一直判定卡住。"""
    span = right - left
    inset = min(margin, max(span // 4, 0))
    return [Waypoint(x=float(left + inset)), Waypoint(x=float(right - inset))]


def _auto_patrol(rt: Runtime, player: Tuple[int, int], pat: PatrolCfg,
                 now: float) -> Optional[Action]:
    """waypoints: auto —— 往一個方向一直試探到走不動（撞牆），兩邊都量到後
    生成巡邏點。回傳 None 代表校正已完成，可以照正常巡邏走。

    撞牆判定用「累積多久沒有前進」而不是「連續幾個 tick 沒動」：校正途中
    一定會被打怪插隊，兩次探邊之間可能隔了好幾秒，角色被怪推來推去後回到
    原位就會被誤判成撞牆。兩次探邊的間隔會被上限截掉，所以戰鬥耗掉的時間
    不會算進停滯計時。
    """
    ap = rt.auto
    if ap.waypoints is not None:
        return None

    x = player[0]
    # 只計入「真的在探邊」的時間；戰鬥造成的長間隔截到單次探邊的兩倍為止
    if ap.last_probe_at is not None:
        ap.stall_time += min(now - ap.last_probe_at, pat.probe_seconds * 2)
    ap.last_probe_at = now
    if ap.prev_x is None or abs(x - ap.prev_x) > pat.probe_stall_px:
        ap.prev_x = x            # 有前進 -> 重新計時
        ap.stall_time = 0.0

    if ap.stall_time < pat.probe_stall_seconds:
        return Probe(ap.direction, pat.probe_seconds)

    # 走不動了 -> 這一側到底了
    if ap.direction > 0:
        ap.right = x
    else:
        ap.left = x
    ap.stall_time = 0.0
    ap.prev_x = None
    ap.last_probe_at = None

    if ap.left is None or ap.right is None:
        ap.direction *= -1
        return Probe(ap.direction, pat.probe_seconds)

    span = ap.right - ap.left
    if span >= pat.probe_min_span_px:
        ap.waypoints = _bounds_to_waypoints(ap.left, ap.right, pat.probe_margin_px)
        return None

    # 量到的範圍不合理。多半是被打怪干擾，重來一次通常就正常了
    ap.attempts += 1
    ap.left = ap.right = None
    if ap.attempts <= pat.probe_retries:
        ap.direction *= -1
        return Probe(ap.direction, pat.probe_seconds)
    return Panic(
        f"自動巡邏校正失敗：重試 {pat.probe_retries} 次，"
        f"量到的可走範圍都只有 {span}px（低於 "
        f"patrol.probe_min_span_px={pat.probe_min_span_px}）。"
        "請確認方向鍵有送進遊戲（遊戲用系統管理員跑的話終端機也要）、"
        "小地圖 ROI 是否正確，或改用手動 patrol.waypoints",
        return_home=False)


def _climb(rt: Runtime, player: Tuple[int, int], pat: PatrolCfg, wp: Waypoint,
           dy: int, total_waypoints: int) -> Action:
    """閉迴路垂直移動：每一步都回頭看小地圖 y 有沒有真的變。

    沒抓到繩子、爬一半被怪打掉，y 就會停住 —— 這時先脫困微調位置重新對繩，
    連續失敗超過 climb_retries 就放棄這個巡邏點，免得永遠卡在繩子前面。
    """
    y = player[1]
    if rt.climb_prev_y is not None and abs(y - rt.climb_prev_y) <= pat.climb_stall_px:
        rt.climb_stalls += 1
    else:
        rt.climb_stalls = 0
    rt.climb_prev_y = y

    if rt.climb_stalls >= pat.climb_stalls:
        rt.pause_climb()
        rt.climb_retries += 1
        if rt.climb_retries > pat.climb_retries:
            rt.next_waypoint(total_waypoints)
            return Wait(f"垂直移動連續失敗（已重試 {pat.climb_retries} 次），"
                        "放棄這個巡邏點", seconds=0.3)
        rt.escape_direction *= -1
        return Escape(rt.escape_direction, pat.jump_key)

    rt.climbing = True
    if dy < 0:                       # 小地圖 y 變小 = 往上，只能靠繩子
        return Climb(-1, pat.climb_up_key, pat.climb_seconds)
    jump_key = pat.jump_key if wp.descend == "jump" else ""
    return Climb(1, pat.climb_down_key, pat.climb_seconds, jump_key=jump_key)


def _attack_stalled(rt: Runtime, player: Tuple[int, int], now: float,
                    stuck_px: int, limit: float) -> bool:
    """一直在打、角色卻一步都沒移動。

    正常打怪會有進展：怪死了就沒目標、角色就往下一個巡邏點走。打了很久位置
    完全沒變，代表這個目標打不死也不會離開攻擊範圍——最常見就是寵物被當成
    怪（牠永遠跟在旁邊），其次是隔著地形打不到的怪。再打下去只會永遠攻擊、
    永遠不巡邏、永遠沒經驗，所以先強制讓路給巡邏。走一段路也剛好讓
    vision/follower.py 拿到判別寵物需要的移動樣本。
    """
    if limit <= 0:
        return False
    if rt.attack_pos is None or \
            abs(player[0] - rt.attack_pos[0]) + abs(player[1] - rt.attack_pos[1]) > stuck_px:
        rt.attack_pos = player
        rt.attack_since = now
        return False
    return now - rt.attack_since >= limit


def _check_stuck(rt: Runtime, player: Tuple[int, int], now: float,
                 stuck_px: int, stuck_seconds: float) -> bool:
    """巡邏移動中位置長時間沒變就視為卡住（地形卡死、被彈回）。"""
    if rt.stuck_pos is None or \
            abs(player[0] - rt.stuck_pos[0]) + abs(player[1] - rt.stuck_pos[1]) > stuck_px:
        rt.stuck_pos = player
        rt.stuck_since = now
        return False
    if now - rt.stuck_since >= stuck_seconds:
        rt.stuck_since = now  # 重新計時，避免連續觸發
        return True
    return False


def decide(state: GameState, cfg: AppCfg, profile: Profile, rt: Runtime,
           now: float, playfield_center: Tuple[int, int]) -> Action:
    if not state.vision_ok:
        return Wait("讀不到畫面狀態（HP 條或小地圖玩家點）")

    assert state.hp is not None and state.player is not None
    # 攻擊範圍要以**角色**為中心，不是畫面中心：鏡頭有跟隨延遲、走到地圖邊緣
    # 還會卡住，實測角色可以偏離畫面中心 200px 以上。量不到就退回畫面中心
    center_x, center_y = state.player_screen or playfield_center
    pat = profile.patrol

    # 單一幀讀到低血就停機太危險——血條被特效蓋住、擷取抖一下都會誤判。
    # 真實血量不會一幀掉到底，所以要求連續幾幀都讀到才算數。
    #
    # 但「低血就停機」本身也是個陷阱：停機之後角色**站在原地繼續被打**，
    # 掛整晚的結局就是屍體（實測掛整晚就是這樣死的）。所以血量
    # 見底時要先灌藥搶救，連續撐了 critical_hp_seconds 還是拉不回來，
    # 才承認救不了而停機——那時停機才真的是止血。
    if state.hp <= cfg.safety.critical_hp_ratio:
        rt.low_hp_streak += 1
        if rt.low_hp_since is None:
            rt.low_hp_since = now
        long_enough = now - rt.low_hp_since >= cfg.safety.critical_hp_seconds
        if rt.low_hp_streak >= cfg.safety.critical_hp_frames and long_enough:
            return Panic(f"HP 剩 {state.hp:.0%}，連續 {now - rt.low_hp_since:.0f} 秒"
                         f"低於危險線 {cfg.safety.critical_hp_ratio:.0%}，補血追不上")
    else:
        rt.low_hp_streak = 0
        rt.low_hp_since = None

    # 緊急回血：血更低的時候改按另一個鍵（大瓶／全補藥）。
    # 一般補血一瓶只回一點點——實測 530 血的角色一瓶回 44，被圍住時
    # 每秒一瓶追不上掉血速度，就會一路掉到危險線停機。
    urgent = profile.potions.get("hp_emergency")
    if urgent and urgent.key and state.hp < urgent.below_ratio \
            and _potion_due(rt, "hp_emergency", urgent.cooldown, now):
        return DrinkPotion("hp_emergency", urgent.key)

    hp_pot = profile.potions.get("hp")
    if hp_pot and state.hp < hp_pot.below_ratio and _potion_due(rt, "hp", hp_pot.cooldown, now):
        return DrinkPotion("hp", hp_pot.key)

    mp_pot = profile.potions.get("mp")
    if mp_pot and state.mp is not None and state.mp < mp_pot.below_ratio \
            and _potion_due(rt, "mp", mp_pot.cooldown, now):
        return DrinkPotion("mp", mp_pot.key)

    if cfg.safety.pause_when_players and state.others:
        return Wait(f"小地圖出現 {len(state.others)} 位其他玩家，暫停動作", seconds=1.0)

    # 掛在繩子上時方向鍵和技能鍵都會讓角色掉下來，所以垂直移動途中不 buff 不打怪
    if not rt.climbing:
        for i, buff in enumerate(profile.buffs):
            if not buff.key or now - rt.last_buff.get(i, 0.0) < buff.every:
                continue
            if _mp_below(state, buff.min_mp):
                continue      # MP 不夠，等回魔再補，別空按
            return CastBuff(i, buff.key, buff.cast_seconds)

        # 依 profile 的順序挑第一個「放得出來又划算」的技能：
        # 冷卻好了、MP 夠、而且範圍內的怪數達到 min_mobs
        #（大絕設 min_mobs=3 就不會浪費在單隻怪身上）。
        mobs_near = False        # 任一技能範圍內有怪 -> 先打完再撿東西
        may_attack = now >= rt.attack_break_until
        s = attack_scale(playfield_center[0] * 2, profile.attack_auto_scale)
        for i, sk in enumerate(profile.active_skills()):
            in_range = [
                m for m in state.mobs
                if _in_attack_box(m, center_x, center_y,
                                  sk.range_px * s, sk.vertical_range_px * s)
            ]
            if in_range:
                mobs_near = True
            if not may_attack:
                continue          # 打太久沒進展，這幾秒先讓路給巡邏
            if len(in_range) < max(sk.min_mobs, 1):
                continue
            if now - rt.last_skill.get(i, 0.0) < sk.cooldown:
                continue
            # MP 不夠就換下一個技能；都放不出來就繼續巡邏等回魔，不站著空揮
            if _mp_below(state, sk.min_mp):
                continue
            if _attack_stalled(rt, state.player, now, pat.stuck_px,
                               cfg.safety.attack_stall_seconds):
                rt.attack_break_until = now + cfg.safety.attack_break_seconds
                rt.attack_breaks += 1
                rt.attack_pos = None
                break             # 跳出攻擊，往下走巡邏
            nearest = min(in_range, key=lambda m: abs(m.cx - center_x))
            # 遠程職業被貼臉時先退開再打（近戰把 keep_away_px 設 0 關掉）。
            # 退一步下個 tick 就打得到了，等於邊退邊射。
            if profile.keep_away_px > 0 and \
                    abs(nearest.cx - center_x) < profile.keep_away_px * s:
                return Chase(-1 if nearest.cx >= center_x else 1,
                             pat.max_step_seconds, away=True)
            direction = 1 if nearest.cx >= center_x else -1
            return Attack(direction, sk.key, sk.cast_seconds, sk.repeat,
                          aoe=(sk.type == "aoe"), index=i)

        if not mobs_near:
            rt.attack_pos = None   # 沒目標了，連續攻擊的計時重來

        # 清完場才撿：範圍內還有怪就先打，撿東西時被圍毆不划算
        loot = profile.loot
        if loot.key and not mobs_near and now - rt.last_loot >= loot.every \
                and (loot.after_combat <= 0
                     or now - rt.last_attack <= loot.after_combat):
            rt.last_loot = now
            return Loot(loot.key, max(loot.taps, 1))

        # 看得到怪但構不到 -> 走過去，不要照原定路線走掉。
        # 只追同一層（用主技能的垂直範圍當判準）：樓上樓下的怪追過去也打不到，
        # 追了反而會被拉離巡邏路線。撿東西排在前面，免得追下一隻就把掉落物丟了。
        if not mobs_near and profile.chase_px > 0 and now >= rt.attack_break_until:
            sk = profile.active_skills()[0]
            reach = profile.chase_px * s
            vertical = sk.vertical_range_px * s
            near = [m for m in state.mobs
                    if abs(m.cx - center_x) <= reach + m.w // 2
                    and abs(m.cy - center_y) <= vertical + m.h // 2]
            if near:
                target = min(near, key=lambda m: abs(m.cx - center_x))
                return Chase(1 if target.cx >= center_x else -1,
                             pat.max_step_seconds)

    waypoints = pat.waypoints
    if pat.auto:
        probing = _auto_patrol(rt, state.player, pat, now)
        if probing is not None:
            return probing
        waypoints = rt.auto.waypoints or []
    if not waypoints:
        return Wait("沒有可用的巡邏點")

    wp = waypoints[rt.wp_index % len(waypoints)]
    target = resolve_waypoint_x(wp, state.minimap_size)
    dist = target - state.player[0]
    target_y = resolve_waypoint_y(wp, state.minimap_size)

    # 站得比巡邏層高的時候，**先下來再說**，不用等 x 對準。
    # 脫困跳躍很容易把角色送上路邊的小平台（實測是地圖中間那間小木屋的屋頂），
    # 站在上面離地面的怪 250px，打不到也撿不到，就只是在空揮。那種地方通常
    # 也走不了幾步，等「x 對準」才處理等於永遠不會處理。
    # 往上爬則相反：一定要先站到繩子前面，所以留在 x 對準之後。
    # 只對 descend: jump 這樣做——抓繩下降本來就得站在繩子上。
    if target_y is not None and wp.descend == "jump":
        drop = target_y - state.player[1]        # 小地圖 y 往下變大
        if drop > pat.y_tolerance:
            return _climb(rt, state.player, pat, wp, drop, len(waypoints))

    if abs(dist) > pat.tolerance:
        # x 還沒對準（也可能是爬繩途中被打掉、位置跑掉了）-> 先走回去
        rt.pause_climb()
        if _check_stuck(rt, state.player, now, pat.stuck_px, pat.stuck_seconds):
            rt.escape_direction *= -1
            return Escape(rt.escape_direction, pat.jump_key)
        seconds = max(min(abs(dist) * pat.step_seconds_per_px, pat.max_step_seconds), 0.08)
        return Move(1 if dist > 0 else -1, seconds, target)

    if target_y is not None:
        dy = target_y - state.player[1]
        if abs(dy) > pat.y_tolerance:
            return _climb(rt, state.player, pat, wp, dy, len(waypoints))

    rt.next_waypoint(len(waypoints))
    if wp.keys:
        return RunKeys(list(wp.keys))
    return Wait("抵達巡邏點，切換下一個", seconds=0.2)
