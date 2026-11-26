"""A workflow log must retain the exact JUnit evidence, including failed and skipped cases."""
import base64
import gzip
import json
from tools import ci_test_report


def test_logged_report_can_be_recovered_byte_for_byte(tmp_path, capsys):
    report = tmp_path / 'junit.xml'
    raw = b'<testsuites><testsuite><testcase classname="suite" name="ok"/><testcase classname="suite" name="bad"><failure message="broken"/></testcase><testcase classname="suite" name="skip"><skipped message="no compiler"/></testcase></testsuite></testsuites>'
    report.write_bytes(raw)
    assert ci_test_report.main([str(report)]) == 0
    lines = capsys.readouterr().out.splitlines()
    summary = json.loads(lines[0])
    assert summary['total'] == 3
    assert summary['failures'] == [{'test': 'suite::bad', 'kind': 'failure', 'message': 'broken'}]
    assert summary['skipped'][0]['message'] == 'no compiler'
    first = lines.index('BEGIN-JUNIT-GZIP-BASE64') + 1
    last = lines.index('END-JUNIT-GZIP-BASE64')
    assert gzip.decompress(base64.b64decode(''.join(lines[first:last]))) == raw
