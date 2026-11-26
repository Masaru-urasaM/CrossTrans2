"""
Tests for src/ui/theme/tokens.py and contrast.py (TODO.md U3a, DoD2).

The palettes are the design's promise of legibility: every text pair 4.5:1 and every boundary,
focus ring and accent 3:1, in both themes. These tests are that promise, so a colour tweak that
breaks it fails here before anyone sees it.
"""
import re

import pytest

from src.ui.theme import contrast, tokens

HEX = re.compile(r'^#[0-9A-Fa-f]{6}$')
PALETTES = list(tokens.PALETTES.values())


def ids(palette):
    return next(name for name, value in tokens.PALETTES.items() if value is palette)


class TestContrastArithmetic:
    def test_black_on_white_is_21_to_1(self):
        assert contrast.contrast_ratio('#000000', '#ffffff') == pytest.approx(21.0)

    def test_the_order_does_not_matter(self):
        assert contrast.contrast_ratio('#0F6CBD', '#FFFFFF') == contrast.contrast_ratio('#FFFFFF', '#0F6CBD')

    def test_a_colour_against_itself_is_1_to_1(self):
        assert contrast.contrast_ratio('#123456', '#123456') == pytest.approx(1.0)

    def test_rgb_tuples_are_accepted(self):
        assert contrast.contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0)

    def test_short_hex_is_expanded_and_garbage_refused(self):
        assert contrast.hex_to_rgb('#abc') == (170, 187, 204)
        with pytest.raises(ValueError):
            contrast.hex_to_rgb('#12345')

    def test_todo_md_measured_the_old_translate_button_at_2_45(self):
        assert contrast.contrast_ratio('#ffffff', '#00bc8c') == pytest.approx(2.45, abs=0.01)

    def test_thresholds_follow_wcag_2_2(self):
        assert contrast.TEXT_MIN == 4.5 and contrast.BOUNDARY_MIN == 3.0
        assert contrast.passes_text('#000000', '#ffffff')
        assert not contrast.passes_text('#888888', '#ffffff')
        assert contrast.passes_boundary('#767676', '#ffffff')

    def test_blend_mixes_by_alpha(self):
        assert contrast.blend('#000000', '#ffffff', 0.5) == '#808080'
        assert contrast.blend('#ff0000', '#0000ff', 1.0) == '#ff0000'
        assert contrast.blend('#ff0000', '#0000ff', 0.0) == '#0000ff'


class TestPalettesAreComplete:
    @pytest.mark.parametrize('palette', PALETTES, ids=ids)
    def test_every_role_is_a_six_digit_hex_colour(self, palette):
        for role, value in palette.as_dict().items():
            assert HEX.match(value), f"{palette.mode}.{role} = {value!r}"

    def test_both_themes_name_the_same_roles(self):
        assert tokens.LIGHT.as_dict().keys() == tokens.DARK.as_dict().keys()

    def test_the_modes_are_labelled(self):
        assert (tokens.LIGHT.mode, tokens.DARK.mode) == ('light', 'dark')
        assert set(tokens.PALETTES) == {'pink', 'blue', 'yellow', 'green', 'light', 'dark',
            'lavender', 'teal', 'peach', 'coffee', 'midnight', 'forest', 'plum', 'graphite'}
        assert set(tokens.MODES) == {'system', *tokens.PALETTES}
        assert tuple(key for key, _ in tokens.THEME_OPTIONS) == tokens.MODES
        assert len(tokens.MODES) == len(set(tokens.MODES))

    def test_light_is_light_and_dark_is_dark(self):
        light = contrast.relative_luminance(contrast.hex_to_rgb(tokens.LIGHT.bg))
        dark = contrast.relative_luminance(contrast.hex_to_rgb(tokens.DARK.bg))
        assert light > 0.5 > 0.1 > dark

    def test_the_dark_reading_colour_is_decision_9s(self):
        assert tokens.DARK.ruby.lower() == '#80b8ff'

    def test_palettes_are_immutable(self):
        with pytest.raises(Exception):
            tokens.LIGHT.bg = '#000000'


class TestLegibility:
    @pytest.mark.parametrize('palette', PALETTES, ids=ids)
    @pytest.mark.parametrize('pair', tokens.TEXT_PAIRS, ids=lambda p: f"{p[0]}-on-{p[1]}")
    def test_text_pairs_reach_4_5_to_1(self, palette, pair):
        fg, bg = (getattr(palette, role) for role in pair)
        ratio = contrast.contrast_ratio(fg, bg)
        assert ratio >= contrast.TEXT_MIN, f"{palette.mode}: {pair[0]} {fg} on {pair[1]} {bg} = {ratio:.2f}:1"

    @pytest.mark.parametrize('palette', PALETTES, ids=ids)
    @pytest.mark.parametrize('pair', tokens.BOUNDARY_PAIRS, ids=lambda p: f"{p[0]}-on-{p[1]}")
    def test_boundaries_and_focus_reach_3_to_1(self, palette, pair):
        fg, bg = (getattr(palette, role) for role in pair)
        ratio = contrast.contrast_ratio(fg, bg)
        assert ratio >= contrast.BOUNDARY_MIN, f"{palette.mode}: {pair[0]} {fg} on {pair[1]} {bg} = {ratio:.2f}:1"

    def test_every_pair_names_real_roles(self):
        roles = set(tokens.LIGHT.as_dict())
        for fg, bg in tokens.TEXT_PAIRS + tokens.BOUNDARY_PAIRS:
            assert fg in roles and bg in roles

    def test_the_surface_is_distinct_from_the_window(self):
        for palette in PALETTES:
            assert palette.surface != palette.bg


class TestMetrics:
    def test_spacing_is_on_the_4px_grid_and_ascending(self):
        assert all(value % 4 == 0 for value in tokens.SPACING)
        assert list(tokens.SPACING) == sorted(tokens.SPACING)

    def test_radii_follow_windows_11_with_softer_controls(self):
        assert tokens.RADIUS['window'] == tokens.RADIUS['card'] == 8
        assert tokens.RADIUS['control'] == 6        # Fluent says 4; 6 reads friendlier (U4b)

    def test_every_target_is_at_least_24px(self):
        assert tokens.CONTROL_HEIGHT >= tokens.MIN_TARGET == 24

    def test_motion_is_short(self):
        assert max(tokens.MOTION_MS.values()) <= 200
