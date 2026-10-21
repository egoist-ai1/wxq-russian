"""Защищённый китайский текст в переводе: остаётся дословно, не считается «непереведённым».

Контракт core.target_issues (закреплён замороженными отчётами по SHA, не меняется) отклоняет любой китайский знак в переводе
('unreviewed-cjk-in-target'). Здесь из этого замечания вычитаются ровно два класса токенов, и только в том числе, в каком они
стоят в исходнике (мультимножество):
  - алиас эмодзи из ЗАКРЫТОГО списка (70 строк, localization/protocol-display-aliases.json,
    поле protected_cjk_nt_ids): чат подставляет значок по строке (game-talk-emoji-localization.md); любая другая `[行动]` - ошибка;
  - код-пароль целиком: `【основа{n}】` или `основа{n}`, основа из закрытого списка TOKEN_STEMS, {n} - плейсхолдер из цифр,
    скобки 【】 вплотную (OperationUtility.lua:214-232 ищет «【ключевое слово+код】» и «ключевое слово+код»). Основа без кода
    («万象结伴活动») не защищена.
Любой другой китайский знак в переводе остаётся ошибкой.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from localization import core  # noqa: E402

ISSUE = 'unreviewed-cjk-in-target'
CJK = re.compile(r'[㐀-鿿]')
TOKEN_STEMS = ('万象结伴', '伴友同行', '天使投资人')
CONTRACT = PROJECT / 'localization/protocol-display-aliases.json'
_ALIAS = re.compile(r'\[[㐀-鿿]{1,4}\]')


def load_aliases(path: Path = CONTRACT) -> tuple[str, ...]:
    """Закрытый список алиасов: исходники записей, чей nt_id перечислен в protected_cjk_nt_ids."""
    data = json.loads(path.read_text(encoding='utf-8'))
    protected = set(data['protected_cjk_nt_ids'])
    aliases = sorted({entry['source'] for entry in data['entries'] if entry['nt_id'] in protected}, key=lambda a: (-len(a), a))
    bad = [a for a in aliases if not _ALIAS.fullmatch(a)]
    if bad or len(aliases) != len(protected):
        raise ValueError(f'protocol-display-contract: ожидалось {len(protected)} алиасов вида [汉字], получено {len(aliases)}, не вида: {bad}')
    return tuple(aliases)


ALIASES = load_aliases()
_STEMS = '|'.join(map(re.escape, TOKEN_STEMS))
_CODE = r'\{\d+\}'
PROTECTED = re.compile('|'.join(map(re.escape, ALIASES)) + rf'|【(?:{_STEMS}){_CODE}】|(?:{_STEMS}){_CODE}')


def _plain(text: str) -> str:
    """Текст без разметки; аргументы `{n}` остаются: код-пароль узнаётся вместе с ними."""
    return core._without_tags(text)


def residual_cjk(source: str, target: str) -> str:
    """Видимый текст перевода без защищённых токенов (не больше, чем их в исходнике)."""
    budget = Counter(PROTECTED.findall(_plain(source)))

    def keep(match: re.Match) -> str:
        token = match.group()
        if budget[token] > 0:
            budget[token] -= 1
            return ''
        return token
    return core._without_arguments(PROTECTED.sub(keep, _plain(target)))


def filter_issues(source: str, target: str, issues: list[str]) -> list[str]:
    """issues без 'unreviewed-cjk-in-target', если весь китайский текст перевода — защищённые токены."""
    if ISSUE in issues and not CJK.search(residual_cjk(source, target)):
        return [issue for issue in issues if issue != ISSUE]
    return list(issues)
