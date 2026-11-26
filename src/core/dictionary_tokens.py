"""Shared word-selection tokenization without logging the user's words."""
import logging
import re


def tokenize(text, language=None, manager=None):
    if re.search(r'\w+-\w+', text):
        return re.findall(r'\S+', text)
    if language:
        try:
            if manager is None:
                from src.core.nlp_manager import nlp_manager
                manager = nlp_manager
            if manager.is_installed(language):
                words = manager.tokenize(text, language)
                logging.info('[DICT_TOKENIZE] language=%s chars=%d tokens=%d', language, len(text), len(words))
                return words
        except Exception:
            logging.exception('[DICT_TOKENIZE] Language pack tokenization failed')
    return re.findall(r'\S+', text)


def selection_words(text, language=None, manager=None, annotate=False):
    """Keep the selector's line context and omit punctuation-only lookup targets."""
    from src.core import furigana
    words, readings = [], []
    for line in text.split('\n'):
        if not line.strip():
            continue
        tokens = tokenize(line, language, manager)
        try:
            runs = furigana.annotate_tokens(tokens, line, language) if annotate else [() for _ in tokens]
        except Exception as error:
            safe = RuntimeError('Contextual readings unavailable (' + type(error).__name__ + ')')
            logging.error('[DICT_TOKENIZE] Falling back to plain words',
                          exc_info=(RuntimeError, safe, error.__traceback__))
            runs = [() for _ in tokens]
        for token, segments in zip(tokens, runs):
            if token and re.search(r'[^\W\d_]', token, re.UNICODE):
                words.append(token)
                readings.append([dict(base=run.base, ruby=run.ruby) for run in segments])
    return words, readings
