"""
Write and verify the CrossTrans II application icons and brand PNGs (TODO.md U5, Decision 48).

    python tools/make_icons.py            # CrossTrans.ico, CrossTrans.png, src/assets/CrossTrans.ico + .png
    python tools/make_icons.py --check    # verify masters, .ico frames, and PNGs instead of writing

`src/ui/theme/brand.py` loads the 512 px Royal Sapphire Crest masters from `src/assets/brand/`
(`crest_full.png`, `crest_compact.png`, `mark_only.png`) and renders every frame size:
- `CrossTrans.ico` at the repository root (the EXE file icon in `CrossTrans.spec`)
- `src/assets/CrossTrans.ico` (the window and installer icon, bundled with the app)
- `src/assets/CrossTrans.png` (a 256 px crest used in documentation and bundled assets)
- `CrossTrans.png` at the repository root (the 512 px Royal Sapphire Crest master logo)
"""
import argparse
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

TARGETS = ('CrossTrans.ico', os.path.join('src', 'assets', 'CrossTrans.ico'))
PNG = os.path.join('src', 'assets', 'CrossTrans.png')
ROOT_PNG = 'CrossTrans.png'
PNG_SPECS = ((PNG, 256), (ROOT_PNG, 512))
BRAND_MASTERS = ('crest_full.png', 'crest_compact.png', 'mark_only.png')
MASTER_SIZE = 512


def brand_master_problems(repo=REPO):
    """Verify the three 512x512 RGBA Royal Sapphire Crest master PNGs exist under src/assets/brand/."""
    from PIL import Image
    problems = []
    for name in BRAND_MASTERS:
        path = os.path.join(repo, 'src', 'assets', 'brand', name)
        if not os.path.isfile(path):
            problems.append(f"{path} is missing")
            continue
        with Image.open(path) as img:
            if img.size != (MASTER_SIZE, MASTER_SIZE):
                problems.append(
                    f"{name} must be {MASTER_SIZE}x{MASTER_SIZE}, found {img.width}x{img.height}")
            if img.mode != 'RGBA':
                problems.append(f"{name} must be RGBA, found {img.mode}")
    return problems


def png_problems(path, expected_size):
    """Verify a generated brand PNG exists, is square RGBA at `expected_size`, and matches `render_mark`."""
    from PIL import Image
    from src.ui.theme import brand
    if not os.path.isfile(path):
        return [f"{path} is missing"]
    with Image.open(path) as img:
        if img.size != (expected_size, expected_size):
            return [f"{os.path.basename(path)} must be {expected_size}x{expected_size}, "
                    f"found {img.width}x{img.height}"]
        rgba = img.convert('RGBA')
    expected = brand.render_mark(expected_size)
    if rgba.tobytes() != expected.tobytes():
        return [f"{path} does not match brand.render_mark({expected_size})"]
    return []


def ico_problems(path):
    """Everything wrong with an .ico: missing, non-square frame, missing size, or stale pixels."""
    from PIL import Image
    from src.ui.theme import brand
    if not os.path.exists(path):
        return [f"{path} is missing"]
    problems = []
    with Image.open(path) as icon:
        sizes = sorted(icon.info.get('sizes', []))
        for width, height in sizes:
            if width != height:
                problems.append(f"{os.path.basename(path)} has a frame that is not square: {width}x{height}")
        have = {w for w, h in sizes}
        missing = [s for s in brand.ICO_SIZES if s not in have]
        if missing:
            problems.append(f"{os.path.basename(path)} lacks the frame sizes {missing}")
        else:
            for size in brand.ICO_SIZES:
                icon.size = (size, size)
                frame = icon.convert('RGBA')
                expected = brand.render_mark(size)
                if frame.size != expected.size or frame.tobytes() != expected.tobytes():
                    problems.append(f"{os.path.basename(path)} frame {size}x{size} does not match brand.render_mark({size})")
    return problems


def write():
    from src.ui.theme import brand
    for target in TARGETS:
        path = os.path.join(REPO, target)
        brand.build_ico(path)
        print(f"wrote {target}: frames {len(brand.ICO_SIZES)}")
    for rel, size in PNG_SPECS:
        png = os.path.join(REPO, rel)
        brand.render_mark(size).save(png)
        print(f"wrote {rel} ({size}x{size})")


def check():
    problems = brand_master_problems(REPO)
    for target in TARGETS:
        problems += ico_problems(os.path.join(REPO, target))
    for rel, size in PNG_SPECS:
        problems += png_problems(os.path.join(REPO, rel), size)
    if problems:
        print('\n'.join(problems))
        return 1
    print("icons: brand masters, every .ico frame, and PNGs verified")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument('--check', action='store_true', help="verify the files instead of writing them")
    args = parser.parse_args(argv)
    if args.check:
        return check()
    write()
    return check()


if __name__ == '__main__':
    sys.exit(main())
