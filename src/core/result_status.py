"""Explicit status markers for translation and notice strings.

Both classes subclass `str` so every caller and test that inspects string values
(`.startswith()`, equality, slicing, Tk insertion, JSON serialization) keeps working
unchanged while error predicates no longer confuse real translations starting with
"Error:" or "No text" with system failures, or mistake non-error notices like
cooldown/empty-prompt messages for replaceable translations.
"""


class FailureText(str):
    """A failure or non-replaceable status notice returned along a text channel.

    Attributes:
        is_error: Always True so `is_error_text()` recognizes this value regardless
            of its wording or UI language.
        raw_reason: Canonical English error text used by `popup_errors.explain()`
            even when the displayed string has been localized.
        status_kind: 'error' for failures or 'notice' for calm informational notices
            (cooldown, empty prompt, nothing to re-translate).
    """

    is_error: bool = True

    def __new__(cls, text: str, *, raw_reason: str = '', kind: str = 'error'):
        value = super().__new__(cls, text)
        value.raw_reason = raw_reason or str(text)
        value.status_kind = kind
        return value


class TranslationText(str):
    """A genuine model translation or grammar-fix output.

    Marks `is_error = False` so outputs that legitimately start with "Error:" or
    "No text" (e.g. translating UI error strings) are never misclassified as failures.
    """

    is_error: bool = False


def mark_translation(text):
    """Mark a plain `str` as a genuine translation when it would otherwise look like an error."""
    if not isinstance(text, str) or not text:
        return text
    if getattr(text, 'is_error', None) is not None:
        return text
    if type(text) is str and text.startswith(("Error:", "No text")):
        return TranslationText(text)
    return text


def is_error_text(translated: str) -> bool:
    """Whether a result string is a failure/status notice rather than a translation."""
    if not translated:
        return False
    marker = getattr(translated, 'is_error', None)
    if marker is not None:
        return bool(marker)
    if not isinstance(translated, str):
        return False
    return translated.startswith("Error:") or translated.startswith("No text")
