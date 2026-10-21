"""Каталог, память переводов и ограничения вёрстки. Только данные, без записи в игру.

Native codec остаётся обязательным вторым валидатором при сборке QtsVFS.
Этот модуль не доказывает совместимость носителя с клиентом или античитом.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re
import unicodedata
from typing import Any, Iterable

from . import latin_allow

MAX_TEXT = 16384
MAX_ROWS = 500000
IDENTITY_FIELDS = ("namespace", "resource", "key", "consumer", "variant")
SCHEMA = "wxq-localization-catalog/v1"
# Защищённые токены игрового текста (контракт выведен из данных, см. RU_ENGINE.md):
# {...} без вложенных скобок (имена, {N}, {N|M}, {N:0000}, {Имя:N}) сравниваются как есть;
# printf (%s, %d, %%); коды в квадратных скобках [N], [NpNqN], [NpNqN/N].
_PLACEHOLDER = re.compile(r"\{[^{}]*\}|%(?:\d+\$)?[-+#0 ]*\d*(?:\.\d+)?[sdfiu]|%%|\[(?:\d+|\d+p\d+q\d+(?:/\d+)?)\]")
# \u0427\u0438\u0441\u043b\u043e = \u0433\u0440\u0443\u043f\u043f\u0430 \u0446\u0438\u0444\u0440: \u0437\u043d\u0430\u043a, \u0434\u0435\u0441\u044f\u0442\u0438\u0447\u043d\u0430\u044f \u0442\u043e\u0447\u043a\u0430/\u0437\u0430\u043f\u044f\u0442\u0430\u044f \u0438 \u0440\u0430\u0437\u0440\u044f\u0434\u044b \u043d\u0435 \u0440\u0430\u0437\u043b\u0438\u0447\u0430\u044e\u0442\u0441\u044f (\u0441\u043c. _numbers).
_NUMBER = re.compile(r"(?<![A-Za-z0-9_])\d+")
# Знак числа: `+`, `-`, `−`, `±` сразу перед числом, не после буквы/цифры/токена (`3-5` - диапазон).
_SIGNS = "+-\u2212\u00b1"
_TOKEN_MARK = "\ue000"
_SIGN_BLOCK = re.compile(r"[A-Za-z0-9_\u0400-\u04ff\ue000]")
_THOUSANDS = re.compile(r"(?<![\d.,])\d{1,3}(?:[ ,\u00a0\u202f]\d{3})+(?!\d)")
_HEX = r"[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?"
# Теги: <b> <i> <a> <a href=..> <c #..> <color=#..> <size=N> + самозакрывающиеся <quad ..> <sprite ..>;
# [xxxxxxxx_Имя] открывает цвет и закрывается </color> (так в Global).
# Значения цвета/размера встречаются как `#hex`, `##hex`, `"#hex"`, `N`, `"N"`: сравниваются дословно.
_TAG = re.compile(r"</?(?:b|i)>|<a(?: href=[^<>\s]+)?>|</a>|<c #" + _HEX + r">|</c>|<color=(?:\"#|##?)" + _HEX + r"\"?>|</color>"
                  r"|<size=\"?\d{1,3}\"?>|</size>|<quad [^<>]*>|<sprite[ =][^<>]*>|\[[0-9a-fA-F]{8}_[^\[\]]*\]", re.IGNORECASE)
_SELF_CLOSING = frozenset({"quad", "sprite"})
_NUMBER_UNIT = re.compile(
    r"(?<![\w])[-+]?\d+(?:[.,]\d+)?(?: +\d{3})*"
    r"(?: +(?:тыс\.|млн\.|млрд\.))?"
    r"(?: +(?:сек\.|секунд(?:а|ы)?|мин\.|минут(?:а|ы)?|мс|с|ч|ОЗ|маны|энергии|алмазов|%))?"
    r"(?![\w])", re.IGNORECASE,
)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_digest(value: Any) -> str:
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def text_check(value: Any) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_TEXT:
        raise ValueError("invalid-text")
    # Эти управляющие символы недопустимы в авторском каталоге. U+000B (мягкий перенос)
    # встречается в исходниках игры; его число сверяет target_diagnostics (soft-break-contract).
    if any((ord(c) < 32 and c not in "\n\r\x0b") or c in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069" for c in value):
        raise ValueError("unsupported-control")
    return value


def identity(row: dict) -> tuple[str, ...]:
    result = tuple(row.get(k) for k in IDENTITY_FIELDS)
    if any(not isinstance(x, str) or not x or len(x) > 512 for x in result):
        raise ValueError("invalid-context-identity")
    return result


def _without_tags(text: str) -> str:
    return _TAG.sub("", text)


def _argument_spans(text: str) -> list[tuple[int, int, str]]:
    """Конечный синтаксис: escaped braces отличаются от аргументов.

    Аргумент — любой `{...}` без вложенных скобок (сравнивается как есть), printf и
    коды `[N]`/`[NpNqN]`. Одиночная `{`/`}` вне аргумента требует native codec review.
    """
    spans: list[tuple[int, int, str]] = []
    position = 0
    while position < len(text):
        escaped = text[position:position+2]
        if escaped in ("{{", "}}"):
            spans.append((position, position+2, escaped))
            position += 2
            continue
        token = _PLACEHOLDER.match(text, position)
        if token:
            spans.append((position, token.end(), token.group()))
            position = token.end()
        elif text[position] in "{}":
            raise ValueError("unknown-placeholder-or-escaping")
        else:
            position += 1
    return spans


def _argument_tokens(text: str) -> list[str]:
    return [token for _, _, token in _argument_spans(text)]


def _without_arguments(text: str, replacement: str = "") -> str:
    parts: list[str] = []
    end = 0
    for start, stop, _ in _argument_spans(text):
        parts.append(text[end:start])
        parts.append(replacement)
        end = stop
    parts.append(text[end:])
    return "".join(parts)


def markup_tokens(text: str) -> tuple[str, ...]:
    """Теги по порядку; проверяет парность и вложенность.

    `<` вне известного тега: если после неё не латинская буква и не `/`
    (например `<=2`, `<1天`), это обычный текст; иначе тег неизвестен.
    """
    stack: list[str] = []
    tokens: list[str] = []
    position = 0
    while position < len(text):
        if text[position] not in "<[":
            position += 1
            continue
        tag = _TAG.match(text, position)
        if not tag:
            if text[position] == "<" and re.match(r"</?[A-Za-z]", text[position:position+2]):
                raise ValueError("unknown-markup")
            position += 1
            continue
        raw = tag.group()
        name = "color" if raw[0] == "[" else re.match(r"</?([A-Za-z]+)", raw).group(1).lower()
        if raw.startswith("</"):
            if not stack or stack.pop() != name:
                raise ValueError("unbalanced-markup")
        elif name not in _SELF_CLOSING:
            if len(stack) >= 8:
                raise ValueError("markup-depth")
            stack.append(name)
        tokens.append(raw)
        position = tag.end()
    if stack:
        raise ValueError("unbalanced-markup")
    return tuple(tokens)


def text_contract(text: str, formatter: dict) -> str:
    # Контракт formatter предоставляет адаптер native codec, а не переводчик.
    arguments = _argument_tokens(text)
    if any(token in ("{{", "}}") for token in arguments) and formatter.get("brace_escaping") != "double":
        raise ValueError("escaping-contract-required")
    payload = {"formatter": formatter, "placeholders": arguments,
               "markup": markup_tokens(text), "hard_breaks": re.findall(r"\r\n|\r|\n", text)}
    if "\x0b" in text:
        payload["soft_breaks"] = text.count("\x0b")
    return canonical_digest(payload)


def _positional_free(tokens: list[str]) -> list[str]:
    """printf-аргументы без номера (`%s`, `%d`) в порядке появления."""
    return [t for t in tokens if t[0] == "%" and t != "%%" and not re.match(r"%\d+\$", t)]


def _numbers(text: str) -> Counter:
    """Числа видимого текста без токенов; число в токене (`{0}`, цвет, размер) не считается."""
    visible = "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c
                      for c in _without_arguments(_without_tags(text)))
    visible = _THOUSANDS.sub(lambda m: re.sub(r"\D", "", m.group()), visible)
    return Counter(m.lstrip("0") or "0" for m in _NUMBER.findall(visible))


def _signed_numbers(text: str) -> list[tuple[str, str]]:
    """(число, знак) для каждой группы цифр видимого текста; знак '' если его нет.

    Знак - `+`, `-`, U+2212, `±` непосредственно перед числом и не после латинской/русской
    буквы, цифры или токена (`3-5`, `Fire-5`, `{0}-5` знаком не считаются). `−` приравнен к `-`.
    Число в токене не учитывается.
    """
    visible = "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c
                      for c in _without_arguments(_without_tags(text), _TOKEN_MARK))
    visible = _THOUSANDS.sub(lambda m: re.sub(r"\D", "", m.group()), visible)
    result: list[tuple[str, str]] = []
    for m in _NUMBER.finditer(visible):
        sign = ""
        before = visible[m.start()-1] if m.start() else ""
        if before and before in _SIGNS and not (m.start() >= 2 and _SIGN_BLOCK.match(visible[m.start()-2])):
            sign = "-" if before in "-\u2212" else before
        result.append((m.group().lstrip("0") or "0", sign))
    return result


def _sign_mismatches(source: str, target: str) -> list[str]:
    """Явный знак числа исходника (`+10`, `-5`, `±5`) обязан остаться у того же числа перевода.

    Проверяются только числа, которые по значению уцелели (потеря/добавление числа - отдельная ошибка).
    """
    src, tgt = _signed_numbers(source), _signed_numbers(target)
    kept = Counter(d for d, _ in src) & Counter(d for d, _ in tgt)
    want, have = Counter(p for p in src if p[1]), Counter(tgt)
    problems: list[str] = []
    for (digits, sign), count in (want - have).items():
        if digits not in kept:
            continue
        others = sorted(t for (d, t), n in (have - want).items() if d == digits for _ in range(n))
        found = others[0] if others else ""
        problems.extend([f"{sign}{digits} -> {found}{digits}"] * count)
    return sorted(problems)


def target_diagnostics(source: str, target: str, semantic_proofs: set[tuple[str, str]],
                       latin_allowlist: Iterable[str] = (), *, formatter: dict | None = None,
                       engine_latin: Iterable[str] = latin_allow.BUILTIN) -> tuple[list[str], dict]:
    """Причины отказа и подробности: какие токены/числа/слова пропали или лишние."""
    issues: list[str] = []
    details: dict[str, list[str]] = {}

    def fail(code: str, **info: list[str]) -> None:
        if code not in issues:
            issues.append(code)
        for key, value in info.items():
            if value:
                details.setdefault(key, []).extend(value)

    try:
        text_check(target)
    except ValueError as error:
        return [str(error)], details
    # Для оценки видимости исключаем format/control/marks/separators.
    # Сам target не нормализуем и не переписываем: его байты закреплены review.
    if not any(unicodedata.category(c)[0] not in "CMZ" and not c.isspace() for c in _without_tags(target)):
        fail("empty-visible-target")
    try:
        source_tokens, target_tokens = _argument_tokens(source), _argument_tokens(target)
    except ValueError as error:
        return issues + [str(error)], details
    if any(token in ("{{", "}}") for token in source_tokens+target_tokens) and (
        not isinstance(formatter, dict) or formatter.get("brace_escaping") != "double"
    ):
        fail("escaping-contract-required")
    # Токены сравниваются как мультимножество: русская грамматика меняет порядок.
    lost, extra = Counter(source_tokens) - Counter(target_tokens), Counter(target_tokens) - Counter(source_tokens)
    if lost or extra:
        fail("placeholder-contract", missing_tokens=sorted(lost.elements()), extra_tokens=sorted(extra.elements()))
    elif _positional_free(source_tokens) != _positional_free(target_tokens):
        # printf без номера привязан к порядку аргументов: %s и %d менять местами нельзя.
        fail("placeholder-contract", missing_tokens=_positional_free(source_tokens), extra_tokens=_positional_free(target_tokens))
    try:
        source_markup = markup_tokens(source)
    except ValueError as error:
        return issues + [str(error)], details
    try:
        target_markup = markup_tokens(target)
    except ValueError as error:
        fail(str(error))
        target_markup = None
    if target_markup is not None:
        lost, extra = Counter(source_markup) - Counter(target_markup), Counter(target_markup) - Counter(source_markup)
        if lost or extra:
            fail("markup-contract", missing_markup=sorted(lost.elements()), extra_markup=sorted(extra.elements()))
    if re.findall(r"\r\n|\r|\n", source) != re.findall(r"\r\n|\r|\n", target):
        fail("hard-break-contract")
    if source.count("\x0b") != target.count("\x0b"):
        fail("soft-break-contract")
    # Числа цветов, font-size и аргументов закреплены токенами и не сравниваются.
    # Мультимножество цифровых групп исходника и перевода совпадает (порядок свободен;
    # пропавшее и лишнее число названы в details); числа словами и склонения единиц
    # доказательства не требуют.
    lost, extra = _numbers(source) - _numbers(target), _numbers(target) - _numbers(source)
    if (lost or extra) and (digest(source), digest(target)) not in semantic_proofs:
        fail("numeric-semantic-proof-required", missing_numbers=sorted(lost.elements()),
             extra_numbers=sorted(extra.elements()))
    # Явный знак числа исходника (+10, -5, ±5) значим: +10 -> -10 - смысловая ошибка.
    # Числа с потерянным значением уже названы выше и здесь не повторяются.
    wrong_signs = _sign_mismatches(source, target)
    if wrong_signs and (digest(source), digest(target)) not in semantic_proofs:
        fail("numeric-sign-contract", sign_changes=wrong_signs)
    # − нет в шрифте игры: в переводе допустим только ASCII `-`.
    if "−" in _without_arguments(_without_tags(target)):
        fail("unicode-minus-in-target")
    # CJK внутри защищённых токенов (имя {推演战术:-5}, URL тега) - часть токена, а не текст перевода.
    if re.search(r"[\u3400-\u9fff]", _without_arguments(_without_tags(target))):
        fail("unreviewed-cjk-in-target")
    if not isinstance(latin_allowlist, (list, tuple, set, frozenset)) or len(latin_allowlist) > 128 or any(
        not isinstance(w, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:/-]{0,511}", w) for w in latin_allowlist
    ):
        raise ValueError("invalid-latin-allowlist")
    # Разрешено: явный список записи, список движка (бренд, approved-глоссарий, единицы)
    # и латиница, дословно стоящая в видимом тексте исходника.
    allowed = set(latin_allowlist) | set(engine_latin) | latin_allow.UNITS
    allowed |= latin_allow.words(_without_arguments(_without_tags(source)))
    unknown = sorted(w for w in latin_allow.words(_without_arguments(_without_tags(target))) if w not in allowed)
    if unknown:
        fail("unreviewed-latin-in-target", latin_words=unknown)
    return issues, details


def target_issues(source: str, target: str, semantic_proofs: set[tuple[str, str]],
                  latin_allowlist: Iterable[str] = (), *, formatter: dict | None = None,
                  engine_latin: Iterable[str] = latin_allow.BUILTIN) -> list[str]:
    return target_diagnostics(source, target, semantic_proofs, latin_allowlist, formatter=formatter,
                              engine_latin=engine_latin)[0]


def _records(rows: Iterable[dict]) -> list[dict]:
    result = list(rows)
    if len(result) > MAX_ROWS:
        raise ValueError("row-budget")
    for row in result:
        identity(row)
        text_check(row.get("source"))
        if not isinstance(row.get("formatter"), dict):
            raise ValueError("formatter-contract-required")
    return result


def reconcile(current: Iterable[dict], memory: Iterable[dict],
              semantic_proofs: set[tuple[str, str]] | None = None,
              engine_latin: Iterable[str] = latin_allow.BUILTIN) -> dict:
    """Неизменённое переводится; новое/изменённое сохраняет актуальный оригинал.

    Память хранится вызывающей стороной неизменно. Даже удалённые ключи остаются в ней
    для аудита, но не попадают в результат актуальной игры.
    """
    rows = _records(current)
    old = _records(memory)
    proofs = semantic_proofs or set()
    current_counts = Counter(identity(r) for r in rows)
    versions: dict[tuple, list[dict]] = defaultdict(list)
    for row in old:
        versions[identity(row)].append(row)
    decisions: list[dict] = []
    for row in rows:
        ident = identity(row)
        source = row["source"]
        sh = digest(source)
        try:
            contract = text_contract(source, row["formatter"])
        except ValueError as error:
            # Неизвестная разметка новой строки не блокирует другие строки.
            decisions.append({**{k: row[k] for k in IDENTITY_FIELDS}, "source_sha256": sh,
                              "contract_sha256": None, "text": source,
                              "status": "source-contract-review-required", "selected_variant": "source",
                              "issues": [str(error)]})
            continue
        candidates = []
        for m in versions[ident]:
            if digest(m["source"]) != sh or m.get("review") != "approved":
                continue
            try:
                if text_contract(m["source"], m["formatter"]) == contract:
                    candidates.append(m)
            except ValueError:
                continue
        status = "source-new" if not versions[ident] else "source-changed-or-unreviewed"
        target = source
        issues: list[str] = []
        details: dict = {}
        selected_variant = "source"
        if current_counts[ident] != 1:
            status, issues = "source-ambiguous", ["duplicate-current-identity"]
        elif candidates:
            variants = {m.get("target") for m in candidates if isinstance(m.get("target"), str)}
            if len(variants) != 1 or any(not isinstance(m.get("target"), str) for m in candidates):
                status, issues = "source-ambiguous", ["conflicting-memory"]
            else:
                proposed = variants.pop()
                issues, details = target_diagnostics(source, proposed, proofs, row["formatter"].get("latin_allowlist", ()),
                                                     formatter=row["formatter"], engine_latin=engine_latin)
                if issues:
                    status = "source-invalid-target"
                else:
                    target, status, selected_variant = proposed, "translated-exact", "approved"
        decisions.append({**{k: row[k] for k in IDENTITY_FIELDS}, "source_sha256": sh,
                          "contract_sha256": contract, "text": target, "status": status,
                          "selected_variant": selected_variant, "issues": issues,
                          **({"issue_details": details} if details else {})})
    ids = set(current_counts)
    deleted = sorted(set(versions) - ids)
    return {"schema": "wxq-localization-reconcile/v1", "records": decisions,
            "counts": dict(Counter(r["status"] for r in decisions)),
            "removed_identities": [dict(zip(IDENTITY_FIELDS, ident)) for ident in deleted],
            "memory_modified": False, "native_install_allowed": False,
            "native_codec_validation_required": True}


def break_opportunities(text: str, protected_ranges: Iterable[tuple[int, int]] = ()) -> list[int]:
    """Индексы ASCII-пробелов, где допустим перенос; UTF-8 байты не разрезаются.

    Число с единицей остаётся одним атомом без добавления отсутствующего NBSP-глифа.
    Утверждённые имена и формулы передаются как точные диапазоны готового текста.
    """
    text_check(text)
    markup_tokens(text)
    tags = list(_TAG.finditer(text))
    # Цветовая вставка не должна разделять число и единицу: ищем атомы по
    # видимому тексту и переносим диапазоны обратно в исходную разметку.
    visible: list[str] = []
    positions: list[int] = []
    end = 0
    for tag in tags:
        visible.extend(text[end:tag.start()])
        positions.extend(range(end, tag.start()))
        end = tag.end()
    visible.extend(text[end:])
    positions.extend(range(end, len(text)))
    protected = list(protected_ranges)
    protected += [(a, b) for a, b, _ in _argument_spans(text)]
    for match in _NUMBER_UNIT.finditer("".join(visible)):
        protected.append((positions[match.start()], positions[match.end()-1]+1))
    protected += [(m.start(), m.end()) for m in tags]
    if any(not (isinstance(a, int) and isinstance(b, int) and 0 <= a < b <= len(text)) for a, b in protected):
        raise ValueError("invalid-protected-range")
    return [i for i, c in enumerate(text) if c == " " and not any(a <= i < b for a, b in protected)]


@dataclass(frozen=True)
class LayoutSignature:
    build: str
    consumer: str
    font_asset_sha256: str
    material_sha256: str
    font_size: float
    font_style: str
    line_spacing: float
    rect_width: float
    rect_height: float
    dpi_scale: float
    canvas_scale: float
    state: str
    geometry_sha256: str

    def key(self) -> str:
        values = (self.font_size, self.line_spacing, self.rect_width, self.rect_height,
                  self.dpi_scale, self.canvas_scale)
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0 for x in values):
            raise ValueError("invalid-layout-metrics")
        if any(not isinstance(x, str) or not re.fullmatch(r"[a-f0-9]{64}", x)
               for x in (self.font_asset_sha256, self.material_sha256, self.geometry_sha256)):
            raise ValueError("layout-asset-fingerprint-required")
        if any(not isinstance(x, str) or not x or len(x) > 512
               for x in (self.build, self.consumer, self.font_style, self.state)):
            raise ValueError("incomplete-layout-signature")
        return canonical_digest(asdict(self))


def variant_review_key(context: dict, name: str, text: str) -> str:
    """Привязка внешней приёмки; SHA сам по себе не подтверждает её подлинность."""
    ident = identity(context)
    source = text_check(context.get("source"))
    text_check(text)
    if not isinstance(name, str) or not name or len(name) > 128:
        raise ValueError("variant-name-required")
    if not isinstance(context.get("formatter"), dict):
        raise ValueError("formatter-contract-required")
    return canonical_digest({"identity": dict(zip(IDENTITY_FIELDS, ident)),
                             "source_sha256": digest(source),
                             "contract_sha256": text_contract(source, context["formatter"]),
                             "variant_name": name, "text_sha256": digest(text)})


def select_fitted_variant(variants: list[dict], signature: LayoutSignature,
                          measurements: list[dict], original: str,
                          semantic_proofs: set[tuple[str, str]] | None = None,
                          *, context: dict) -> dict:
    """Принимает только измеренные и визуально проверенные утверждённые варианты.

    Протокол доверяет входным свидетельствам QA; не измеряет TimiText сам и не
    выдаёт synthetic fixture за реальное измерение. Поле source_sha256 обязано
    совпасть с актуальным исходником. review_key связывает identity, formatter,
    имя варианта и точный target. Смысл проверяется отдельно редактором/codec;
    вычисление ключа не заменяет настоящую редакторскую приёмку.
    """
    key = signature.key()
    text_check(original)
    fallback = {"text": original, "status": "source-fit-review-required", "layout_key": key}
    try:
        identity(context)
        variant_review_key(context, "context-check", original)
    except (ValueError, TypeError, AttributeError):
        return fallback
    if context["source"] != original or context["consumer"] != signature.consumer:
        return fallback
    for variant in variants:
        text = variant.get("text")
        if variant.get("review") != "approved" or variant.get("source_sha256") != digest(original):
            continue
        try:
            text_check(text)
            if variant.get("review_key") != variant_review_key(context, variant.get("name"), text):
                continue
        except ValueError:
            continue
        if target_issues(original, text, semantic_proofs or set(), context["formatter"].get("latin_allowlist", ()),
                         formatter=context["formatter"]):
            continue
        proofs = [p for p in measurements if p.get("layout_key") == key and p.get("text_sha256") == digest(text)]
        for proof in proofs:
            width, height = proof.get("width"), proof.get("height")
            finite = all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0 for x in (width, height))
            if finite and width <= signature.rect_width and height <= signature.rect_height and all(
                proof.get(k) is True for k in ("native_timitext", "glyphs_verified", "neighbors_clear", "hit_regions_clear", "font_unchanged", "material_unchanged")
            ):
                return {"text": text, "status": "fitted-approved", "layout_key": key,
                        "variant": variant["name"], "review_key": variant["review_key"]}
    return fallback


class LayoutCache:
    """Содержимое и вся вёрстка входят в ключ; любое событие инвалидирует узел.

    Runtime-адаптер обязан вызвать invalidate при resize/DPI/font/material/data/
    canvas/locale/unload и изменении соседей/масок. geometry_sha256 включает
    положение, anchors, padding, соседние rect, mask и hit regions, не только
    ширину/высоту. Наличие класса не доказывает подключение этих событий.
    """
    def __init__(self, limit: int = 128):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 4096:
            raise ValueError("invalid-cache-budget")
        self.limit = limit
        self._values: dict[str, tuple[str, str, str]] = {}

    def put(self, node: str, text: str, signature: LayoutSignature, output: str) -> bool:
        if node not in self._values and len(self._values) >= self.limit:
            return False
        self._values[node] = (digest(text_check(text)), signature.key(), text_check(output))
        return True

    def get(self, node: str, text: str, signature: LayoutSignature) -> str | None:
        item = self._values.get(node)
        if item and item[:2] == (digest(text_check(text)), signature.key()):
            return item[2]
        return None

    def invalidate(self, node: str) -> None:
        self._values.pop(node, None)
