"""
Tests for the toasts and the update notice (TODO.md U6, V3).

A toast is a floating card holding an info bar of its kind. Before 2.0 the four kinds differed only
by a glyph - ttkbootstrap 1.x repainted their colours (V3) - and the warning's link was dark blue
on near-black. These pin that each kind now has its own tint and icon, in both themes, that the
text on it reads, that a click dismisses, that the action works, and that the update notice is a
card of the same family that still never takes the focus.
"""
import tkinter as tk
from unittest.mock import Mock

import pytest

from src.ui import components as ui
from src.ui.components import styles
from src.ui.theme import tokens, windows
from src.ui.theme.contrast import blend, contrast_ratio
from src.ui.toast import ToastManager, ToastType
from src.ui.update_notice import UpdateNotice

from tests.test_components import env  # noqa: F401  (the engine and the component context)


@pytest.fixture(params=['light', 'dark'])
def mode(request, env):
    env.manager.set_mode(request.param)
    return request.param


@pytest.fixture
def toasts(env, tk_root):
    manager = ToastManager(tk_root)
    yield manager
    manager.dismiss_all()


def lower(value):
    return str(value).lower()


class TestEachKindLooksItsOwn:
    KINDS = {ToastType.SUCCESS: 'success', ToastType.ERROR: 'danger',
             ToastType.INFO: 'info', ToastType.WARNING: 'warning'}

    def test_the_four_kinds_map_to_the_four_info_bars(self, toasts):
        assert {t: toasts.CONFIGS[t].kind for t in ToastType} == self.KINDS
        assert set(self.KINDS.values()) == set(styles.INFO_KINDS)

    def test_each_has_its_own_style_and_icon(self, toasts, tk_root):
        seen = {}
        for toast_type in ToastType:
            toast = toasts.show(f"{toast_type.value} message", toast_type)
            tk_root.update()
            seen[toast_type] = (str(toast.bar.cget('style')), str(toast.bar.icon.cget('image')))
        assert len({style for style, _ in seen.values()}) == 4        # V3: they used to look alike
        assert len({image for _, image in seen.values()}) == 4

    def test_the_message_is_shown(self, toasts, tk_root):
        toast = toasts.show("Copied to clipboard!", ToastType.SUCCESS)
        tk_root.update()
        assert toast.bar.text.cget('text') == "Copied to clipboard!"

    def test_there_is_no_glyph_in_front_of_the_words(self, toasts, tk_root):
        toast = toasts.show("Saved", ToastType.SUCCESS)
        tk_root.update()
        assert toast.bar.text.cget('text') == "Saved"          # the icon is an image now

    def test_the_text_reads_on_every_tint(self, mode, env):
        p = env.palette
        for kind in styles.INFO_KINDS:
            tint = styles.infobar_colours(p, kind, p.surface)['tint']
            assert contrast_ratio(p.text, tint) >= 4.5, (kind, mode)

    def test_the_default_times_are_kept(self, toasts):
        assert {t: toasts.CONFIGS[t].default_duration for t in ToastType} == {
            ToastType.SUCCESS: 2000, ToastType.ERROR: 4000, ToastType.INFO: 2500,
            ToastType.WARNING: 3000}


class TestTheCard:
    def test_it_has_a_hairline_ring_on_the_surface(self, toasts, tk_root, env, mode):
        toast = toasts.show("Hello", ToastType.INFO)
        tk_root.update()
        assert int(toast.cget('highlightthickness')) == 1
        assert lower(toast.cget('highlightbackground')) == lower(env.palette.border)
        assert lower(toast.cget('bg')) == lower(env.palette.surface)

    def test_it_fades_in_from_nothing(self, toasts, env, monkeypatch):
        from dataclasses import replace
        monkeypatch.setattr(env.manager, 'accessibility', replace(env.manager.accessibility, animations=True, high_contrast=False))
        toast = toasts.show("Hello", ToastType.INFO)
        assert float(toast.attributes('-alpha')) <= 0.2

    def test_it_asks_for_rounded_corners_once_mapped(self, toasts, tk_root, monkeypatch):
        calls = []
        monkeypatch.setattr(windows, 'style_floating', lambda *a, **k: calls.append(a))
        toasts.show("Hello", ToastType.INFO)
        tk_root.update()
        assert len(calls) == 1

    def test_a_long_message_wraps(self, toasts, tk_root, env):
        toast = toasts.show("word " * 80, ToastType.INFO)
        tk_root.update()
        assert toast.winfo_width() <= env.px(300) + env.px(160)
        assert toast.winfo_height() > env.px(90)

    def test_it_is_as_wide_as_its_text_not_wider(self, toasts, tk_root, env):
        short = toasts.show("OK", ToastType.INFO)
        long = toasts.show("A somewhat longer message", ToastType.INFO)
        tk_root.update()
        assert short.winfo_width() < long.winfo_width()

    def test_stacked_toasts_do_not_overlap(self, toasts, tk_root):
        first = toasts.show("one", ToastType.INFO)
        second = toasts.show("two", ToastType.INFO)
        tk_root.update()
        assert second.winfo_y() + second.winfo_height() <= first.winfo_y()


class TestDismissAndAction:
    def test_a_click_anywhere_on_it_dismisses(self, toasts, tk_root):
        for target in ('bar', 'icon', 'text'):
            toast = toasts.show("Click me", ToastType.INFO)
            tk_root.update()
            widget = toast if target == 'bar' else getattr(toast.bar, target)
            widget.event_generate('<Button-1>')
            tk_root.update()
            assert toast not in toasts.active_toasts, target

    def test_pressing_the_action_does_not_start_a_fade_before_it_runs(self, toasts, tk_root):
        toast = toasts.show_warning_with_action("x", "Open it", lambda: None)
        tk_root.update()
        toast.bar.action.event_generate('<ButtonPress-1>')
        tk_root.update()
        assert toast in toasts.active_toasts

    def test_the_action_runs_its_callback_and_dismisses(self, toasts, tk_root):
        seen = []
        toast = toasts.show_warning_with_action("No vision-capable API found.",
                                                "Open API Key Settings", lambda: seen.append(1))
        tk_root.update()
        assert toast.bar.action.cget('text') == "Open API Key Settings"
        toast.bar.action.invoke()
        assert seen == [1] and toast not in toasts.active_toasts

    def test_the_action_toast_is_the_warning_kind(self, toasts, tk_root):
        toast = toasts.show_warning_with_action("x", "y", lambda: None)
        assert toast.bar.kind == 'warning'

    def test_the_action_link_reads_on_the_tint(self, mode, env):
        p = env.palette
        tint = styles.infobar_colours(p, 'warning', p.surface)['tint']
        assert contrast_ratio(p.text, tint) >= 4.5

    def test_at_most_five_are_kept(self, toasts, tk_root):
        for n in range(8):
            toasts.show(f"toast {n}", ToastType.INFO)
        assert len(toasts.active_toasts) == toasts.MAX_TOASTS

    def test_dismiss_all_clears_them(self, toasts, tk_root):
        for n in range(3):
            toasts.show(f"toast {n}", ToastType.INFO)
        toasts.dismiss_all()
        assert toasts.active_toasts == []

    def test_the_shake_still_runs_on_a_warning(self, toasts, tk_root):
        toast = toasts.show_warning_with_shake("Look here")
        tk_root.update()
        assert toast.bar.kind == 'warning'


class TestTheUpdateNotice:
    @pytest.fixture
    def notice(self, env, tk_root, monkeypatch):
        monkeypatch.setattr(UpdateNotice, '_work_area', staticmethod(lambda root: (0, 0, 1280, 900)))
        tk_root.withdraw()
        made = UpdateNotice(tk_root, '9.9.9', Mock())
        tk_root.update()
        yield made
        made.close()

    def test_it_is_a_card_with_a_ring(self, mode, notice, env):      # the theme first, then the notice
        window = notice.window
        assert int(window.cget('highlightthickness')) == env.px(1)
        assert lower(window.cget('highlightbackground')) == lower(env.palette.border)
        assert lower(window.cget('bg')) == lower(env.palette.surface)

    def test_update_is_the_accent_later_is_soft_and_skip_is_quiet(self, notice):
        kinds = {action: str(button.cget('style')) for action, button in notice.buttons.items()}
        assert kinds == {'update': 'CtAccentSurface.TButton', 'later': 'CtSoftSurface.TButton',
                         'skip': 'CtSubtleSurface.TButton'}

    def test_update_carries_a_download_icon(self, notice):
        assert notice.buttons['update'].icon_name == 'download'

    def test_the_wording_is_unchanged(self, notice):
        texts = [str(w.cget('text')) for w in _labels(notice.window)]
        assert 'Update available' in texts
        assert 'CrossTrans v9.9.9 is ready to download.' in texts
        assert 'Your work stays open until you choose to restart.' in texts

    def test_it_sits_in_the_corner_of_the_work_area(self, notice):
        left, top, right, bottom = notice._work_area(notice.window)
        window = notice.window
        assert window.winfo_x() + window.winfo_width() <= right
        assert window.winfo_y() + window.winfo_height() <= bottom

    def test_it_asks_for_rounded_corners_once_mapped(self, env, tk_root, monkeypatch):
        calls = []
        monkeypatch.setattr(windows, 'style_floating', lambda *a, **k: calls.append(a))
        tk_root.withdraw()
        made = UpdateNotice(tk_root, '9.9.9', Mock())
        tk_root.update()
        made.close()
        assert len(calls) == 1


def _labels(widget):
    found = []
    for child in widget.winfo_children():
        if child.winfo_class() == 'TLabel':
            found.append(child)
        found.extend(_labels(child))
    return found
