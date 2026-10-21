"""Разрешённая латиница в русском переводе: только данные, без записи в игру.

Слово из этого набора не считается «непроверенной латиницей». Сверх набора
core.target_issues пропускает латиницу, которая дословно есть в видимом тексте
исходника (токены и теги игры вырезаются до проверки), и явный
`formatter.latin_allowlist` записи.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

def _load_brands() -> frozenset[str]:
    """Бренды/платформы и римские цифры I-X из latin_brands.json (данные, не код)."""
    data = json.loads((Path(__file__).with_name("latin_brands.json")).read_text(encoding="utf-8"))
    return frozenset(data["brands"]) | frozenset(data["roman_numerals"])


BRANDS = _load_brands()
# Бренд и обозначения, названные владельцем; HP/CD/XP — игровые сокращения.
BUILTIN = frozenset({"Honor", "of", "Kings", "ACE", "QR", "HP", "CD", "XP"}) | BRANDS
# Буквенные единицы времени/объёма; встречаются рядом с числом.
UNITS = frozenset({"s", "ms", "d", "h", "min", "m", "km", "kg", "g", "mg", "px", "Hz", "FPS", "KB", "MB", "GB"})

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]*")


def words(text: str) -> set[str]:
    return {w.rstrip(".:/-") for w in _WORD.findall(text)}


def from_glossary(path: str | Path) -> frozenset[str]:
    """Латинские слова из ru_canon терминов со статусом approved."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    result: set[str] = set()
    for term in data.get("terms", ()):
        if term.get("status") == "approved" and isinstance(term.get("ru_canon"), str):
            result |= words(term["ru_canon"])
    return frozenset(result)


def default_allowlist(glossary_path: str | Path | None = None) -> frozenset[str]:
    """BUILTIN + approved-латиница глоссария (если файл есть)."""
    result = set(BUILTIN)
    if glossary_path is not None and Path(glossary_path).is_file():
        result |= from_glossary(glossary_path)
    return frozenset(result)
