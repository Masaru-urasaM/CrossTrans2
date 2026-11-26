"""
The Translator's tone chips (TODO.md U7, UQ9, V19).

The custom prompt used to be an always-open box with a hint nobody read. Now it sits under "More
options" and the four tones people ask for most - Formal, Casual, Concise, Natural - are chips that
write the instruction for them; the field stays free text, so a chip is a shortcut, never a
restriction. A chip is lit while its sentence is in the field.

Pure logic, no Tk: text in, text out. The sentences are what the model reads, so they are plain
imperative English whatever language the interface is in.
"""
from typing import Dict, List, Tuple

# (key, label on the chip, the sentence it writes)
TONES: Tuple[Tuple[str, str, str], ...] = (
    ('formal', 'Formal', 'Use a formal, polite tone.'),
    ('casual', 'Casual', 'Use a casual, friendly tone.'),
    ('concise', 'Concise', 'Keep it short and to the point.'),
    ('natural', 'Natural', 'Make it sound natural, the way a native speaker would say it.'),
)

SENTENCES: Dict[str, str] = {key: sentence for key, _, sentence in TONES}
LABELS: Dict[str, str] = {key: label for key, label, _ in TONES}


def active(text: str) -> List[str]:
    """The tone keys whose sentence is in `text`, in the chips' order."""
    return [key for key, _, sentence in TONES if sentence in (text or '')]


def toggle(text: str, key: str) -> str:
    """`text` with the tone `key` switched: its sentence is removed when it is there, added after
    whatever the user wrote when it is not. Other words are left exactly as typed."""
    sentence = SENTENCES[key]
    text = text or ''
    if sentence in text:
        rest = text.replace(sentence, '')
        return ' '.join(rest.split()) if rest.strip() else ''
    return f'{text.rstrip()} {sentence}'.strip()
