"""Пробел между числом/токеном и токеном-словом: «1 Энергия», «Опыт шахматиста +2».

Китайскому тексту пробел не нужен: `获得{0|1}{Energy}`. Игра раскрывает `{Energy}` в слово («Энергия»), поэтому русский
шаблон `получите {0|1}{Energy}` даёт «1Энергия», а `{LordExp}+{3|2}` - «Опыт шахматиста+2».

Контракт core.target_issues закреплён замороженными отчётами по SHA и не меняется (как в protected_cjk.py), поэтому проверка
стоит рядом: `build_carrier.row_issues` (а через неё сборка носителя и `merge_new_translations.py`) добавляет замечание
`token-word-adjacency`; `scripts/normalize_token_spacing.py` исправляет источники теми же правилами.

Токены-слова - закрытый список WORD_TOKENS (проверено по носителю 1.6.0.1: прочие `{Имя}` - число X, имя героя HeroName, P1 -
с числом не слипаются). Правила:
  R1  пробел перед токеном-словом, если ему сразу предшествует цифра, `}` или `]`;
  R2  пробел после токена-слова, если сразу за ним (с необязательным +/-) идёт цифра или `{`.
Не затрагиваются: содержимое токенов, диапазоны `{0|1}-{0|5}`, `50+80%`, `[2]+[3]%`, «Ур. 10», `{0}/{1}`, а также числовой
токен вплотную к кириллице (`{0}с`) - это отдельный случай, правило его не охватывает.
"""
from __future__ import annotations

import re

ISSUE = 'token-word-adjacency'
WORD_TOKENS = ('Energy', 'LordHp', 'LordExp')
_WORD_TOKEN = r'\{(?:' + '|'.join(WORD_TOKENS) + r')\}'
BEFORE = re.compile(r'(?<=[\d}\]])(?=' + _WORD_TOKEN + ')')
AFTER = re.compile('(' + _WORD_TOKEN + r')(?=[+\-]?(?:\d|\{))')


def space_token_words(text: str) -> str:
    """Текст с пробелами по R1 и R2; повторный вызов ничего не меняет."""
    return AFTER.sub(r'\1 ', BEFORE.sub(' ', text))


def positions(text: str) -> list[int]:
    """Индексы, куда нужно вставить пробел."""
    return sorted([m.start() for m in BEFORE.finditer(text)] + [m.end() for m in AFTER.finditer(text)])


def spots(text: str) -> list[str]:
    """Слипшиеся места: до 8 знаков контекста слева и справа от места пробела, разделены `|`."""
    return [text[max(0, i - 8):i] + '|' + text[i:i + 8] for i in positions(text)]


def issues(target: str) -> list[str]:
    """['token-word-adjacency'], если в переводе токен-слово слеплен с числом или токеном, иначе []."""
    return [ISSUE] if BEFORE.search(target) or AFTER.search(target) else []
