"""Исключения «битая разметка в исходнике»: 4 уникальных текста игры, где сам оригинал нарушает контракт тегов.

localization/core.py (контракт закреплён замороженными отчётами по SHA, не меняется) отклоняет пару, если разметка исходника
не парная или содержит неизвестный тег: 'unbalanced-markup' / 'unknown-markup'. Для четырёх текстов это дефект самого оригинала
(`<a>╫牺牲</color></a>`, `color=#dfa15e>]`, незакрытый `<color=#ffffff>`, `<color=#98cbffF>`), игра читает их как есть.

Снимаются ровно эти два замечания, и только если выполнены ВСЕ условия:
  1. пара (SHA-256 UTF-8 исходника, SHA-256 UTF-8 перевода) перечислена в glossary/markup_exceptions.json (закрытый список);
  2. упорядоченная последовательность технических фрагментов (теги кодека, `{n}`/printf/`[N]`, значки ═ ╫, неизвестные теги и
     мусорные куски вида `color=#dfa15e>]`) в исходнике и переводе совпадает байт в байт;
  3. число символов `<>{}[]=#` в исходнике и переводе одинаково.
Остальные проверки без исключений: core возвращает управление раньше, когда разметка исходника битая, поэтому здесь они
запускаются на тексте с вырезанными техническими фрагментами (числа, знаки, латиница, китайский текст, переводы строк).
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import AbstractSet, Iterable

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from localization import core, latin_allow  # noqa: E402

PATH = PROJECT / 'glossary/markup_exceptions.json'
SCHEMA = 'wxq-markup-exceptions/v1'
WAIVED = frozenset({'unbalanced-markup', 'unknown-markup'})
_SHA256 = re.compile(r'[0-9a-f]{64}')
_SPECIAL = frozenset('<>{}[]=#')
_FRAGMENT = re.compile('|'.join((
    f'(?i:{core._TAG.pattern})',        # теги кодека
    core._PLACEHOLDER.pattern,          # {n}, printf, [N]
    '[═╫]',                             # значки-маркеры игры
    r'</?[A-Za-z][^<>]*>',              # неизвестный тег (`<color=#98cbffF>`)
    r'color=#[0-9A-Fa-f]+>\]?',         # мусорный кусок без `<`
)))


def load(path: Path = PATH) -> frozenset[tuple[str, str]]:
    """Закрытый список пар (SHA исходника, SHA перевода); неверный формат файла - ValueError."""
    if not path.is_file():
        return frozenset()
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') != SCHEMA or not isinstance(data.get('pairs'), list):
        raise ValueError(f'{path.name}: ожидалась схема {SCHEMA} со списком pairs')
    pairs = set()
    for row in data['pairs']:
        source, target = row.get('source_sha256'), row.get('target_sha256')
        if not (isinstance(source, str) and isinstance(target, str) and _SHA256.fullmatch(source) and _SHA256.fullmatch(target)):
            raise ValueError(f'{path.name}: пара должна состоять из двух SHA-256 (строчные hex): {row!r}')
        pairs.add((source, target))
    return frozenset(pairs)


def fragments(text: str) -> tuple[str, ...]:
    """Технические фрагменты текста по порядку."""
    return tuple(match.group() for match in _FRAGMENT.finditer(text))


def rule_problems(source: str, target: str) -> list[str]:
    """Пусто, если перевод держит разметку исходника байт в байт; иначе названа причина."""
    problems = []
    if fragments(source) != fragments(target):
        problems.append('markup-exception-fragments-differ')
    if Counter(c for c in source if c in _SPECIAL) != Counter(c for c in target if c in _SPECIAL):
        problems.append('markup-exception-special-chars-differ')
    return problems


def cleaned(source: str, target: str) -> tuple[str, str]:
    """Пара без технических фрагментов: на ней выполняются проверки, которые встали бы на битой разметке."""
    return _FRAGMENT.sub('', source), _FRAGMENT.sub('', target)


def accepts(source: str, target: str, emitted: str | None = None, *,
            exceptions: AbstractSet[tuple[str, str]] | None = None) -> bool:
    """Пара есть в закрытом списке и перевод (в виде, уходящем в игру) держит разметку исходника."""
    pairs = load() if exceptions is None else exceptions
    return (core.digest(source), core.digest(target)) in pairs and not rule_problems(source, target if emitted is None else emitted)


def filter_issues(source: str, target: str, issues: list[str], *, emitted: str | None = None,
                  exceptions: AbstractSet[tuple[str, str]] | None = None,
                  semantic_proofs: AbstractSet[tuple[str, str]] = frozenset(),
                  engine_latin: Iterable[str] = latin_allow.BUILTIN) -> list[str]:
    """issues без 'unbalanced-markup'/'unknown-markup' для пары из списка при выполненном правиле.

    target - текст перевода в носителе (по нему сверяется пара); emitted - текст, который уйдёт в игру (по умолчанию тот же).
    Остальные замечания остаются; проверки core, пропущенные при битой разметке, выполняются на тексте без фрагментов."""
    if not WAIVED.intersection(issues):
        return list(issues)
    pairs = load() if exceptions is None else exceptions
    if (core.digest(source), core.digest(target)) not in pairs:
        return list(issues)
    sent = target if emitted is None else emitted
    problems = rule_problems(source, sent)
    if problems:
        return list(issues) + problems
    rest = [issue for issue in issues if issue not in WAIVED]
    clean_source, clean_sent = cleaned(source, sent)
    rest += core.target_issues(clean_source, clean_sent, semantic_proofs, (), formatter={}, engine_latin=engine_latin)
    return list(dict.fromkeys(rest))
