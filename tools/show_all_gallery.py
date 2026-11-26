"""Capture translation-first Quick expansion on the guarded private desktop."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from tools import ui_gallery as gallery
from tools.popup_gallery import settle


ALLOCATIONS = []


def surface(ctx):
    from src.core.translation_alignment import AlignedText, make_metadata

    app, quick = ctx.app, ctx.app.quick_translate_manager
    app.config.set_furigana_enabled(True)
    quick._last_mouse_x, quick._last_mouse_y = 200, 100
    source_lines = [f'{i}. 漢字を確認してから、次の操作に進んでください。\n' for i in range(60)]
    target_lines = [f'{i}. Check the translated result before continuing to the next step.\n'
                    for i in range(60)]
    source, target = ''.join(source_lines), ''.join(target_lines)
    groups, source_offset, target_offset = [], 0, 0
    for original, translated in zip(source_lines, target_lines):
        groups.append({'source': [source_offset, source_offset + len(original)],
                       'translated': [target_offset, target_offset + len(translated)]})
        source_offset += len(original)
        target_offset += len(translated)
    metadata = make_metadata(source, target, 'English', 'text', groups)
    quick.show(AlignedText(target, metadata), 'English', original=source, source_lang='Japanese',
               model_info='Fixture / translation-first expansion')
    settle(quick, ctx.root, compose_s=.1)
    assert quick._layout['done'] and quick._layout['revealed']
    compact = quick.popup.geometry()
    yield 'compact', quick.popup
    controller = quick._linked_controller
    assert controller is not None
    quick.popup_original_toggle.invoke()
    ctx.pump(.4)

    def priority(state):
        layout, popup = quick._layout, quick.popup
        metrics, heights = layout['resize_metrics'], layout['heights']
        room = popup.winfo_height() - layout['chrome']
        assert heights.translation == min(metrics['translation'], room - metrics['min_original'])
        assert heights.original == metrics['min_original']
        assert heights.translation_capped and heights.original_capped
        assert quick._original_scrollbar.winfo_ismapped()
        assert quick.popup_furigana.get_plain() == source
        assert quick.popup_text.get_plain() == target
        assert quick._linked_controller is controller
        ALLOCATIONS.append(dict(state=state, room=room, heights=heights._asdict(),
            source_floor=metrics['min_original'], translation_need=metrics['translation'],
            original_need=metrics['original'], geometry=popup.geometry()))

    priority('expanded')
    yield 'expanded-translation-first', quick.popup
    target_view = quick.popup_text.yview()
    controller.activate(1, len(controller.groups) - 1)
    ctx.pump(.3)
    assert quick.popup_text.yview() == target_view
    assert quick.popup_furigana.yview()[0] > 0
    ranges = quick.popup_furigana.tag_ranges('linked_reading')
    assert ranges and quick.popup_furigana.bbox(ranges[0]) is not None
    priority('linked-source')
    yield 'linked-source', quick.popup

    quick.popup_original_toggle.invoke()
    ctx.pump(.3)
    assert quick.popup.geometry() == compact, (quick.popup.geometry(), compact)
    yield 'restored', quick.popup
    quick.close()

    # Short translation leaves surplus height for a large, scrollable original.
    quick.show('A short translated result.\nRead the full original when needed.', 'English',
               original=source, source_lang='Japanese')
    settle(quick, ctx.root, compose_s=.1)
    assert quick._layout['done'] and quick._layout['revealed']
    compact_width = quick.popup.winfo_width()
    quick.popup_original_toggle.invoke()
    ctx.pump(.3)
    layout = quick._layout
    assert quick.popup.winfo_width() == compact_width
    assert not layout['heights'].translation_capped
    assert layout['heights'].original > layout['resize_metrics']['min_original']
    yield 'source-uses-spare-room', quick.popup
    quick.close()


def main(argv=None):
    gallery.SURFACES['show-all-priority'] = surface
    args = gallery.parse_args(argv)
    if args.child is None:
        raise SystemExit('Use --child for a single guarded private-desktop profile')
    report = gallery.run_scale(args, args.child)
    report['allocations'] = ALLOCATIONS
    folder = Path(gallery.OUT_ROOT) / args.label
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f's{args.child}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                            encoding='utf-8')
    bad = report['errors'] or report['blocked'] or report['windows_on_user_desktop']
    return int(bool(bad or (args.strict and report['findings'])))


if __name__ == '__main__':
    raise SystemExit(main())
