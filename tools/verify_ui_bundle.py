"""Verify 2.0 UI resources and packaged project code without launching the EXE.

Usage: python tools/verify_ui_bundle.py --exe PATH [--json PATH]
Application text uses installed Windows fonts. Bootstrap's privately rendered icon font is data.
"""
import argparse
import hashlib
import importlib.util
from importlib.metadata import distribution
import json
import marshal
from pathlib import Path
import struct
import sys
import tkinter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ui.theme import brand  # noqa: E402
ESSENTIAL_MODULES = {'src.product', 'src.ui.main_workspace', 'src.ui.home_dictionary',
                     'src.core.key_usage', 'src.core.key_identity', 'src.utils.github_updates', 'src.utils.gc_owner',
                     'src.ui.components.theme_picker', 'src.ui.i18n', 'src.ui.welcome', 'src.core.onboarding',
                     'src.ui.theme.accessibility', 'src.ui.theme.brand', 'src.ui.theme.windows',
                     'src.ui.tray', 'src.ui.components.menu',
                     'src.ui.settings.page_about', 'src.ui.settings.page_dictionary',
                     'src.ui.settings.page_providers', 'src.ui.inline_reading', 'src.core.source_languages'}
ESSENTIAL_MODULES.update({'src.core.audio_media', 'src.core.audio_service', 'src.core.speech_engines',
                          'src.ui.audio_controller', 'src.ui.settings.audio_settings',
                          'src.utils.logging_setup', 'src.utils.diagnostics', 'src.ui.diagnostics_dialog',
                          'src.core.result_status', 'src.core.selection_capture',
                          'src.core.registry_source', 'src.core.model_registry_client'})
BRAND_ASSETS = (
    'src/assets/CrossTrans.ico',
    'src/assets/CrossTrans.png',
    'src/assets/brand/crest_full.png',
    'src/assets/brand/crest_compact.png',
    'src/assets/brand/mark_only.png',
)
TCL_TK_REQUIRED_DATA = ('_tcl_data/init.tcl', '_tk_data/tk.tcl')
TKDND_TCL9_REQUIRED_FILES = (
    'tkinterdnd2/tkdnd/win-x64-tcl9/pkgIndex.tcl',
    'tkinterdnd2/tkdnd/win-x64-tcl9/libtkdnd2.10.3.dll',
)
ICON_DATA = ('assets/icons/bootstrap.ttf', 'assets/icons/glyphmap.json',
             'assets/icons/icon_metrics.json', 'assets/elements/manifest.json',
             'assets/elements/checkbox-checked.png', 'assets/elements/switch-round.png',
             'assets/elements/slider-handle.png', 'assets/elements/progressbar-default.png')


def normalized(name):
    return str(name).replace('\\', '/').lstrip('./')


def check_data(reader, expected):
    names = {normalized(name): name for name in reader.toc}
    errors = []
    for path, contents in expected.items():
        archived = names.get(normalized(path))
        if archived is None:
            errors.append(f'Missing data: {path}')
        elif reader.extract(archived) != contents:
            errors.append(f'Stale/different data: {path}')
    return errors


def check_tcl_tk(reader, require_tkdnd_tcl9=None):
    names = {normalized(name): name for name in reader.toc}
    errors = []
    for path in TCL_TK_REQUIRED_DATA:
        archived = names.get(normalized(path))
        if archived is None:
            errors.append(f'Missing Tcl/Tk runtime data: {path}')
        elif not reader.extract(archived):
            errors.append(f'Empty Tcl/Tk runtime data: {path}')
    if 'pyi_rth_boot' not in names:
        errors.append('Missing early boot runtime hook: pyi_rth_boot')
    if require_tkdnd_tcl9 is None:
        try:
            require_tkdnd_tcl9 = int(str(tkinter.TclVersion).split('.')[0]) >= 9
        except Exception:
            require_tkdnd_tcl9 = False
    if require_tkdnd_tcl9:
        for path in TKDND_TCL9_REQUIRED_FILES:
            archived = names.get(normalized(path))
            if archived is None:
                errors.append(f'Missing Tcl 9 TkDnD runtime file: {path}')
            elif not reader.extract(archived):
                errors.append(f'Empty Tcl 9 TkDnD runtime file: {path}')
    return errors


def collect_repo_data(root=ROOT):
    result = {}
    for folder in ('src/assets', 'docs/screenshots', 'src/ui/locales', 'src/core/data'):
        for path in sorted((root / folder).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.py', '.pyc'):
                result[path.relative_to(root).as_posix()] = path.read_bytes()
    if 'src/ui/locales/catalog.json' not in result:
        raise ValueError('The source interface catalog is missing')
    for asset in BRAND_ASSETS:
        if not result.get(asset):
            raise ValueError(f'Missing or empty brand asset: {asset}')
    screenshots = [name for name in result if name.startswith('docs/screenshots/') and name.endswith('.png')]
    if len(screenshots) != 10:
        raise ValueError(f'Expected ten documentation screenshots, found {len(screenshots)}')
    for shot in screenshots:
        if not result[shot]:
            raise ValueError(f'Empty documentation screenshot: {shot}')
    return result


def expected_data(root=ROOT):
    result = collect_repo_data(root)
    spec = importlib.util.find_spec('ttkbootstrap')
    package = Path(spec.origin).parent
    for path in ICON_DATA:
        result['ttkbootstrap/' + path] = (package / path).read_bytes()
    toolkit = distribution('ttkbootstrap')
    metadata = [path for path in toolkit.files or ()
                if str(path).endswith('.dist-info/METADATA')]
    if len(metadata) != 1:
        raise ValueError('The installed toolkit distribution metadata is missing or ambiguous')
    result[str(metadata[0]).replace('\\', '/')] = Path(toolkit.locate_file(metadata[0])).read_bytes()
    pdf = importlib.util.find_spec('pymupdf')
    if pdf and pdf.origin:
        result.update(pdf_layout_data(Path(pdf.origin).parent))
    return result


PDF_LAYOUT_RESOURCES = 'pymupdf/layout/resources/'
PDF_LAYOUT_MODEL_SUFFIX = '.onnx'


def pdf_layout_data(package):
    return {'pymupdf/' + path.relative_to(package).as_posix(): path.read_bytes()
            for path in (package / 'layout/resources').rglob('*')
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != PDF_LAYOUT_MODEL_SUFFIX}


def unused_pdf_layout_models(reader):
    """The ONNX layout models of pymupdf-layout: only pymupdf.layout runs them, and the app never imports it."""
    return [f'Unused PDF layout model is bundled: {name}' for name in sorted(reader.toc)
            if normalized(name).startswith(PDF_LAYOUT_RESOURCES)
            and normalized(name).endswith(PDF_LAYOUT_MODEL_SUFFIX)]


def check_code(code, source):
    expected = compile(source, code.co_filename, 'exec', dont_inherit=True, optimize=1)
    return code == expected


def check_modules(reader, root=ROOT):
    pyz_name = next(name for name in reader.toc if name.lower().endswith('.pyz'))
    pyz = reader.open_embedded_archive(pyz_name)
    checked, errors = [], []
    for module in sorted(pyz.toc):
        if module != 'config' and module != 'src' and not module.startswith('src.'):
            continue
        source = root / (module.replace('.', '/') + '.py')
        if not source.is_file():
            source = root / module.replace('.', '/') / '__init__.py'
        if not source.is_file():
            errors.append(f'Packaged module has no current source: {module}')
            continue
        if not check_code(pyz.extract(module), source.read_bytes()):
            errors.append(f'Stale/different code: {module}')
        checked.append(module)
    for module in sorted(ESSENTIAL_MODULES - set(checked)):
        errors.append(f'Missing required module: {module}')
    if 'main' not in reader.toc:
        errors.append('Missing entry script: main')
    else:
        code = marshal.loads(reader.extract('main'))
        if not check_code(code, (root / 'main.py').read_bytes()):
            errors.append('Stale/different entry script: main')
        checked.append('main')
    return checked, errors


def ico_frame_bytes(ico_bytes: bytes) -> dict[int, bytes]:
    """Parse square frame payloads from an ICO file keyed by pixel size."""
    if len(ico_bytes) < 6:
        raise ValueError('Invalid ICO header')
    _reserved, icon_type, count = struct.unpack_from('<HHH', ico_bytes, 0)
    if icon_type != 1 or count == 0:
        raise ValueError('Invalid ICO directory')
    frames = {}
    for idx in range(count):
        width, height, _colors, _res, _planes, _bpp, size, offset = struct.unpack_from(
            '<BBBBHHII', ico_bytes, 6 + idx * 16)
        w = 256 if width == 0 else width
        h = 256 if height == 0 else height
        if w != h:
            raise ValueError(f'Non-square ICO frame: {w}x{h}')
        frames[w] = ico_bytes[offset:offset + size]
    return frames


def check_exe_icon(pe, root=ROOT) -> list[str]:
    """Verify the PE executable carries every DPI frame of CrossTrans.ico with matching bytes."""
    entries = getattr(getattr(pe, 'DIRECTORY_ENTRY_RESOURCE', None), 'entries', ())
    by_id = {getattr(item, 'id', None): item for item in entries}
    group_res = by_id.get(14)  # RT_GROUP_ICON
    icon_res = by_id.get(3)    # RT_ICON
    if group_res is None or not getattr(getattr(group_res, 'directory', None), 'entries', ()):
        return ['The EXE has no group-icon resource']
    if icon_res is None or not getattr(getattr(icon_res, 'directory', None), 'entries', ()):
        return ['The EXE has no icon image resources']

    rt_icons = {}
    for entry in icon_res.directory.entries:
        lang_entries = getattr(getattr(entry, 'directory', None), 'entries', ())
        if lang_entries:
            struct_data = lang_entries[0].data.struct
            rt_icons[entry.id] = pe.get_data(struct_data.OffsetToData, struct_data.Size)

    group_lang = group_res.directory.entries[0].directory.entries[0].data.struct
    group_data = pe.get_data(group_lang.OffsetToData, group_lang.Size)
    _reserved, _icon_type, count = struct.unpack_from('<HHH', group_data, 0)
    exe_frames = {}
    errors = []
    for idx in range(count):
        width, height, _colors, _res, _planes, _bpp, _size, icon_id = struct.unpack_from(
            '<BBBBHHIH', group_data, 6 + idx * 14)
        w = 256 if width == 0 else width
        h = 256 if height == 0 else height
        if w != h:
            errors.append(f'Non-square EXE icon frame: {w}x{h}')
            continue
        if icon_id not in rt_icons:
            errors.append(f'Missing RT_ICON payload for {w}px frame (id={icon_id})')
            continue
        exe_frames[w] = rt_icons[icon_id]

    missing = [size for size in brand.ICO_SIZES if size not in exe_frames]
    if missing:
        errors.append(f'Missing EXE icon frame sizes: {missing}')

    source_ico = root / 'CrossTrans.ico'
    if not source_ico.is_file():
        errors.append(f'Missing source icon for comparison: {source_ico}')
    else:
        expected_frames = ico_frame_bytes(source_ico.read_bytes())
        for size, expected_payload in sorted(expected_frames.items()):
            if size in exe_frames and exe_frames[size] != expected_payload:
                errors.append(f'Stale/different EXE icon frame: {size}px')
    return errors


def verify(path, root=ROOT):
    from PyInstaller.archive.readers import CArchiveReader
    import pefile
    reader = CArchiveReader(str(path))
    expected = expected_data(root)
    errors = check_data(reader, expected)
    errors.extend(unused_pdf_layout_models(reader))
    errors.extend(check_tcl_tk(reader))
    modules, issues = check_modules(reader, root)
    errors.extend(issues)
    with pefile.PE(str(path), fast_load=False) as pe:
        icon_errors = check_exe_icon(pe, root)
    icon = not icon_errors
    errors.extend(icon_errors)
    retired = ('add_btn_blue.png', 'add_btn_gray.png', 'add_btn_light.png', 'add_btn_light_blue.png')
    for name in reader.toc:
        if normalized(name).rsplit('/', 1)[-1] in retired:
            errors.append(f'Retired button asset is still bundled: {name}')
    return {'exe': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'bytes': path.stat().st_size, 'data_files_checked': len(expected),
            'project_modules_checked': modules, 'exe_icon': icon, 'errors': errors}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--exe', required=True, type=Path)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args(argv)
    report = verify(args.exe)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f"UI bundle: {len(report['project_modules_checked'])} modules, "
          f"{report['data_files_checked']} data files, EXE icon {report['exe_icon']}")
    for error in report['errors']:
        print('FAILED:', error)
    print('SHA256:', report['sha256'])
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
