"""Review real product surfaces for one palette on the guarded private desktop."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import ui_gallery as gallery


def surface(ctx):
    import popup_cases
    from src.ui import components as ui
    from src.ui.theme import tokens
    from src.ui.theme.contrast import contrast_ratio

    app = ctx.app
    mode = app.theme_manager.mode
    palette = app.theme_manager.palette
    modes = tuple(tokens.PALETTES)
    other = modes[(modes.index(mode) + 1) % len(modes)]
    app.show_popup(gallery.JP_SRC, gallery.EN_TR, 'English', force_new=True)
    app._refresh_reading_pane()
    size = (860, 760) if app.theme_manager.text_scale > 1 else (1120, 760)
    app.popup.geometry(f'{size[0]}x{size[1]}+30+30')
    ctx.pump(.4)
    original = app.original_text.get('1.0', 'end-1c')
    translated = app.trans_text.get_plain()
    yield 'translate', app.popup

    quick = app.quick_translate_manager
    quick._last_mouse_x, quick._last_mouse_y = 400, 200
    quick.show_loading('Japanese')
    ctx.pump(.2)
    loading = quick.popup

    def check_loading():
        p = app.theme_manager.palette
        assert quick.popup is loading
        assert str(loading.cget('bg')).lower() == p.accent.lower()
        assert contrast_ratio(p.on_accent, p.accent) >= 4.5
        assert contrast_ratio(p.on_accent, p.hover) >= 4.5
        for label in (quick._loading_label, quick._loading_dots):
            style = str(label.cget('style'))
            assert ui.get().style.lookup(style, 'background').lower() == p.accent.lower()
            assert ui.get().style.lookup(style, 'foreground').lower() == p.on_accent.lower()

    check_loading()
    app.theme_manager.set_mode(other)
    ctx.pump(.2)
    check_loading()
    app.theme_manager.set_mode(mode)
    ctx.pump(.2)
    check_loading()
    yield 'loading', loading
    quick.popup_close_btn.state(['active'])
    yield 'loading-hover', loading
    quick.popup_close_btn.state(['!active', 'focus'])
    yield 'loading-focus', loading
    quick.popup_close_btn.state(['!focus'])
    quick.popup_close_btn.invoke()
    assert quick.popup is None
    assert app.original_text.get('1.0', 'end-1c') == original
    assert app.trans_text.get_plain() == translated

    popup_cases.show_case(quick, popup_cases.case('E'))
    ctx.pump(.5)
    canonical = quick.popup_text.get_plain()
    assert canonical
    assert str(quick.popup.cget('bg')).lower() == palette.surface.lower()
    yield 'quick-result', quick.popup
    assert quick.popup_text.get_plain() == canonical
    quick.close()

    app.show_settings()
    settings = app.settings_window
    settings.open_page('appearance')
    ctx.pump(.3)
    ctx.settle_settings()
    page = settings.pages['appearance']
    area = settings._areas['appearance']
    assert len(page.theme.options) == len(tokens.MODES)
    assert set(page.theme_preview.tiles) == set(tokens.PALETTES)
    area.canvas.yview_moveto(0)
    yield 'appearance', app.popup
    last = page.theme_preview.tiles[modes[-1]]
    area.reveal(last, ui.get().px(8))
    ctx.pump(.2)
    assert last.winfo_rooty() >= area.canvas.winfo_rooty() - 1
    assert last.winfo_rooty() + last.winfo_height() <= (
        area.canvas.winfo_rooty() + area.canvas.winfo_height() + 1)
    yield 'appearance-last-preview', app.popup
    area.reveal(page.theme, ui.get().px(8))
    ctx.pump(.2)
    page.theme.open()
    page.theme._go(len(page.theme.options) - 1)
    ctx.pump(.15)
    assert page.theme.scrollbar is not None and page.theme.scrollbar.winfo_ismapped()
    assert page.theme.listbox.bbox(len(page.theme.options) - 1) is not None
    yield 'theme-list-end', page.theme.window
    page.theme.close()
    assert app.config.get_theme_mode() == app.theme_manager.mode == mode

    setup = app.onboarding
    setup.show()
    ctx.pump(.2)
    assert set(setup.theme_picker.tiles) == set(tokens.PALETTES)
    setup.scroll.reveal(setup.theme_picker.tiles[modes[-1]], ui.get().px(8))
    ctx.pump(.2)
    yield 'setup-last-preview', setup.window
    setup.skip()


def main(argv=None):
    gallery.SURFACES['palette-review'] = surface
    args = gallery.parse_args(argv)
    if args.child is None:
        raise SystemExit('Use --child for a single owned private-desktop profile')
    report = gallery.run_scale(args, args.child)
    folder = Path(gallery.OUT_ROOT) / args.label
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f's{args.child}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                           encoding='utf-8')
    bad = report['errors'] or report['blocked'] or report['windows_on_user_desktop']
    return int(bool(bad or (args.strict and report['findings'])))


if __name__ == '__main__':
    raise SystemExit(main())
