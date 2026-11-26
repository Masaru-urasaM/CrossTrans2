"""Capture one installed test window in a separately timeout-bounded process."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.ui_gallery import capture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('handle', type=int)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    return 0 if capture(args.handle, str(args.output)) else 1


if __name__ == '__main__':
    raise SystemExit(main())
