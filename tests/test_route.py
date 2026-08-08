"""錄製路線 -> 巡邏點的壓縮。"""
from maplebot.route import RoutePoint, Sample, compress, to_yaml_block


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
        _walk([50, 50], y=40, keys=("alt",))
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
