"""Verify Home Translate refinement on a guarded private desktop."""
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from tools import ui_gallery as gallery


GEOMETRY = []


def settle_layout(ctx, expected=None):
    """Observe drained, stable native layout instead of sleeping a fixed interval."""
    from tools.tk_resize.settlement import LayoutSettlement
    tracker = LayoutSettlement(stable_ms=120, timeout_ms=4000)
    started = time.monotonic()
    while time.monotonic() - started < 4:
        ctx.pump(.02)
        app, view = ctx.app, ctx.app._view
        window, controller, area = app.popup, view._content_resize, view.body_area
        geometry, pending, inconsistent = [], [], []
        stack = [window]
        while stack:
            widget = stack.pop()
            if widget.winfo_ismapped():
                geometry.append((str(widget), widget.winfo_geometry()))
                stack.extend(widget.winfo_children())
        if expected is not None and view.mode != expected:
            pending.append('arrangement')
        for name, value in (('arrange', view._arrange_job), ('scroll-layout', area._layout_job),
                            ('scrollbar', area._bar_job), ('commit', controller._commit_job),
                            ('restore', controller._restore_job)):
            if value is not None:
                pending.append(name)
        if controller._last_client != (window.winfo_width(), window.winfo_height()):
            pending.append('host-size')
        if area.canvas.winfo_width() != area.content.winfo_width():
            inconsistent.append('viewport-width')
        snapshot = dict(geometry=geometry, pending=pending, inconsistent=inconsistent)
        if tracker.observe(snapshot, (time.monotonic() - started) * 1000) == 'settled':
            return
    raise AssertionError('Home layout did not settle: ' + str(tracker.report()['samples'][-1]))


def surface(ctx):
    from PIL import Image
    from src.core.translation_alignment import AlignedText, make_metadata
    from src.ui import translator_layout as layout

    app = ctx.app
    app.config.set('vision_enabled', True)
    app.config.set('file_processing_enabled', True)
    app.config.has_any_vision_capable = lambda: True
    app.config.has_any_file_capable = lambda: True
    original, translated = '  私は毎日日本語を勉強しています。\n次の文章です。\n', '  I study Japanese every day.\nThis is the next sentence.\n'
    data = make_metadata(original, translated, 'English', 'text', [
        {'source': [0, len(original)], 'translated': [0, len(translated)]}])
    factor = ctx.scale / 100
    wide = tuple(round(value * factor) for value in
                 ((1720, 900) if app.theme_manager.text_scale > 1 else (1480, 850)))
    narrow = tuple(round(value * factor) for value in (850, 900))

    def show(source, result, target='English'):
        app.show_popup(source, result, target, force_new=True)
        app.popup.geometry(f'{wide[0]}x{wide[1]}+20+20')
        app._refresh_reading_pane()
        settle_layout(ctx, layout.SIDE)

    def forbidden(*args, **kwargs):
        raise AssertionError('Swap submitted a translation request')

    app._do_retranslate = forbidden
    app._start_translation_task = forbidden
    show('', '')
    view = app._view
    assert view.mode == layout.SIDE
    assert view.input_placeholder.winfo_manager() == 'place'
    yield 'wide-empty', app.popup
    view.input_placeholder.event_generate('<Button-1>')
    settle_layout(ctx)
    assert view.original_text.focus_get() == view.original_text
    assert not view.input_placeholder.winfo_manager()
    assert app.original_text.get('1.0', 'end-1c') == ''
    yield 'wide-focused-empty', app.popup
    view.picker.button.focus_set()
    settle_layout(ctx)
    assert view.input_placeholder.winfo_manager() == 'place'

    show(original, AlignedText(translated, data))
    view = app._view

    def check_geometry(state):
        left = lambda w: w.winfo_rootx()
        right = lambda w: w.winfo_rootx() + w.winfo_width()
        top = lambda w: w.winfo_rooty()
        bottom = lambda w: w.winfo_rooty() + w.winfo_height()
        source, result, button = view.source_card, view.translation_card, view.swap_btn
        assert button.winfo_width() == button.winfo_height()
        area = app.attachment_area
        assert abs(right(view.source_chip) - right(source)) <= 1
        assert left(area) >= left(source) and right(area) <= left(view.source_chip)
        assert right(area.add_btn) <= right(area) + 1
        center = lambda widget: widget.winfo_rooty() + widget.winfo_height() / 2
        assert abs(center(view.source_chip) - center(area.add_btn)) <= 3
        if view.mode == layout.SIDE:
            assert right(source) <= left(button) < right(button) <= left(result)
            assert abs(center(source) - center(button)) <= 1
            assert abs(center(view.source_chip) - center(view.picker)) <= 3
        else:
            assert bottom(view.source_pane) <= top(button) < bottom(button) <= top(result)
            assert abs((left(button) + right(button))/2 - (left(source) + right(source))/2) <= 1
        GEOMETRY.append(dict(state=state, mode=view.mode,
            source=[left(source), top(source), source.winfo_width(), source.winfo_height()],
            result=[left(result), top(result), result.winfo_width(), result.winfo_height()],
            swap=[left(button), top(button), button.winfo_width(), button.winfo_height()],
            source_picker_right=right(view.source_chip), attachment_right=right(area)))

    check_geometry('wide-ready')
    yield 'wide-ready', app.popup
    view.swap_btn.event_generate('<Enter>')
    settle_layout(ctx)
    yield 'wide-swap-hover', app.popup
    view.swap_btn.event_generate('<Leave>')
    view.swap_btn.focus_force()
    settle_layout(ctx)
    yield 'wide-swap-focus', app.popup
    view.swap_btn.event_generate('<Return>')
    settle_layout(ctx)
    assert app.original_text.get('1.0', 'end-1c') == translated
    assert app.trans_text.get_plain() == original
    assert app.selected_language == 'Japanese' and view.source_chip.value == 'English'
    assert app._main_linked.controller is not None
    yield 'wide-swapped', app.popup
    view.swap_btn.invoke()
    settle_layout(ctx)
    assert app.original_text.get('1.0', 'end-1c') == original
    assert app.trans_text.get_plain() == translated
    assert app.selected_language == 'English' and view.source_chip.value == 'Japanese'

    with tempfile.TemporaryDirectory(prefix='home-translate-gallery-') as scratch:
        image = Path(scratch) / 'fixture.png'
        document = Path(scratch) / 'a_long_document_name_to_verify_source_width_wrapping.txt'
        Image.new('RGB', (64, 48), (80, 135, 180)).save(image)
        document.write_text('Fake translation fixture.', encoding='utf-8')
        assert app.attachment_area.add_file(str(image), show_warning=False)
        assert app.attachment_area.add_file(str(document), show_warning=False)
        settle_layout(ctx)
        files = list(app.attachment_area.get_attachments())
        check_geometry('wide-attachments')
        yield 'wide-attachments', app.popup
        app.popup.geometry(f'{narrow[0]}x{narrow[1]}+20+20')
        settle_layout(ctx, layout.STACKED)
        assert view.mode == layout.STACKED
        check_geometry('stacked-attachments')
        yield 'stacked-attachments', app.popup
        view.swap_btn.event_generate('<Enter>')
        settle_layout(ctx)
        yield 'stacked-swap-hover', app.popup
        view.swap_btn.event_generate('<Leave>')
        view.swap_btn.invoke()
        settle_layout(ctx)
        assert app.original_text.get('1.0', 'end-1c') == translated
        assert app.trans_text.get_plain() == original
        assert app.attachment_area.get_attachments() == files
        check_geometry('stacked-swapped')
        yield 'stacked-swapped', app.popup
        view.swap_btn.invoke()
        settle_layout(ctx)
        assert app.original_text.get('1.0', 'end-1c') == original
        assert app.trans_text.get_plain() == translated

    show('Bonjour.', 'Hello.')
    view = app._view
    from src.core.nlp_manager import nlp_manager
    with patch.object(nlp_manager, 'detect_language', return_value=('Unknown', 0.0)):
        view.swap_btn.invoke()
    settle_layout(ctx)
    assert view.source_chip.window is not None
    assert app.original_text.get('1.0', 'end-1c') == 'Bonjour.'
    yield 'unknown-source-choice', view.source_chip.window
    view.source_chip.set('French')
    view.source_chip.close()
    view.swap_btn.invoke()
    settle_layout(ctx)
    assert app.selected_language == 'French' and view.source_chip.value == 'English'
    assert app.original_text.get('1.0', 'end-1c') == 'Hello.'
    assert app.trans_text.get_plain() == 'Bonjour.'
    yield 'unknown-source-swapped', app.popup

    show("i'm supperman", 'A translated result.', 'Vietnamese')
    view = app._view
    assert view.source_chip.value == 'Auto'
    with patch.object(nlp_manager, 'is_installed', side_effect=lambda language: language == 'English'):
        view.swap_btn.invoke()
    settle_layout(ctx)
    assert view.source_chip.window is None
    assert app.selected_language == 'English' and view.source_chip.value == 'Vietnamese'
    assert app.original_text.get('1.0', 'end-1c') == 'A translated result.'
    assert app.trans_text.get_plain() == "i'm supperman"
    yield 'auto-detected-swap', app.popup


def main(argv=None):
    gallery.SURFACES['home-translate-upgrade'] = surface
    args = gallery.parse_args(argv)
    if args.child is None:
        raise SystemExit('Use --child for a single guarded profile')
    report = gallery.run_scale(args, args.child)
    report['home_geometry'] = GEOMETRY
    folder = Path(gallery.OUT_ROOT) / args.label
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f's{args.child}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return int(bool(report['errors'] or report['blocked'] or report['windows_on_user_desktop']
                    or args.strict and report['findings']))


if __name__ == '__main__':
    raise SystemExit(main())
