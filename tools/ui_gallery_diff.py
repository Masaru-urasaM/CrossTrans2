"""
Compare two ui_gallery runs image by image (TODO.md U3: "the gallery is pixel-identical").

    python tools/ui_gallery_diff.py before u3-after
    python tools/ui_gallery_diff.py before u3-after --scales 100,150 --surfaces toasts

For each PNG both runs hold it counts the pixels that differ, boxes them, and writes a
highlighted copy to logs/ui_gallery/diff_<a>_<b>/. An image only one run has is reported as
missing. Exit status 0 when every compared image is identical (within --tolerance pixels), 1
otherwise. `--ignore` names captures whose pixels are not meant to repeat (an animation frame,
a fade) so they do not hide a real change in the rest.
"""
import argparse
import json
import os
import sys
from typing import Dict, Iterable, List, Optional, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = os.path.join(REPO, 'logs', 'ui_gallery')
# Captures that are not meant to repeat: the Translate button pulses and the popup's dots move
# (the "loading" states), and the screenshot overlay is a layered window (alpha 0.3 over a black
# canvas) that PrintWindow returns as flat black or flat #222222 depending on timing - until U9
# paints a frozen screenshot into it. Their size and findings are still compared via the JSON.
DEFAULT_IGNORE = ('translator.loading', 'quick_popup.loading', 'screenshot_overlay.')


def png_map(label: str, root: str = OUT_ROOT) -> Dict[Tuple[int, str], str]:
    """{(scale percent, 'surface.state'): path} for every PNG of a run."""
    found: Dict[Tuple[int, str], str] = {}
    base = os.path.join(root, label)
    if not os.path.isdir(base):
        raise SystemExit(f"ui_gallery_diff: no run {label!r} under {root}")
    for folder in sorted(os.listdir(base)):
        path = os.path.join(base, folder)
        if folder.startswith('s') and folder[1:].isdigit() and os.path.isdir(path):
            for name in sorted(os.listdir(path)):
                if name.endswith('.png'):
                    found[(int(folder[1:]), name[:-4])] = os.path.join(path, name)
    return found


def difference_mask(a, b, threshold: int = 0):
    """An 'L' mask, 255 where any channel of `a` and `b` differs by more than `threshold`.
    (Converting the delta to greyscale would weigh a one-level red change down to zero.)"""
    from PIL import ImageChops
    red, green, blue = ImageChops.difference(a, b).split()
    biggest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return biggest.point(lambda v: 255 if v > threshold else 0)


def diff_images(path_a: str, path_b: str, threshold: int = 0) -> dict:
    """{'same_size', 'differing', 'bbox', 'size_a', 'size_b'}; pixels differ when any channel
    moves by more than `threshold`."""
    from PIL import Image
    a, b = Image.open(path_a).convert('RGB'), Image.open(path_b).convert('RGB')
    if a.size != b.size:
        return {'same_size': False, 'differing': None, 'bbox': None,
                'size_a': list(a.size), 'size_b': list(b.size)}
    mask = difference_mask(a, b, threshold)
    count = sum(mask.histogram()[1:])
    return {'same_size': True, 'differing': count, 'bbox': mask.getbbox(),
            'size_a': list(a.size), 'size_b': list(b.size)}


def highlight(path_a: str, path_b: str, out_path: str, threshold: int = 0) -> None:
    """Write B with every differing pixel painted magenta (A's pixel dimmed beside it)."""
    from PIL import Image
    a, b = Image.open(path_a).convert('RGB'), Image.open(path_b).convert('RGB')
    if a.size != b.size:
        return
    mask = difference_mask(a, b, threshold)
    marked = b.copy()
    marked.paste(Image.new('RGB', b.size, (255, 0, 255)), mask=mask)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    marked.save(out_path)


def compare(label_a: str, label_b: str, scales: Optional[Iterable[int]] = None,
            surfaces: Optional[Iterable[str]] = None, ignore: Iterable[str] = DEFAULT_IGNORE,
            tolerance: int = 0, threshold: int = 0, root: str = OUT_ROOT,
            write_diffs: bool = True) -> List[dict]:
    """One row per capture present in either run."""
    maps = png_map(label_a, root), png_map(label_b, root)
    keys = sorted(set(maps[0]) | set(maps[1]))
    wanted_scales = set(scales) if scales else None
    wanted_surfaces = tuple(surfaces) if surfaces else None
    ignored = tuple(ignore)
    rows = []
    for scale, name in keys:
        if wanted_scales and scale not in wanted_scales:
            continue
        if wanted_surfaces and not name.startswith(wanted_surfaces):
            continue
        row = {'scale': scale, 'capture': name, 'ignored': any(name.startswith(i) for i in ignored)}
        a, b = maps[0].get((scale, name)), maps[1].get((scale, name))
        if a is None or b is None:
            row.update(status='missing', detail=f"only in {label_b if a is None else label_a}")
        else:
            result = diff_images(a, b, threshold)
            row.update(result)
            if not result['same_size']:
                row.update(status='resized', detail=f"{result['size_a']} -> {result['size_b']}")
            elif result['differing'] <= tolerance:
                row.update(status='identical')
            else:
                row.update(status='changed', detail=f"{result['differing']} px in {result['bbox']}")
                if write_diffs:
                    highlight(a, b, os.path.join(root, f'diff_{label_a}_{label_b}', f's{scale}',
                                                 f'{name}.png'), threshold)
        rows.append(row)
    return rows


def verdict(rows: List[dict]) -> Tuple[bool, Dict[str, int]]:
    """(all identical, counts by status), ignored captures excluded."""
    counts: Dict[str, int] = {}
    for row in rows:
        if row['ignored']:
            counts['ignored'] = counts.get('ignored', 0) + 1
            continue
        counts[row['status']] = counts.get(row['status'], 0) + 1
    return all(row['status'] == 'identical' for row in rows if not row['ignored']), counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument('label_a')
    parser.add_argument('label_b')
    parser.add_argument('--scales', help="comma-separated percent values (default: all)")
    parser.add_argument('--surfaces', help="comma-separated name prefixes (default: all)")
    parser.add_argument('--ignore', default=','.join(DEFAULT_IGNORE),
                        help="capture-name prefixes not meant to repeat")
    parser.add_argument('--tolerance', type=int, default=0,
                        help="differing pixels allowed per image (default 0)")
    parser.add_argument('--threshold', type=int, default=0,
                        help="a channel must move by more than this to count (default 0)")
    args = parser.parse_args(argv)
    rows = compare(args.label_a, args.label_b,
                   [int(s) for s in args.scales.split(',')] if args.scales else None,
                   args.surfaces.split(',') if args.surfaces else None,
                   [i for i in args.ignore.split(',') if i], args.tolerance, args.threshold)
    for row in rows:
        mark = 'ignored' if row['ignored'] else row['status']
        print(f"{row['scale']:>3}%  {row['capture']:<36} {mark:<9} {row.get('detail', '')}")
    ok, counts = verdict(rows)
    out = os.path.join(OUT_ROOT, f'diff_{args.label_a}_{args.label_b}.json')
    os.makedirs(OUT_ROOT, exist_ok=True)
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump({'a': args.label_a, 'b': args.label_b, 'identical': ok, 'counts': counts,
                   'rows': rows}, fh, indent=1)
    print(f"\n{'IDENTICAL' if ok else 'DIFFERENT'} {counts} -> {os.path.relpath(out, REPO)}")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
