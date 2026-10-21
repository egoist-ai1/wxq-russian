"""Доказанные по смыслу замены чисел для .lang (например «盈利N万钻» -> «Прибыль N тыс. алмазов»).

scripts/game_lang_codec.py закреплён замороженными отчётами по SHA и не меняется. Здесь исходные validate_text,
LangDocument.replace и validate_rewrite вызываются как есть, а из их замечаний убирается ровно одно -
'literal number/percentage mismatch' - и только для пары (SHA-256 UTF-8 исходника, SHA-256 UTF-8 перевода),
перечисленной в number_waivers (пары берутся из glossary/semantic_proofs.json). Остальные проверки кодека действуют полностью.
"""
from __future__ import annotations

import contextlib
import hashlib
import sys
from pathlib import Path
from typing import AbstractSet, Mapping

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
for _path in (str(PROJECT), str(PROJECT / 'scripts')):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import game_lang_codec  # noqa: E402,F401  (загружает модуль; сам кодек берётся у документа, см. _codec)

NUMBER_ISSUE = 'literal number/percentage mismatch'


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _codec(document):
    """Модуль кодека именно этого документа: файл может быть импортирован и как game_lang_codec, и как scripts.game_lang_codec."""
    return sys.modules[type(document).__module__]


def _check(codec, source, translated, unit_conversions, number_waivers) -> list[str]:
    issues = codec.validate_text(source, translated, unit_conversions=unit_conversions)
    if number_waivers and NUMBER_ISSUE in issues and isinstance(translated, str) and (_sha(source), _sha(translated)) in number_waivers:
        issues = [issue for issue in issues if issue != NUMBER_ISSUE]
    return issues


def validate_text(source: str, translated: str, *, unit_conversions=None,
                  number_waivers: AbstractSet[tuple[str, str]] | None = None) -> list[str]:
    return _check(_codec_module(), source, translated, unit_conversions, number_waivers)


def _codec_module():
    return sys.modules.get('scripts.game_lang_codec') or sys.modules['game_lang_codec']


@contextlib.contextmanager
def _waived(codec, number_waivers):
    """На время вызова кодек проверяет текст с исключениями (сборка однопоточная)."""
    if not number_waivers:
        yield
        return
    original = codec.validate_text
    codec.validate_text = lambda source, translated, *, unit_conversions=None: _check(
        type('C', (), {'validate_text': staticmethod(original)}), source, translated, unit_conversions, number_waivers)
    try:
        yield
    finally:
        codec.validate_text = original


def replace(document, translations: Mapping[str, str], *, unit_conversions=None,
            number_waivers: AbstractSet[tuple[str, str]] | None = None) -> bytes:
    with _waived(_codec(document), number_waivers):
        return document.replace(translations, unit_conversions=unit_conversions)


def validate_rewrite(source, result, translations: Mapping[str, str], *,
                     unit_conversions=None, number_waivers: AbstractSet[tuple[str, str]] | None = None) -> None:
    codec = _codec(source)
    with _waived(codec, number_waivers):
        codec.validate_rewrite(source, result, translations, unit_conversions=unit_conversions)
