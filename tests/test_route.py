"""錄製路線 -> 巡邏點的壓縮。"""
from maplebot.route import (RoutePoint, Sample, compress, coverage,
                            describe, to_yaml_block)


def _walk(xs, y=20, keys=()):
    return [Sample(t=i * 0.125, x=x, y=y, keys=keys) for i, x in enumerate(xs)]


def _sweep(lo, hi, step=2):
    return list(range(lo, hi + 1, step)) + list(range(hi - step, lo - 1, -step))


def test_left_right_patrol_becomes_two_points():
    pts = compress(_walk(_sweep(30, 90)))
    assert [p.x for p in pts] == [90, 30]
    assert all(p.y is None for p in pts)      # 單層地圖不需要 y


def test_repeated_laps_do_not_pile_up_points():
    """走三圈跟走一圈應該錄出同一條路線。"""
    pts = compress(_walk(_sweep(30, 90) * 3))
    assert [p.x for p in pts] == [90, 30]


def test_jitter_while_standing_still_makes_one_point():
    pts = compress(_walk([50, 51, 50, 49, 50, 51, 50]))
    assert len(pts) == 1


def test_frames_without_a_player_dot_are_ignored():
    samples = _walk(_sweep(30, 90))
    samples[3] = Sample(t=0.4, x=None, y=None)
    assert [p.x for p in compress(samples)] == [90, 30]


def test_climbing_records_the_level():
    """小地圖 y 跳一階 = 爬了繩子，這個落點要帶 y。"""
    route = _walk(range(30, 60, 2), y=40) + _walk([60] * 4, y=20) + \
        _walk(range(60, 90, 2), y=20)
    pts = compress(route)
    assert any(p.y == 20 for p in pts)
    assert [p.y for p in pts] == sorted([p.y for p in pts], reverse=True)


def test_jumping_down_is_marked_as_descend():
    route = _walk(range(30, 50, 2), y=20) + \
        _walk([50, 50, 50, 50], y=40, keys=("alt",))
    pts = compress(route, jump_key="alt")
    assert any(p.descend == "jump" for p in pts)


def test_keys_pressed_while_standing_attach_to_that_point():
    route = _walk(range(30, 60, 2)) + _walk([60, 60, 60], keys=("9",))
    pts = compress(route)
    assert pts[-1].keys == ["9"]


def test_arrow_keys_are_not_recorded():
    """方向鍵是「怎麼走到這裡」，執行時由巡邏邏輯自己決定。"""
    route = _walk(range(30, 60, 2), keys=("right",)) + _walk([60], keys=("x",))
    pts = compress(route)
    assert all("right" not in p.keys for p in pts)
    assert pts[-1].keys == ["x"]


def test_short_wobbles_do_not_create_waypoints():
    """走到底前被怪打退兩格不算折返。"""
    pts = compress(_walk([30, 40, 50, 48, 50, 60, 70]), min_span=6)
    assert [p.x for p in pts] == [70]


def test_empty_recording_is_survived():
    assert compress([]) == []
    assert "waypoints: []" in to_yaml_block([])


def test_yaml_block_is_simple_for_a_flat_map():
    out = to_yaml_block([RoutePoint(30), RoutePoint(90)])
    assert out.strip() == "patrol:\n  waypoints: [30, 90]"


def test_yaml_block_expands_when_there_is_more_than_x():
    out = to_yaml_block([RoutePoint(30, y=40), RoutePoint(60, y=20, keys=["9"])])
    assert "- {x: 30, y: 40}" in out
    assert "- {x: 60, y: 20, keys: ['9']}" in out


def test_describe_reads_like_a_route():
    pts = compress(_walk(range(30, 60, 2), y=40) + _walk([60] * 3, y=20)
                   + _walk(range(60, 90, 2), y=20, keys=("9",)))
    text = describe(pts)
    assert "x=" in text and "y=" in text and "→" in text
    assert "9" in text          # 站定按的技能鍵也要看得到


def test_describe_marks_a_jump_down():
    pts = compress(_walk(range(30, 50, 2), y=20) + _walk([50, 50, 50, 50], y=40, keys=("alt",)),
                   jump_key="alt")
    assert "下跳" in describe(pts)


def test_describe_survives_an_empty_recording():
    assert describe([]) == "（沒有錄到有效座標）"


def test_coverage_reports_span_and_levels():
    assert "左右跨距 60px" in coverage([RoutePoint(30), RoutePoint(90)])
    assert "樓層" not in coverage([RoutePoint(30), RoutePoint(90)])
    assert "2 個樓層" in coverage([RoutePoint(30, y=40), RoutePoint(60, y=20)])
    assert coverage([]) == "0 個點"


def test_one_rope_climb_is_one_point_not_a_trail_of_them():
    """爬繩途中 y 是一格一格變的。每一幀都當成新樓層的話，一次爬繩會錄成
    一串中途高度（實測 y=40 爬到 20 錄出 6 個點），執行時就會傻傻地想
    「爬到 y=36」再「爬到 y=32」，每一個都爬不到、每一個都重試。
    """
    route = _walk(range(30, 62, 2), y=40)
    for y in (36, 32, 28, 24):
        route += _walk([60], y=y)          # 還在繩子上，y 一直在動
    route += _walk([60] * 4, y=20)         # 爬到頂、站定
    route += _walk(range(60, 92, 2), y=20)

    pts = compress(route)
    levels = sorted({p.y for p in pts})
    assert levels == [20, 40], describe(pts)
    assert len(pts) == 3, describe(pts)


def test_a_blip_in_the_minimap_reading_is_not_a_floor():
    """小地圖 y 偶爾跳一下不代表換樓層——沒站定就不算。"""
    route = _walk(range(30, 60, 2), y=20)
    route[5] = Sample(t=0.6, x=40, y=44)   # 單幀誤讀
    pts = compress(route)
    assert {p.y for p in pts} == {20}, describe(pts)
