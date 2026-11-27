"""Reject missing/stale frozen UI data and code; never launch a built executable."""
import zipfile
import pytest
from src.utils import tcl_tk_bundle
from tools import verify_ui_bundle as bundle


class Archive:
    def __init__(self, files):
        self.toc = files

    def extract(self, name):
        return self.toc[name]


def test_windows_archive_paths_match_source_paths():
    expected = {'src/ui/locales/catalog.json': b'current locale'}
    reader = Archive({'src\\ui\\locales\\catalog.json': b'current locale'})
    assert bundle.check_data(reader, expected) == []


@pytest.mark.parametrize('name', ['src/ui/locales/catalog.json',
                                   'ttkbootstrap/assets/icons/bootstrap.ttf',
                                   'docs/screenshots/quick_translate.png'])
def test_missing_or_stale_ui_resources_fail_verification(name):
    expected = {name: b'current'}
    assert bundle.check_data(Archive({}), expected) == [f'Missing data: {name}']
    assert bundle.check_data(Archive({name: b'old'}), expected) == [f'Stale/different data: {name}']


def test_project_code_is_compared_with_matching_optimization():
    code = compile('VERSION = "2.0.0"\nassert False\n', 'packaged.py', 'exec', optimize=1)
    assert bundle.check_code(code, b'VERSION = "2.0.0"\nassert False\n')
    assert not bundle.check_code(code, b'VERSION = "2.0.1"\nassert False\n')


def test_source_manifest_has_locale_ten_screenshots_and_icon_font():
    expected = bundle.expected_data()
    assert 'src/ui/locales/catalog.json' in expected
    assert 'ttkbootstrap/assets/icons/bootstrap.ttf' in expected
    assert sum(name.startswith('docs/screenshots/') and name.endswith('.png') for name in expected) == 10
    assert all('__pycache__' not in name for name in expected)


def test_json_report_creates_its_parent_on_a_clean_runner(tmp_path, monkeypatch):
    import json
    destination = tmp_path / 'new' / 'nested' / 'report.json'
    report = {'project_modules_checked': [], 'data_files_checked': 0, 'exe_icon': True,
              'errors': [], 'sha256': 'a' * 64}
    monkeypatch.setattr(bundle, 'verify', lambda path: report)
    assert bundle.main(['--exe', 'not-launched.exe', '--json', str(destination)]) == 0
    assert json.loads(destination.read_text(encoding='utf-8')) == report


def test_installed_pdf_layout_resources_are_required_in_frozen_data(tmp_path):
    package = tmp_path / 'pymupdf'
    yaml = package / 'layout/resources/onnx/layout.yaml'
    yaml.parent.mkdir(parents=True)
    yaml.write_bytes(b'model: fixture')
    (package / 'layout/resources/onnx/layout.onnx').write_bytes(b'weights')
    expected = bundle.pdf_layout_data(package)
    assert expected == {'pymupdf/layout/resources/onnx/layout.yaml': b'model: fixture'}
    assert bundle.check_data(Archive({}), expected) == ['Missing data: pymupdf/layout/resources/onnx/layout.yaml']
    assert bundle.check_data(Archive(expected), expected) == []


def test_unused_pdf_layout_models_fail_verification():
    reader = Archive({'pymupdf\\layout\\resources\\onnx\\layout_imf1.onnx': b'weights',
                      'pymupdf/layout/resources/onnx/layout_imf1.yaml': b'model: fixture',
                      'pymupdf/mupdfcpp64.dll': b'library'})
    assert bundle.unused_pdf_layout_models(reader) == [
        'Unused PDF layout model is bundled: pymupdf\\layout\\resources\\onnx\\layout_imf1.onnx']
    assert bundle.unused_pdf_layout_models(Archive({'pymupdf/mupdfcpp64.dll': b'library'})) == []


def test_source_manifest_requires_layout_yaml_but_no_layout_model():
    expected = bundle.expected_data()
    layout = [name for name in expected if name.startswith(bundle.PDF_LAYOUT_RESOURCES)]
    assert layout and all(name.endswith('.yaml') for name in layout)


def test_spec_collects_pymupdf_data_without_the_layout_models():
    from pathlib import Path
    spec = (Path(__file__).resolve().parents[1] / 'CrossTrans.spec').read_text(encoding='utf-8')
    assert "collect_data_files('pymupdf', excludes=['layout/resources/**/*.onnx'])" in spec
    assert "collect_data_files('pymupdf')," not in spec
    # The contrib hook for pymupdf >= 2.0 collects every data file itself, so the models are also dropped from
    # the analysis result (the Phase 14 build still carried them).
    assert "a.datas = [entry for entry in a.datas" in spec
    assert "startswith('pymupdf/layout/resources/')" in spec and "endswith('.onnx')" in spec


def test_toolkit_distribution_metadata_is_required_by_the_frozen_version_probe():
    from email.parser import BytesParser
    from importlib.metadata import version
    expected = bundle.expected_data()
    metadata = [name for name in expected if name.startswith('ttkbootstrap-') and name.endswith('.dist-info/METADATA')]
    assert len(metadata) == 1
    assert BytesParser().parsebytes(expected[metadata[0]])['Version'] == version('ttkbootstrap')
    assert bundle.check_data(Archive({}), {metadata[0]: expected[metadata[0]]}) == [f'Missing data: {metadata[0]}']


def test_tcl_tk_runtime_data_and_early_boot_hook_are_required_in_frozen_bundle():
    assert bundle.check_tcl_tk(Archive({}), require_tkdnd_tcl9=True) == [
        'Missing Tcl/Tk runtime data: _tcl_data/init.tcl',
        'Missing Tcl/Tk runtime data: _tk_data/tk.tcl',
        'Missing early boot runtime hook: pyi_rth_boot',
        'Missing Tcl 9 TkDnD runtime file: tkinterdnd2/tkdnd/win-x64-tcl9/pkgIndex.tcl',
        'Missing Tcl 9 TkDnD runtime file: tkinterdnd2/tkdnd/win-x64-tcl9/libtkdnd2.10.3.dll',
    ]
    valid = Archive({
        '_tcl_data\\init.tcl': b'package provide Tcl',
        '_tk_data\\tk.tcl': b'package provide Tk',
        'pyi_rth_boot': b'boot',
        'tkinterdnd2\\tkdnd\\win-x64-tcl9\\pkgIndex.tcl': b'package ifneeded tkdnd',
        'tkinterdnd2\\tkdnd\\win-x64-tcl9\\libtkdnd2.10.3.dll': b'MZ',
    })
    assert bundle.check_tcl_tk(valid, require_tkdnd_tcl9=True) == []


def test_tcl_tk_bundle_extracts_zipfs_libraries_for_pyinstaller(tmp_path):
    dll_dir = tmp_path / 'DLLs'
    dll_dir.mkdir()
    tcl_dll = dll_dir / 'tcl90.dll'
    tk_dll = dll_dir / 'tcl9tk90.dll'
    with zipfile.ZipFile(tcl_dll, 'w') as zf:
        zf.writestr('tcl_library/init.tcl', 'set tcl_version 9.0')
        zf.writestr('tcl_library/encoding/utf-8.enc', 'utf8')
        zf.writestr('tcl_library/demos/widget.tcl', 'ignored')
        zf.writestr('tcl_library/tclConfig.sh', 'ignored')
    with zipfile.ZipFile(tk_dll, 'w') as zf:
        zf.writestr('tk_library/tk.tcl', 'set tk_version 9.0')
        zf.writestr('tk_library/ttk/ttk.tcl', 'ttk')

    staging = tmp_path / 'staging'
    status = tcl_tk_bundle.ensure_tcl_tk_dirs(staging, dll_search_dirs=[dll_dir])
    assert status['tcl_ready'] is True
    assert status['tk_ready'] is True
    assert status['extracted'] is True
    assert (staging / '_tcl_data' / 'init.tcl').is_file()
    assert (staging / '_tcl_data' / 'encoding' / 'utf-8.enc').is_file()
    assert not (staging / '_tcl_data' / 'demos').exists()
    assert not (staging / '_tcl_data' / 'tclConfig.sh').exists()
    assert (staging / '_tk_data' / 'tk.tcl').is_file()
    assert (staging / '_tk_data' / 'ttk' / 'ttk.tcl').is_file()


def test_collect_tkinterdnd2_tcl9_gathers_scripts_and_dll_for_tcl9(tmp_path):
    pkg = tmp_path / 'tkinterdnd2'
    tcl9_dir = pkg / 'tkdnd' / 'win-x64-tcl9'
    tcl9_dir.mkdir(parents=True)
    (tcl9_dir / 'pkgIndex.tcl').write_text('# index', encoding='utf-8')
    (tcl9_dir / 'tkdnd.tcl').write_text('# dnd', encoding='utf-8')
    (tcl9_dir / 'libtkdnd2.10.3.dll').write_bytes(b'MZ')
    (tcl9_dir / 'tkdnd2.10.3.lib').write_bytes(b'LIB')

    datas, binaries = tcl_tk_bundle.collect_tkinterdnd2_tcl9(
        pkg, arch='AMD64', system='Windows', require_when_tcl9=True
    )
    assert [dest for _src, dest in datas] == ['tkinterdnd2/tkdnd/win-x64-tcl9'] * 2
    assert sorted(src.rsplit('\\', 1)[-1].rsplit('/', 1)[-1] for src, _ in datas) == [
        'pkgIndex.tcl', 'tkdnd.tcl'
    ]
    assert len(binaries) == 1
    assert binaries[0][0].endswith('libtkdnd2.10.3.dll')
    assert binaries[0][1] == 'tkinterdnd2/tkdnd/win-x64-tcl9'


def test_collect_tkinterdnd2_tcl9_fails_loudly_when_tcl9_dir_missing(tmp_path):
    pkg = tmp_path / 'tkinterdnd2'
    (pkg / 'tkdnd' / 'win-x64').mkdir(parents=True)
    import tkinter
    if int(str(tkinter.TclVersion).split('.')[0]) < 9:
        pytest.skip('Test asserts Tcl >= 9 requirement')
    with pytest.raises(RuntimeError, match='win-x64-tcl9'):
        tcl_tk_bundle.collect_tkinterdnd2_tcl9(pkg, arch='AMD64', system='Windows', require_when_tcl9=True)


def test_repo_manifest_requires_all_brand_assets_and_ten_screenshots(tmp_path):
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    collected = bundle.collect_repo_data(repo)
    for asset in bundle.BRAND_ASSETS:
        assert asset in collected and collected[asset]
    assert sum(name.startswith('docs/screenshots/') and name.endswith('.png') for name in collected) == 10
    assert {'src.ui.theme.brand', 'src.ui.theme.windows', 'src.ui.tray'} <= bundle.ESSENTIAL_MODULES

    # Copy minimal repo layout into tmp_path and verify missing a brand asset fails loudly.
    for rel in list(collected):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(collected[rel])
    (tmp_path / 'src/assets/brand/crest_compact.png').unlink()
    with pytest.raises(ValueError, match='crest_compact.png'):
        bundle.collect_repo_data(tmp_path)


def test_check_exe_icon_verifies_all_dpi_frames_and_catches_stale_payloads(tmp_path):
    import struct
    from pathlib import Path
    from types import SimpleNamespace

    repo = Path(__file__).resolve().parents[1]
    ico_bytes = (repo / 'CrossTrans.ico').read_bytes()
    (tmp_path / 'CrossTrans.ico').write_bytes(ico_bytes)
    frames = bundle.ico_frame_bytes(ico_bytes)

    def make_pe(frame_map):
        ordered = sorted(frame_map.items())
        header = struct.pack('<HHH', 0, 1, len(ordered))
        entries = []
        icon_entries = []
        payloads = {}
        for idx, (size, payload) in enumerate(ordered, 1):
            dim = 0 if size == 256 else size
            entries.append(struct.pack('<BBBBHHIH', dim, dim, 0, 0, 1, 32, len(payload), idx))
            payloads[f'rt:{idx}'] = payload
            icon_entries.append(SimpleNamespace(
                id=idx,
                directory=SimpleNamespace(entries=[SimpleNamespace(
                    data=SimpleNamespace(struct=SimpleNamespace(OffsetToData=f'rt:{idx}', Size=len(payload)))
                )]),
            ))
        payloads['grp'] = header + b''.join(entries)
        group_res = SimpleNamespace(
            id=14,
            directory=SimpleNamespace(entries=[SimpleNamespace(
                directory=SimpleNamespace(entries=[SimpleNamespace(
                    data=SimpleNamespace(struct=SimpleNamespace(OffsetToData='grp', Size=len(payloads['grp'])))
                )])
            )]),
        )
        icon_res = SimpleNamespace(id=3, directory=SimpleNamespace(entries=icon_entries))
        return SimpleNamespace(
            DIRECTORY_ENTRY_RESOURCE=SimpleNamespace(entries=[group_res, icon_res]),
            get_data=lambda key, _sz: payloads[key],
        )

    assert bundle.check_exe_icon(make_pe(frames), root=tmp_path) == []

    stale = dict(frames)
    stale[16] = b'old-silver-16px-frame'
    errors = bundle.check_exe_icon(make_pe(stale), root=tmp_path)
    assert errors == ['Stale/different EXE icon frame: 16px']

    incomplete = {k: v for k, v in frames.items() if k != 256}
    errors = bundle.check_exe_icon(make_pe(incomplete), root=tmp_path)
    assert any('256' in err for err in errors)
