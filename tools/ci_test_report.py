"""Retain a portable JUnit report in CI logs alongside readable failures and skips."""
import argparse
import base64
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    args = parser.parse_args(argv)
    raw = args.report.read_bytes()
    root = ET.fromstring(raw)
    tests = list(root.iter('testcase'))
    failures, skipped = [], []
    for test in tests:
        name = test.get('classname', '') + '::' + test.get('name', '')
        for kind in ('failure', 'error', 'skipped'):
            item = test.find(kind)
            if item is not None:
                (skipped if kind == 'skipped' else failures).append(
                    {'test': name, 'kind': kind, 'message': item.get('message', '')})
    print(json.dumps({'total': len(tests), 'failures': failures, 'skipped': skipped}, ensure_ascii=True))
    print('BEGIN-JUNIT-GZIP-BASE64')
    encoded = base64.b64encode(gzip.compress(raw)).decode('ascii')
    for start in range(0, len(encoded), 200):
        print(encoded[start:start + 200])
    print('END-JUNIT-GZIP-BASE64')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
