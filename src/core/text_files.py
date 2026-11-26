"""Shared, strict decoding for text attachments, independent of their filename suffix."""
from pathlib import Path
import unicodedata


TEXT_EXTENSIONS = ('.txt', '.md', '.markdown', '.csv', '.tsv', '.json', '.jsonl', '.xml',
                   '.html', '.htm', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf',
                   '.log', '.rst', '.tex', '.vtt', '.py', '.js', '.ts', '.css')
EXTRACTOR_EXTENSIONS = ('.docx', '.srt', '.pdf')
MAX_TEXT_BYTES = 20 * 1024 * 1024


def decode_text(raw: bytes) -> str:
    """Never replace undecodable bytes or accept binary data merely because it has a text suffix."""
    if not raw:
        return ''
    if raw.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
        encodings = ['utf-32']
    elif raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        encodings = ['utf-16']
    elif raw.startswith(b'\xef\xbb\xbf'):
        encodings = ['utf-8-sig']
    else:
        encodings = ['utf-8', 'cp932']
        try:
            import chardet
            result = chardet.detect(raw)
            detected = result.get('encoding')
            if detected and result.get('confidence', 0) >= 0.80:
                encodings.append(detected)
        except ImportError:
            pass
    for encoding in dict.fromkeys(encodings):
        try:
            text = raw.decode(encoding, errors='strict')
        except (UnicodeError, LookupError):
            continue
        if any(unicodedata.category(char) == 'Cc' and char not in '\t\n\r\f' for char in text):
            continue
        return text
    raise ValueError('This file is binary or its text encoding could not be identified.')


def read_text_file(path) -> str:
    """Read a bounded file without newline conversion, preserving the original text structure."""
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_TEXT_BYTES + 1)
    if len(raw) > MAX_TEXT_BYTES:
        raise ValueError('Text attachments must be 20 MB or smaller.')
    return decode_text(raw)


def validate_document(path) -> None:
    """Browse and drop use the same check as the extraction pipeline."""
    if not Path(path).is_file():
        raise ValueError('Choose a readable file, not a folder.')
    if Path(path).suffix.lower() not in EXTRACTOR_EXTENSIONS:
        read_text_file(path)
