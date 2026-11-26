"""
Tests for src/ui/theme/fonts.py and scale.py (TODO.md U3a).

Fonts: a family is used only if it is installed (a missing one becomes MS PGothic in Tk, V7);
sizes are pixels at the screen's scale. Scale: one rounding rule for every size.
"""
import pytest

from src.ui.theme import fonts, scale

WIN11 = {name.lower() for name in (
    'Segoe UI', 'Segoe UI Semibold', 'Segoe UI Variable Text', 'Segoe UI Variable Text Semibold',
    'Segoe UI Variable Display Semib', 'Segoe Fluent Icons', 'Segoe MDL2 Assets', 'Yu Gothic UI',
    'Yu Gothic UI Semibold', 'Consolas', 'Cascadia Mono', 'Malgun Gothic')}
WIN10 = {name.lower() for name in ('Segoe UI', 'Segoe UI Semibold', 'Segoe MDL2 Assets',
                                   'Yu Gothic UI', 'Consolas', 'Meiryo UI')}


class TestPx:
    def test_identity_at_100_percent(self):
        assert [scale.px(v) for v in (0, 1, 4, 12, 32)] == [0, 1, 4, 12, 32]

    def test_rounds_half_up(self):
        assert scale.px(5, 1.5) == 8          # 7.5
        assert scale.px(3, 1.25) == 4         # 3.75
        assert scale.px(2, 1.25) == 3         # 2.5

    def test_a_hairline_never_vanishes(self):
        assert scale.px(1, 1.0) == 1 and scale.px(1, 0.4) == 1

    def test_zero_stays_zero_and_negatives_mirror(self):
        assert scale.px(0, 2.0) == 0
        assert scale.px(-4, 1.5) == -6

    def test_dpi_to_scale(self):
        assert scale.scale_for_dpi(96) == 1.0
        assert scale.scale_for_dpi(144) == 1.5
        assert scale.scale_for_dpi(192) == 2.0

    def test_a_widget_knows_its_scale(self):
        class Fake:
            def winfo_fpixels(self, unit):
                assert unit == '1i'
                return 120.0
        assert scale.scale_of(Fake()) == 1.25

    def test_inverse_px_round_trips(self):
        assert scale.inverse_px(scale.px(32, 1.5), 1.5) == 32


class TestResolve:
    def test_the_first_installed_family_wins(self):
        assert fonts.resolve_family(('Nope', 'Segoe UI Variable Text', 'Segoe UI'), WIN11) \
            == 'Segoe UI Variable Text'

    def test_matching_ignores_case(self):
        assert fonts.resolve_family(('SEGOE UI',), WIN11) == 'SEGOE UI'

    def test_nothing_installed_falls_back_explicitly(self):
        assert fonts.resolve_family(('Nope', 'Missing'), WIN11) == 'Segoe UI'
        assert fonts.resolve_family(('Nope',), set(), fallback='Arial') == 'Arial'

    def test_windows_10_loses_the_variable_fonts_and_keeps_segoe_ui(self):
        assert fonts.resolve_family(fonts.CHAINS[('latin', 'regular')], WIN10) == 'Segoe UI'

    def test_the_icon_font_falls_back_to_mdl2(self):
        assert fonts.resolve_family(fonts.CHAINS[('icon', 'regular')], WIN10) == 'Segoe MDL2 Assets'
        assert fonts.resolve_family(fonts.CHAINS[('icon', 'regular')], WIN11) == 'Segoe Fluent Icons'

    def test_every_chain_ends_in_a_family_present_on_every_windows(self):
        for key, chain in fonts.CHAINS.items():
            assert chain[-1] in ('Segoe UI', 'Consolas', 'Segoe MDL2 Assets'), key


class TestRamp:
    def test_the_ramp_matches_fluent(self):
        r = fonts.RAMP
        assert (r['caption'].px, r['caption'].line) == (12, 16)
        assert (r['body'].px, r['body'].line) == (14, 20)
        assert (r['subtitle'].px, r['subtitle'].line) == (20, 28)
        assert (r['title'].px, r['title'].line) == (28, 36)
        assert (r['reading'].px, r['reading'].line) == (16, 24)

    def test_no_text_style_is_under_12px(self):
        assert all(s.px >= 12 for s in fonts.RAMP.values() if s.family_kind == 'text')

    def test_headings_are_semibold_not_bold(self):
        assert fonts.RAMP['title'].semibold and fonts.RAMP['body_strong'].semibold
        assert not fonts.RAMP['body'].semibold


class TestFontTuple:
    def test_body_on_windows_11(self):
        assert fonts.font_tuple('body', WIN11) == ('Segoe UI Variable Text', -14)

    def test_body_on_windows_10(self):
        assert fonts.font_tuple('body', WIN10) == ('Segoe UI', -14)

    def test_semibold_uses_the_semibold_face_without_a_bold_flag(self):
        assert fonts.font_tuple('body_strong', WIN11) == ('Segoe UI Variable Text Semibold', -14)
        assert fonts.font_tuple('body_strong', WIN10) == ('Segoe UI Semibold', -14)

    def test_a_semibold_style_with_no_semibold_face_becomes_bold(self):
        only_regular = {'segoe ui'}
        assert fonts.font_tuple('body_strong', only_regular) == ('Segoe UI', -14, 'bold')

    def test_the_display_family_has_two_spellings(self):
        assert fonts.font_tuple('title', WIN11)[0] == 'Segoe UI Variable Display Semib'
        assert fonts.font_tuple('title', {'segoe ui variable display semibold', 'segoe ui'})[0] \
            == 'Segoe UI Variable Display Semibold'

    def test_sizes_follow_the_screen_scale_and_the_text_size(self):
        assert fonts.font_tuple('body', WIN11, scale=1.5)[1] == -21
        assert fonts.font_tuple('body', WIN11, scale=1.0, text_scale=1.25)[1] == -18

    def test_japanese_uses_yu_gothic_ui_in_every_style(self):
        assert fonts.font_tuple('body', WIN11, script='japanese')[0] == 'Yu Gothic UI'
        assert fonts.font_tuple('title', WIN11, script='japanese')[0] == 'Yu Gothic UI Semibold'

    def test_an_unlisted_script_reads_in_the_latin_chain(self):
        assert fonts.font_tuple('body', WIN11, script='klingon')[0] == 'Segoe UI Variable Text'

    def test_mono_and_icon_ignore_the_script(self):
        assert fonts.font_tuple('mono', WIN11, script='japanese')[0] == 'Cascadia Mono'
        assert fonts.font_tuple('icon', WIN11, script='japanese')[0] == 'Segoe Fluent Icons'

    def test_text_never_shrinks_below_12px_even_at_a_small_text_size(self):
        assert fonts.font_tuple('caption', WIN11, text_scale=0.5)[1] == -12

    def test_line_height_scales_too(self):
        assert fonts.line_px('body') == 20
        assert fonts.line_px('body', scale=1.5) == 30

    def test_a_language_picks_its_script(self):
        assert fonts.script_for_language('Japanese') == 'japanese'
        assert fonts.script_for_language('Chinese (Simplified)') == 'chinese-simplified'
        assert fonts.script_for_language('French') == 'latin'
        assert fonts.script_for_language(None) == 'latin'

    def test_every_listed_language_script_has_a_chain(self):
        for script in set(fonts.LANGUAGE_SCRIPTS.values()):
            assert (script, 'regular') in fonts.CHAINS


class TestAgainstTk:
    def test_the_installed_list_is_lower_cased_and_resolves(self, tk_root):
        installed = fonts.installed_families(tk_root)
        assert installed and all(name == name.lower() for name in installed)
        family, size = fonts.font_tuple('body', installed)[:2]
        assert family.lower() in installed or family == fonts.FALLBACK_FAMILY
        assert size == -14
