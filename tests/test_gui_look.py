"""控制台的配色、字型與尺寸換算（maplebot/gui/look.py）。"""
import re

from maplebot.gui import live, look


def test_pick_font_prefers_jhenghei_ui():
    fams = ["Arial", "Microsoft JhengHei", "Microsoft JhengHei UI", "Noto Sans TC"]
    assert look.pick_font(fams) == "Microsoft JhengHei UI"
    assert look.pick_font(["Noto Sans TC", "DejaVu Sans"]) == "Noto Sans TC"
    assert look.pick_font(["Arial", "Consolas"]) is None


def test_scale():
    assert look.scale(10, 1.5) == 15
    assert look.scale(12, 1.0) == 12
    assert look.scale(7, 1.25) == 9


def _rect(geometry):
    w, h, x, y = (int(v) for v in re.match(r"(\d+)x(\d+)\+(\d+)\+(\d+)$", geometry).groups())
    return w, h, x, y


def test_initial_geometry_fits_a_150_percent_laptop():
    # 1080p 開 150% 只剩約 1280x672 邏輯 px
    g = look.initial_geometry(1280, 672, 1.0)
    w, h, x, y = _rect(g)
    assert x + w <= 1280 and y + h <= 672
    assert look.fits(g, (0, 0, 1280, 672))
    assert _rect(look.initial_geometry(1920, 1080, 1.0))[:2] == (1100, 700)
    assert _rect(look.initial_geometry(3840, 2160, 2.0))[:2] == (2200, 1400)


def test_min_size_never_exceeds_the_screen():
    assert look.min_size(1920, 1080, 1.0) == (900, 600)
    assert look.min_size(1280, 560, 1.0) == (900, 560)


def test_fits_rejects_a_window_on_an_unplugged_monitor():
    vscreen = (0, 0, 1920, 1080)
    assert look.fits("1100x700+100+100", vscreen)
    assert look.fits("1936x1056+-8+-8", vscreen)        # Windows 的最大化會往外多 8px
    assert not look.fits("1100x700+2000+100", vscreen)  # 副螢幕拔掉了
    assert not look.fits("1100x700+-1500+100", vscreen)
    assert look.fits("1100x700+-1500+100", (-1920, 0, 3840, 1080))
    assert not look.fits("", vscreen)
    assert not look.fits("1100x700-0-0", vscreen)


def test_palette_has_every_tone_the_views_use():
    assert set(look.TONES) >= {"ok", "warn", "bad", "info", "tool", "muted", "plain"}
    assert set(look.METERS) == {"hp", "mp", "exp"}
    assert set(look.MARKS) == {"potion", "critical"}
    assert set(look.SCOPES) == {"profile", "config"}
    for color in list(look.PALETTE.values()) + [c for pair in look.TONES.values() for c in pair]:
        assert re.fullmatch(r"#[0-9A-F]{6}", color), color
    for label, _fg, _bg in look.SCOPES.values():
        assert live.symbols_ok(label)
