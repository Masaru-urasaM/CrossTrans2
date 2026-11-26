"""
Write the application icon files from the drawn mark (TODO.md U5, Decision 48).

    python tools/make_icons.py            # CrossTrans.ico, src/assets/CrossTrans.ico + .png
    python tools/make_icons.py --check    # verify them instead of writing

`src/ui/theme/brand.py` draws the mark at any size; this writes it where Windows and PyInstaller
look: `CrossTrans.ico` at the repository root (the .exe's file icon - `CrossTrans.spec`),
`src/assets/CrossTrans.ico` (the icon every window shows, bundled with the app) and
`src/assets/CrossTrans.png` (a 256 px picture of the mark). The full logo, `CrossTrans.png` at the
root, is not touched: the README and the welcome screen still use it.
"""
import argparse
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

TARGETS = ('CrossTrans.ico', os.path.join('src', 'assets', 'CrossTrans.ico'))
PNG = os.path.join('src', 'assets', 'CrossTrans.png')


def ico_problems(path):
    """Everything wrong with an .ico: missing, a frame that is not square, a size missing."""
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
    return problems


def write():
    from src.ui.theme import brand
    for target in TARGETS:
        path = os.path.join(REPO, target)
        brand.build_ico(path)
        print(f"wrote {target}: frames {len(brand.ICO_SIZES)}")
    png = os.path.join(REPO, PNG)
    brand.render_mark(256).save(png)
    print(f"wrote {PNG}")


def check():
    problems = []
    for target in TARGETS:
        problems += ico_problems(os.path.join(REPO, target))
    if problems:
        print('\n'.join(problems))
        return 1
    print("icons: every frame square and complete")
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
