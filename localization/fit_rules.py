"""Правила вёрстки русского текста для места в интерфейсе: только данные и чистые функции.

Шесть правил (пилот допустимости, 13.10.2026):
1. Шрифт и размер не меняются: место описывается только текстом и бюджетом, поля
   font/size/style/material в описании места отвергаются (`font-size-change-forbidden`).
   Бинарный контракт сборщика меняет лишь текстовые поля (`prove_text_only`) или
   пиксели внутри маски; поля шрифта и размера он не затрагивает.
2. Бюджет длины задаёт сама запись: максимум видимых символов и/или ширина в пикселях
   по оригиналу. Если полная форма не влезает, берётся короткая (`ru_short` глоссария).
3. Перенос только по целым словам (по ASCII-пробелам вне защищённых диапазонов);
   слово длиннее строки — ошибка, слова не делятся. Предлог/союз склеивается со
   следующим словом, число с единицей («5 ур.») не разрывается.
4. В шрифтах игры OSGameFont_Title/Content нет U+00A0 и U+2212 (проверено по cmap):
   в итоговом тексте их быть не должно, `emit_for_game` меняет их на ASCII-пробел и «-»
   и возвращает диапазоны, где перенос запрещён.
5. Числовой шрифт без кириллицы: поле `kind="number"` принимает только ASCII-цифры и
   знаки числа.
6. Если известен набор глифов шрифта, любой видимый символ вне него — ошибка.

Ширина оценивается как `символы * char_width_px` и подтверждением в клиенте не является:
реальную меру даёт `core.select_fitted_variant` с измерениями TimiText.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import struct
from typing import Iterable

from localization import core

HARD_BREAK = chr(10)
NBSP = chr(0xA0)
MINUS = chr(0x2212)
# Символы, которых нет в игровом шрифте, и их ASCII-замены (проверка по cmap, см. glyph_codepoints).
GAME_FONT_REPLACEMENTS = {NBSP: " ", MINUS: "-"}
FORBIDDEN_SLOT_KEYS = frozenset({"font", "font_name", "font_size", "size", "font_style", "style",
                                 "material", "line_spacing", "tracking"})
# Короткие слова, которые не должны оставаться в конце строки (предлоги, союзы, частицы).
GLUE_WORDS = frozenset("""а без в во для до за и из или к ко на над не ни но о об от по под при про с со у""".split())
_WORD = re.compile(r"[^\s]+")
_ARGUMENT = re.compile(r"\{[A-Za-z0-9_]+\}|\[\d+\]|%(?:\d+\$)?[-+#0 ]*\d*(?:\.\d+)?[sdfiu]|%%")
# Единицы, которые не отрываются от числа («5 ур.», «3 шт.»); общие из core.py учитываются отдельно.
_NUMBER_WITH_UNIT = re.compile(
    r"(?<![\w])[-+]?\d+(?:[.,]\d+)?( )(?:ур\.|ед\.|шт\.|очк\.|оч\.|раз(?:а)?|мин\.|сек\.|ход(?:а|ов)?)(?![\w])")
_NUMBER_CHARS = frozenset("0123456789+-.,:%/ x")


@dataclass(frozen=True)
class Slot:
    """Место вывода: бюджет по оригиналу. Шрифта и размера здесь нет намеренно."""
    max_chars: int | None = None        # максимум видимых символов (без тегов и аргументов) во всём тексте
    max_width_px: float | None = None   # допустимая ширина строки
    char_width_px: float | None = None  # оценка ширины символа для max_width_px
    line_chars: int | None = None       # символов в строке; для переноса и проверки слов
    max_lines: int = 1
    kind: str = "text"                  # "text" | "number"
    glyphs: frozenset | None = None     # кодовые точки шрифта, если известны

    def __post_init__(self):
        if self.kind not in ("text", "number"):
            raise ValueError("slot-kind")
        for name in ("max_chars", "line_chars"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"slot-{name}")
        if type(self.max_lines) is not int or self.max_lines < 1:
            raise ValueError("slot-max_lines")
        if (self.max_width_px is None) != (self.char_width_px is None):
            raise ValueError("slot-width-needs-char-width")

    @classmethod
    def from_mapping(cls, value: dict) -> "Slot":
        """Строгая сборка из JSON; любые поля шрифта/размера отвергаются."""
        if not isinstance(value, dict):
            raise ValueError("slot-must-be-object")
        bad = sorted(set(value) & FORBIDDEN_SLOT_KEYS)
        if bad:
            raise ValueError("font-size-change-forbidden: " + ", ".join(bad))
        allowed = {"max_chars", "max_width_px", "char_width_px", "line_chars", "max_lines", "kind"}
        extra = sorted(set(value) - allowed)
        if extra:
            raise ValueError("slot-unknown-fields: " + ", ".join(extra))
        return cls(**value)


def visible_text(text: str) -> str:
    """Текст без тегов разметки и аргументов формата: то, что игрок видит как буквы."""
    return _ARGUMENT.sub("", core._TAG.sub("", text))


def visible_length(text: str) -> int:
    return len(visible_text(text))


def glyph_codepoints(font: bytes) -> frozenset:
    """Кодовые точки cmap (форматы 4 и 12) шрифта OTF/TTF без сторонних библиотек."""
    if len(font) < 12:
        raise ValueError("font-too-short")
    count = struct.unpack_from(">H", font, 4)[0]
    cmap = None
    for index in range(count):
        tag, _checksum, offset, _length = struct.unpack_from(">4sIII", font, 12 + 16 * index)
        if tag == b"cmap":
            cmap = offset
    if cmap is None:
        raise ValueError("font-has-no-cmap")
    points: set[int] = set()
    _version, tables = struct.unpack_from(">HH", font, cmap)
    for index in range(tables):
        _platform, _encoding, sub = struct.unpack_from(">HHI", font, cmap + 4 + 8 * index)
        base = cmap + sub
        form = struct.unpack_from(">H", font, base)[0]
        if form == 4:
            segments = struct.unpack_from(">H", font, base + 6)[0] // 2
            ends = struct.unpack_from(f">{segments}H", font, base + 14)
            starts = struct.unpack_from(f">{segments}H", font, base + 16 + 2 * segments)
            for start, end in zip(starts, ends):
                if start != 0xFFFF:
                    points.update(range(start, end + 1))
        elif form == 12:
            groups = struct.unpack_from(">I", font, base + 12)[0]
            for group in range(groups):
                start, end, _glyph = struct.unpack_from(">III", font, base + 16 + 12 * group)
                points.update(range(start, end + 1))
    return frozenset(points)


def protected_ranges(text: str) -> list[tuple[int, int]]:
    """Диапазоны, где перенос запрещён: NBSP-связки, предлог+слово, число+единица.

    Индексы относятся к самому `text` (NBSP считается пробелом той же длины), поэтому
    остаются верными после `emit_for_game`.
    """
    ranges = [(i, i + 1) for i, c in enumerate(text) if c == NBSP]
    ranges += [(a, b) for a, b, _ in core._argument_spans(text)]
    spaced = text.replace(NBSP, " ")
    for match in _WORD.finditer(spaced):
        word = core._TAG.sub("", match.group()).strip("«»\"'(),.;:!?").lower()
        after = match.end()
        if word in GLUE_WORDS and after < len(spaced) and spaced[after] == " ":
            nxt = _WORD.match(spaced, after + 1)
            if nxt:
                ranges.append((after, after + 1))
    ranges += [(m.start(1), m.end(1)) for m in _NUMBER_WITH_UNIT.finditer(spaced)]
    return sorted(set(ranges))


def break_points(text: str) -> list[int]:
    """Индексы пробелов, в которых допустим перенос (по целым словам)."""
    spaced = text.replace(NBSP, " ")
    return core.break_opportunities(spaced, protected_ranges(text))


def emit_for_game(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Текст для ресурса игры и диапазоны запрета переноса.

    U+00A0 и U+2212 заменяются на ASCII, потому что в шрифте игры для них нет глифов.
    """
    ranges = protected_ranges(text)
    for old, new in GAME_FONT_REPLACEMENTS.items():
        text = text.replace(old, new)
    return text, ranges


def unbreakable_runs(text: str) -> list[list[str]]:
    """По жёстким строкам: куски, между которыми перенос разрешён (слово или связка слов)."""
    points = set(break_points(text))
    lines, position = [], 0
    for hard in text.split(HARD_BREAK):
        tokens, start = [], 0
        for index in range(len(hard)):
            if position + index in points:
                tokens.append(hard[start:index])
                start = index + 1
        tokens.append(hard[start:])
        lines.append(tokens)
        position += len(hard) + 1
    return lines


def wrap_lines(text: str, line_chars: int) -> list[str]:
    """Жадный перенос по разрешённым пробелам; слово не режется. Жёсткие переносы сохраняются."""
    result: list[str] = []
    for tokens in unbreakable_runs(text):
        line = ""
        for token in tokens:
            candidate = token if not line else line + " " + token
            if line and visible_length(candidate) > line_chars:
                result.append(line)
                line = token
            else:
                line = candidate
        result.append(line)
    return result


def fit_report(source_cn: str, ru_text: str, slot: Slot | dict) -> list[str]:
    """Список нарушений вёрстки; пустой список — укладывается по оценке.

    Смысл, плейсхолдеры, числа и разметку проверяет `core.target_issues`: здесь только вёрстка.
    """
    if isinstance(slot, dict):
        try:
            slot = Slot.from_mapping(slot)
        except ValueError as error:
            return [str(error)]
    issues: list[str] = []
    try:
        core.text_check(ru_text)
        core.markup_tokens(ru_text)
    except ValueError as error:
        return [str(error)]
    for char, replacement in GAME_FONT_REPLACEMENTS.items():
        if char in ru_text:
            issues.append(f"glyph-missing-U+{ord(char):04X}-use-ascii-{replacement!r}")
    visible = visible_text(ru_text)
    if slot.kind == "number":
        if any(c not in _NUMBER_CHARS for c in visible):
            issues.append("non-numeric-glyphs-in-number-field")
    if slot.glyphs is not None:
        missing = sorted({c for c in visible if c not in "\n\r" and ord(c) not in slot.glyphs})
        if missing:
            issues.append("glyph-missing:" + ",".join(f"U+{ord(c):04X}" for c in missing[:8]))
    length = len(visible.replace("\n", ""))
    if slot.max_chars is not None and length > slot.max_chars:
        issues.append(f"over-length-budget:{length}>{slot.max_chars}")
    if slot.max_width_px is not None and length * slot.char_width_px > slot.max_width_px * slot.max_lines:
        issues.append(f"over-width-budget:{length * slot.char_width_px:.0f}>{slot.max_width_px * slot.max_lines:.0f}px")
    if slot.line_chars is not None:
        spaced, _ = emit_for_game(ru_text)
        for tokens in unbreakable_runs(spaced):
            for token in tokens:
                if visible_length(token) > slot.line_chars:
                    kind = "unbreakable-run" if " " in token else "word"
                    issues.append(f"{kind}-longer-than-line:{token}")
        lines = wrap_lines(spaced, slot.line_chars)
        if len(lines) > slot.max_lines:
            issues.append(f"too-many-lines:{len(lines)}>{slot.max_lines}")
    return issues


def load_short_forms(path: Path) -> dict[str, str]:
    """{cn: ru_short} из glossary.json; термины без ru_short пропускаются."""
    data = json.loads(Path(path).read_text("utf-8-sig"))
    return {t["cn"]: t["ru_short"] for t in data.get("terms", []) if t.get("ru_short")}


def choose_variant(source_cn: str, variants: Iterable[tuple[str, str]], slot: Slot | dict) -> tuple[str, str, list[str]]:
    """Первая форма из `variants` ((имя, текст), от полной к короткой), прошедшая fit_report.

    Возвращает (имя, текст, issues). Если не подошла ни одна, имя пустое, а issues — у последней формы.
    """
    last: tuple[str, str, list[str]] = ("", "", ["no-variants"])
    for name, text in variants:
        issues = fit_report(source_cn, text, slot)
        if not issues:
            return name, text, []
        last = ("", text, issues)
    return last
