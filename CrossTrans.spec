# -*- mode: python ; coding: utf-8 -*-

# CrossTrans v2.0.0 - PyInstaller Build Configuration
# Build command: pyinstaller CrossTrans.spec --clean --noconfirm

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

sys.path.insert(0, str(Path.cwd()))
from src.utils.tcl_tk_bundle import collect_pyinstaller_tcl_tk_datas, collect_tkinterdnd2_tcl9

tkdnd_tcl9_datas, tkdnd_tcl9_binaries = collect_tkinterdnd2_tcl9()

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[
        *tkdnd_tcl9_binaries,
    ],
    datas=[
        ('src/assets', 'src/assets'),  # Icons and images
        ('src/ui/locales', 'src/ui/locales'),  # English/Japanese interface catalog
        ('src/core/data', 'src/core/data'),  # Offline world-language catalog
        ('docs/screenshots', 'docs/screenshots'),  # Guide tab screenshots
        *collect_pyinstaller_tcl_tk_datas(Path('build') / 'tcl_tk_runtime'),  # Tcl/Tk 9.0 zipfs script libraries
        *tkdnd_tcl9_datas,  # Tcl 9.0 TkDnD scripts
        # PyMuPDF data: the layout YAML files a frozen import needed on 2026-10-24, without the 42 MB of
        # ONNX layout models, which only pymupdf.layout runs and the app never imports (DEFERRED D28)
        *collect_data_files('pymupdf', excludes=['layout/resources/**/*.onnx']),
        *collect_data_files('pykakasi'),  # Furigana dictionary data files
        *copy_metadata('ttkbootstrap'),  # __version__ reads distribution metadata in a frozen app
        *collect_data_files('ttkbootstrap'),  # Element sprites and Bootstrap icon font/maps
    ],
    hiddenimports=[
        # GUI framework
        'ttkbootstrap',
        'ttkbootstrap.dialogs',
        'ttkbootstrap.constants',
        'ttkbootstrap.style',
        'ttkbootstrap.style.icons',
        'ttkbootstrap.themes',
        'ttkbootstrap.themes.standard',
        'ttkbootstrap.localization',
        'ttkbootstrap.widgets.tooltip',
        'ttkbootstrap.widgets.scrolled',
        'ttkbootstrap.widgets.tableview',
        # Windows APIs
        'win32clipboard',
        'win32con',
        'win32api',
        'pywintypes',
        # Diagnostics & self-check UI
        'src.utils.diagnostics',
        'src.ui.diagnostics_dialog',
        # Status markers & universal selection capture
        'src.core.result_status',
        'src.core.selection_capture',
        # Other dependencies
        'keyboard',
        'packaging',
        'packaging.version',
        'config',
        'pyaudiowpatch',
        'av',
        # Character sets src/core/lang_id.py reads glyph evidence from. Looked up
        # by name at run time, so nothing imports them where PyInstaller can see.
        'encodings.euc_jis_2004',
        'encodings.cp932',
        'encodings.gb2312',
        'encodings.big5hkscs',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['src/utils/pyi_rth_boot.py'],
    excludes=[
        # Exclude unnecessary modules to reduce EXE size
        'pytest',
        'pytest_cov',
        'pytest_mock',
        'test',
        'tests',
        # Unused tkinter modules
        'tkinter.test',
        # Unused standard library
        'lib2to3',

        # ============================================
        # Google Generative AI SDK (using REST API instead)
        # ============================================
        'google.generativeai',
        'google.ai',
        'google.api_core',
        'google.auth',
        'google.protobuf',
        'grpc',
        'grpcio',
        'grpcio_status',
        'proto',

        # ============================================
        # NLP packages - downloaded on-demand by user
        # DO NOT bundle in EXE
        # ============================================
        # Core NLP libraries
        'spacy',
        'spacy_legacy',
        'spacy_loggers',
        'jieba',
        'fugashi',
        'unidic',
        'unidic_lite',
        'underthesea',
        'underthesea_core',
        'ufal',
        'ufal.udpipe',
        'pythainlp',
        'kiwipiepy',
        # spaCy dependencies
        'thinc',
        'thinc_apple_ops',
        'cymem',
        'preshed',
        'murmurhash',
        'blis',
        'wasabi',
        'srsly',
        'catalogue',
        'typer',
        'pathy',
        'confection',
        'spacy_pkuseg',
        # Other NLP dependencies
        'sudachipy',
        'sudachidict_core',
        'nagisa',
        'konlpy',
        'mecab',
        'janome',
        'pyvi',
        'pymorphy2',
        'pymorphy3',
        'stanza',
        'nltk',
        'transformers',
        'torch',
        'tensorflow',
        'keras',

        # ============================================
        # Scientific computing - not needed for translation
        # ============================================
        'pandas',
        'pandas._libs',
        'pandas.core',
        'pandas.io',
        'scipy',
        'scipy.linalg',
        'scipy.sparse',
        'scipy.special',
        'scipy.stats',
        'scipy.optimize',
        'scipy.integrate',
        'scipy.interpolate',
        'scipy.signal',
        'scipy.ndimage',
        'scipy.spatial',
        'scipy.constants',
        'scipy.fft',
        'scipy._lib',

        # ============================================
        # Data / filesystem - not needed
        # ============================================
        'fsspec',
        'pyarrow',
        'numba',
        'bottleneck',
        'xarray',
        'h5py',
        'tables',
        'openpyxl',
        'xlrd',
    ],
    noarchive=False,
    optimize=1,  # Basic optimization
)
# pyinstaller-hooks-contrib's hook for pymupdf >= 2.0 collects all of its data files, which brings the ONNX layout
# models back; drop them after the analysis (DEFERRED D28, CHANGELOG Phases 14 and 16).
a.datas = [entry for entry in a.datas
           if not (entry[0].replace('\\', '/').startswith('pymupdf/layout/resources/')
                   and entry[0].endswith('.onnx'))]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='CrossTrans2',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='CrossTrans.ico',
    version='installer/version_info.txt',
)
