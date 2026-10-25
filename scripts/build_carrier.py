"""Массовый носитель русификации: все строки carrier -> override пакета 1 (QtsVFS), офлайн.

Обобщение build_pilot_carrier.py: вход не pilot-content.json, а carrier движка (`localization.delta.run`,
сохранён скриптом `build/coverage-2026-10-13/after-sign/save_carrier.py`). Игра читается только на чтение,
единственный запуск — изолированный SDK-воркер проекта во временной лаборатории. Результат — каталог
`build/carrier-<дата>-<id>/` с манифестом контракта wxq-pilot-carrier/v1 (как у пилота).

Меняются только строки carrier; всё остальное в ресурсе байт-в-байт как в оригинале (validate_rewrite у LangDocument,
prove_text_only у GlobalMapDocument). Строка с ошибкой проверки не вставляется (остаётся оригинал) и попадает в REPORT.md
и excluded.json. Установку делает game_layer_deploy.py; здесь ничего не ставится.

Один кандидат на пакет (`--package`, по умолчанию 1): все виды правок пакета собираются сразу, иначе второй кандидат
того же пакета затёр бы первый при установке.
  пакет 1  - строки .lang и Global из --carrier + текстовые правки protobuf-таблиц (kind=pb-table-text) из --text-edits
             (+ --raw-resource для замен целиком); выход build/carrier-<дата>-<id>;
  пакет 19 - текстуры из готового кандидата (--import-textures build/pilot-...: rgba32-texture и bc7-texture, байты
             берутся из его payloads/ после сверки SHA) + правки Unity-текста (kind=unity-text) из --text-edits;
             без carrier выход build/pilot-<дата>-<id>;
  пакет 4  - как раньше: атласы bc7 через --import-textures.

Файл правок --text-edits (JSON, кодировка UTF-8):
  {"schema": "wxq-text-edits/v1",
   "edits": [
     {"pkg": "1", "table_id": "<16 hex>", "path": "2/13", "cn": "<китайский текст>", "ru": "<перевод>"},
        все листья колонки `path` таблицы, чей текст равен cn (путь без индексов, как в каталоге колонок);
     {"pkg": "1", "table_id": "<16 hex>", "addr": "2[3]/13[0]", "cn": "<китайский текст>", "ru": "<перевод>"},
        один лист по адресу pb_table_codec;
     {"pkg": "19", "resource": "<16 hex>", "path_id": -4538919611162611716, "field": "m_Text", "cn": "<китайский текст>", "ru": "<перевод>"}],
   "pending": [{"table_id": "...", "path": "...", "cn": "...", "proposal": "...", "note": "..."}]}
  `pending` в кандидат не попадает: только список в REPORT.md (строки без утверждённого перевода).
Правка берётся, только если колонка таблицы (table_id + path) или группа Unity-текста (cn) имеют в каталоге
`--catalog` (localization/profiles/display-columns-*.json) status=include; сам файл правок - явный белый список канарейки.
Источник (cn) обязан совпасть с текстом оригинала в prime, оригинал - с пином localization/profiles/text-resource-pins-*.json.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import json
import random
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
for _path in (str(PROJECT), str(PROJECT / 'scripts')):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import build_pilot_carrier as bp  # noqa: E402
from localization import lang_waivers  # noqa: E402
import pilot_contract  # noqa: E402
import wxq_install  # noqa: E402
from game_inspect import QtsContainer  # noqa: E402
from localization import core, fit_rules, latin_allow, markup_exceptions, pb_table_codec, protected_cjk, ru_cli, token_spacing, unity_fit, unity_text_codec  # noqa: E402
from localization.global_map_codec import GlobalMapDocument, prove_text_only  # noqa: E402
from scripts.game_lang_codec import LangDocument, validate_rewrite, validate_text  # noqa: E402

PACKAGE = '1'  # пакет по умолчанию; фактический берётся из --package
TEXTURE_KINDS = ('rgba32-texture', 'bc7-texture')
DEFAULT_CATALOG = PROJECT / 'localization/profiles/display-columns-1.5.0.1.json'
GLOBAL_ID = 'dd5b5edfbb69feed'
NAMESPACES = {'pb-lang': 'lang', 'global': 'global'}
KIND = {'lang': 'lang', 'global': 'global-localize'}
RESOURCE = re.compile(r'[0-9a-f]{16}')
SHA256 = re.compile(r'[0-9a-f]{64}')
CARRIER_KEYS = {'namespace', 'resource', 'key', 'consumer', 'variant', 'source_sha256', 'text'}
# Невидимые символы, которые не должны появляться в переводе, если их нет в оригинале.
INVISIBLE = frozenset('­​‌‍⁠﻿�')
# Слог на отдельной строке между буквами («Ин\nвес\nтор») — слово порвано жёстким переносом; «слово\nслово» — норма.
_CJK_ONE = re.compile(r"[\u3400-\u9fff]")


def is_vertical_label(source: str, target: str) -> bool:
    """Исключение vertical-label: исходник — столбец одиночных иероглифов (2+ строк через LF),
    перевод — столько же строк, в каждой ровно одна буква. Последней строкой допускается точка
    (сокращение «Ред.»: Р/е/д/.) либо буква с точкой."""
    src, dst = source.split("\n"), target.split("\n")
    if len(src) < 2 or len(src) != len(dst):
        return False
    if not all(_CJK_ONE.fullmatch(part) for part in src):
        return False
    if not all(re.fullmatch(r"[A-Za-zА-Яа-яЁё]", part) for part in dst[:-1]):
        return False
    return re.fullmatch(r"[A-Za-zА-Яа-яЁё]\.?|\.", dst[-1]) is not None


_TORN_WORD = re.compile(r'[A-Za-zА-Яа-яЁё]\n[A-Za-zА-Яа-яЁё]{1,3}\n[A-Za-zА-Яа-яЁё]')
_HYPHEN_BREAK = re.compile(r'[A-Za-zА-Яа-яЁё]-\n')
DEFAULT_CARRIER = PROJECT / 'build/coverage-2026-10-13/after-sign/carrier.json'


class CarrierError(bp.PilotError):
    """Вход или окружение не годятся."""


# ---------------------------------------------------------------- вход

def load_carrier(path: Path) -> tuple[list[dict], dict]:
    try:
        value = json.loads(path.read_text('utf-8-sig'))
    except (OSError, ValueError) as error:
        raise CarrierError(f'carrier не читается: {path}: {error}') from error
    rows = value.get('carrier') if isinstance(value, dict) else value
    if not isinstance(rows, list) or not rows:
        raise CarrierError('carrier: ожидается непустой список записей (или {"carrier": [...]})')
    problems, seen = [], set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or CARRIER_KEYS - set(row):
            problems.append(f'запись {index}: нет полей {sorted(CARRIER_KEYS - set(row)) if isinstance(row, dict) else "объект"}')
            continue
        ident = (row['namespace'], row['resource'], row['key'])
        if row['namespace'] not in NAMESPACES:
            problems.append(f'{ident}: namespace не из {sorted(NAMESPACES)}')
        elif not isinstance(row['resource'], str) or not RESOURCE.fullmatch(row['resource']):
            problems.append(f'{ident}: resource должен быть 16 hex')
        elif row['namespace'] == 'global' and row['resource'] != GLOBAL_ID:
            problems.append(f'{ident}: Global только в ресурсе {GLOBAL_ID}')
        elif not isinstance(row['key'], str) or not row['key'] or not isinstance(row['text'], str) or not row['text'].strip():
            problems.append(f'{ident}: пустой key или text')
        elif row['consumer'] != '*' or row['variant'] != '*':
            problems.append(f'{ident}: consumer/variant не "*" (доставка по consumer не реализована)')
        elif not isinstance(row['source_sha256'], str) or not SHA256.fullmatch(row['source_sha256']):
            problems.append(f'{ident}: source_sha256')
        elif ident in seen:
            problems.append(f'{ident}: повтор')
        seen.add(ident)
        if len(problems) >= 20:
            break
    if problems:
        raise CarrierError('carrier:\n  ' + '\n  '.join(problems))
    meta = {k: v for k, v in value.items() if k != 'carrier'} if isinstance(value, dict) else {}
    return rows, meta


def load_slots(path: Path | None) -> dict:
    """Необязательные места вывода: {"<namespace>/<resource>/<key>": {slot}}. Шрифт и размер в слоте запрещены."""
    if path is None:
        return {}
    value = json.loads(path.read_text('utf-8-sig'))
    if not isinstance(value, dict):
        raise CarrierError('slots: ожидается объект')
    return value


def load_glyphs() -> frozenset | None:
    """Кодовые точки, которые есть в обоих основных шрифтах игры; None, если шрифты проекта недоступны."""
    try:
        import game_font_coverage
        fonts, _ = game_font_coverage.load_main_fonts()
    except (OSError, ValueError, KeyError, ImportError):
        return None
    sets = [set(f['codepoints']) for f in fonts]
    return frozenset(set.intersection(*sets)) if sets else None


# ---------------------------------------------------------------- проверка одной строки

def loose_issues(source: str, ru_game: str) -> list[str]:
    """Проверка без известного места: слова не рвутся, нет невидимых символов, которых не было в оригинале."""
    issues = []
    new_invisible = sorted({c for c in ru_game if c in INVISIBLE and c not in source})
    if new_invisible:
        issues.append('invisible-char:' + ','.join(f'U+{ord(c):04X}' for c in new_invisible))
    if is_vertical_label(source, ru_game):
        return issues  # исключение vertical-label: слово намеренно разложено по строкам
    if len(_TORN_WORD.findall(ru_game)) > len(_TORN_WORD.findall(source)):
        issues.append('word-torn-by-newline')
    if len(_HYPHEN_BREAK.findall(ru_game)) > len(_HYPHEN_BREAK.findall(source)):
        issues.append('hyphen-at-line-end')
    return issues


def glyph_issues(source: str, ru_game: str, glyphs: frozenset | None) -> list[str]:
    """Символы перевода, которых не было в оригинале и нет в шрифтах игры."""
    if glyphs is None:
        return []
    known = set(fit_rules.visible_text(source))
    missing = sorted({c for c in fit_rules.visible_text(ru_game) if c not in '\n\r' and c not in known and ord(c) not in glyphs})
    return ['glyph-missing:' + ','.join(f'U+{ord(c):04X}' for c in missing[:8])] if missing else []


_EDGE_IDENTS: set | None = None


def edge_space_idents() -> set:
    """Записи, где пробел по краю нужен игре («Серебро » + номер ступени): правила вёрстки с reason rank-name-gap / edge-space."""
    global _EDGE_IDENTS
    if _EDGE_IDENTS is None:
        path = PROJECT / 'glossary/layout_fixes.json'
        rows = json.loads(path.read_text('utf-8'))['fixes'] if path.is_file() else []
        _EDGE_IDENTS = {(r['namespace'], r['resource'], r['key']) for r in rows if r.get('reason') in ('rank-name-gap', 'edge-space')}
    return _EDGE_IDENTS


def row_issues(namespace: str, source: str, ru_text: str, slot: dict | None, glyphs: frozenset | None,
               latin: frozenset, keep_edges: bool = False) -> tuple[str, list[str]]:
    """(текст для игры, замечания). Пустой список — строку можно вставлять. keep_edges сохраняет пробелы ASCII по краям текста."""
    body = ru_text.strip()
    lead = ru_text[:len(ru_text) - len(ru_text.lstrip(' '))] if keep_edges else ''
    tail = ru_text[len(ru_text.rstrip(' ')):] if keep_edges else ''
    ru_game, _ranges = fit_rules.emit_for_game(body)
    # Переводы строк по краям исходника (`\n` в начале/конце) - часть вёрстки игры: strip() их не должен съедать.
    lead_lf = min(len(source) - len(source.lstrip('\n')), len(ru_text) - len(ru_text.lstrip('\n')))
    tail_lf = min(len(source) - len(source.rstrip('\n')), len(ru_text) - len(ru_text.rstrip('\n')))
    # то же для \r и \r\n: если край перевода записан теми же переводами строк, что и край исходника, сохраняем их как есть
    source_lead, source_tail = source[:len(source) - len(source.lstrip('\r\n'))], source[len(source.rstrip('\r\n')):]
    ru_lead, ru_tail = ru_text[:len(ru_text) - len(ru_text.lstrip('\r\n'))], ru_text[len(ru_text.rstrip('\r\n')):]
    lead_br = source_lead if source_lead == ru_lead and '\r' in source_lead else '\n' * lead_lf
    tail_br = source_tail if source_tail == ru_tail and '\r' in source_tail else '\n' * tail_lf
    ru_game = lead + lead_br + ru_game + tail_br + tail
    proofs = semantic_proofs()
    proven = is_proven(source, ru_text, ru_game)
    issues = list(core.target_issues(source, ru_game, proofs if proven else set(), (), formatter={}, engine_latin=latin))
    issues = protected_cjk.filter_issues(source, ru_game, issues)  # алиасы эмодзи и коды-пароли остаются китайскими
    issues += token_spacing.issues(ru_game)  # «1Энергия», «Опыт+2»: между числом/токеном и токеном-словом нужен пробел
    # битая разметка самого исходника (4 текста): закрытый список пар, разметка байт в байт (localization/markup_exceptions.py)
    issues = markup_exceptions.filter_issues(source, ru_text, issues, emitted=ru_game, semantic_proofs=proofs if proven else set(), engine_latin=latin)
    fit = fit_rules.fit_report(source, ru_game, slot or {})
    if markup_exceptions.WAIVED.intersection(fit) and markup_exceptions.accepts(source, ru_text, ru_game):
        # fit_rules тоже встаёт на битой разметке исходника: вёрстку проверяем на тексте без технических фрагментов
        fit = [item for item in fit if item not in markup_exceptions.WAIVED] + fit_rules.fit_report(*markup_exceptions.cleaned(source, ru_game), slot or {})
    issues += fit
    issues += loose_issues(source, ru_game)
    issues += glyph_issues(source, ru_game, glyphs)
    if namespace == 'lang':
        # Пара из проверенного файла доказательств смысла (пересчёт 万/亿, дата) не отклоняется за буквальное число.
        issues += ['codec:' + text for text in validate_text(source, ru_game) if not (proven and text == 'literal number/percentage mismatch')]
    return ru_game, list(dict.fromkeys(issues))


def is_proven(source: str, ru_text: str, ru_game: str) -> bool:
    """Пара (исходник, перевод) есть в glossary/semantic_proofs.json - по тексту до или после подготовки для игры."""
    proofs = semantic_proofs()
    return (core.digest(source), core.digest(ru_text.strip())) in proofs or (core.digest(source), core.digest(ru_game)) in proofs


_PROOFS: set | None = None


def semantic_proofs() -> set:
    """Пары (SHA исходника, SHA перевода) из glossary/semantic_proofs.json (проверено вручную и независимым ревью)."""
    global _PROOFS
    if _PROOFS is None:
        path = PROJECT / 'glossary/semantic_proofs.json'
        rows = json.loads(path.read_text('utf-8'))['proofs'] if path.is_file() else []
        _PROOFS = {(core.digest(row['cn']), core.digest(row['ru'])) for row in rows}
    return _PROOFS


# ---------------------------------------------------------------- игра (только чтение)

def read_originals(game: Path, idents: set[int], package: str = PACKAGE) -> dict[int, tuple[bytes, str]]:
    """Ресурсы пакета из prime-файлов: каждый файл данных открывается один раз."""
    found: dict[int, tuple[bytes, str]] = {}
    for path in bp.data_files(game, package):
        want = idents - set(found)
        if not want:
            break
        with QtsContainer(path) as container:
            for ident in sorted(want & set(container.entries)):
                found[ident] = (container.decode_entry(ident), path.name)
    return found


def apply_rows(namespace: str, original: bytes, document, translations: dict[str, str],
               number_waivers: set[tuple[str, str]] | None = None) -> bytes:
    """Новая нагрузка ресурса с доказательством «изменены только выбранные строки».

    number_waivers - пары (SHA исходника, SHA перевода) из semantic_proofs: кодек не отклоняет их за буквальное число."""
    if namespace == 'global':
        payload = document.replace_text(translations)
        prove_text_only(original, payload, translations)
    else:
        payload = lang_waivers.replace(document, translations, number_waivers=number_waivers)
        lang_waivers.validate_rewrite(document, LangDocument.parse(payload), translations, number_waivers=number_waivers)
    return payload


def prepare(game: Path, entries: list[dict], *, slots: dict | None = None, glyphs: frozenset | None = None,
            latin: frozenset = latin_allow.BUILTIN, progress=None) -> tuple[list[dict], list[dict], dict]:
    """Новые нагрузки ресурсов, список исключённых строк и статистика. Ничего не пишет."""
    slots = slots or {}
    groups: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for row in entries:
        groups[(NAMESPACES[row['namespace']], row['resource'])].append(row)
    originals = read_originals(game, {int(resource, 16) for _, resource in groups})
    resources, excluded = [], []
    stats = collections.Counter()
    for number, ((namespace, resource), group) in enumerate(sorted(groups.items()), 1):
        if progress and number % 50 == 0:
            progress(f'{number}/{len(groups)} ресурсов')
        bad = lambda row, *reasons: excluded.append({'namespace': row['namespace'], 'resource': resource, 'key': row['key'],
                                                    'reasons': list(reasons), 'ru': row['text']})
        if int(resource, 16) not in originals:
            for row in group:
                bad(row, 'resource-not-in-package-1')
            stats['excluded_resource_missing'] += len(group)
            continue
        original, data_file = originals[int(resource, 16)]
        document = GlobalMapDocument.parse(original) if namespace == 'global' else LangDocument.parse(original)
        actual = {r.key: r.text for r in document.rows}
        translations: dict[str, str] = {}
        waivers: set[tuple[str, str]] = set()
        for row in group:
            source = actual.get(row['key'])
            if source is None:
                bad(row, 'key-not-in-resource')
            elif core.digest(source) != row['source_sha256']:
                bad(row, 'source-differs-from-game')
            else:
                slot = slots.get(f'{row["namespace"]}/{resource}/{row["key"]}')
                ru_game, issues = row_issues(namespace, source, row['text'], slot, glyphs, latin, (row['namespace'], row['resource'], row['key']) in edge_space_idents())
                if issues:
                    bad(row, *issues)
                elif ru_game == source:
                    stats['equal_to_original'] += 1
                else:
                    translations[row['key']] = ru_game
                    if namespace == 'lang' and is_proven(source, row['text'], ru_game):
                        waivers.add((core.digest(source), core.digest(ru_game)))
        if not translations:
            continue
        try:
            payload = apply_rows(namespace, original, document, translations, waivers)
        except ValueError as error:
            # Предпроверка строк кодека должна была это исключить; ищем виновников по одной, остальные оставляем.
            for key in list(translations):
                try:
                    apply_rows(namespace, original, document, {key: translations[key]}, waivers)
                except ValueError as single:
                    del translations[key]
                    excluded.append({'namespace': 'global' if namespace == 'global' else 'pb-lang', 'resource': resource, 'key': key,
                                     'reasons': [f'codec-rejected: {single}'], 'ru': ''})
            if not translations:
                continue
            payload = apply_rows(namespace, original, document, translations, waivers)
        stats['rows_changed'] += len(translations)
        resources.append({'package': PACKAGE, 'id_hex': resource, 'kind': KIND[namespace], 'original': original,
                          'original_sha256': bp.sha_bytes(original), 'items': [], 'data_file': data_file,
                          'alias': f'/Pilot/{resource}.bin', 'payload': payload, 'sha256': bp.sha_bytes(payload),
                          'rows_changed': len(translations), 'rows_total': len(actual)})
    stats['resources'] = len(resources)
    stats['excluded'] = len(excluded)
    return resources, excluded, dict(stats)


# ---------------------------------------------------------------- текстовые правки таблиц и Unity-файлов

def load_text_edits(path: Path | None) -> tuple[list[dict], list[dict]]:
    """(правки, pending). Формат описан в шапке скрипта; всё лишнее и неполное отклоняется."""
    if path is None:
        return [], []
    try:
        value = json.loads(path.read_text('utf-8-sig'))
    except (OSError, ValueError) as error:
        raise CarrierError(f'text-edits не читается: {path}: {error}') from error
    if not isinstance(value, dict) or not isinstance(value.get('edits'), list):
        raise CarrierError('text-edits: ожидается объект с полем edits')
    pending = value.get('pending', [])
    if not isinstance(pending, list) or any(not isinstance(item, dict) for item in pending):
        raise CarrierError('text-edits: pending должен быть списком объектов')
    problems, seen = [], set()
    for index, row in enumerate(value['edits']):
        if not isinstance(row, dict):
            problems.append(f'правка {index}: ожидается объект')
            continue
        where = f'правка {index}'
        pb, unity = 'table_id' in row, 'path_id' in row
        allowed = {'pkg', 'cn', 'ru', 'table_id', 'path', 'addr'} if pb else {'pkg', 'cn', 'ru', 'resource', 'path_id', 'field'}
        if pb == unity or set(row) - allowed:
            problems.append(f'{where}: ровно один из table_id (таблица) или path_id (Unity), других полей нет')
        elif not all(isinstance(row.get(k), str) and row[k] for k in ('pkg', 'cn', 'ru')):
            problems.append(f'{where}: pkg, cn, ru - непустые строки')
        elif pb and (not RESOURCE.fullmatch(str(row['table_id'])) or (('path' in row) == ('addr' in row))):
            problems.append(f'{where}: table_id 16 hex и ровно одно из path/addr')
        elif unity and (not RESOURCE.fullmatch(str(row.get('resource'))) or type(row['path_id']) is not int or not isinstance(row.get('field'), str)):
            problems.append(f'{where}: resource 16 hex, path_id целое, field строка')
        else:
            ident = ('pb', row['table_id'], row.get('path') or pb_table_codec.normalize_address(row['addr']), row['cn']) if pb else ('unity', row['resource'], row['path_id'], row['field'])
            if ident in seen:
                problems.append(f'{where}: повтор {ident}')
            seen.add(ident)
        if len(problems) >= 20:
            break
    if problems:
        raise CarrierError('text-edits:\n  ' + '\n  '.join(problems))
    return value['edits'], pending


def load_catalog(path: Path) -> dict:
    try:
        value = json.loads(path.read_text('utf-8-sig'))
        return {'columns': {(c['table_id'], c['path']): c['status'] for c in value['columns']},
                'unity': {g['text']: g['status'] for g in value['unity_groups']}}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise CarrierError(f'каталог колонок не читается: {path}: {error}') from error


def column_path(address: str) -> str:
    """`2[3]/13[0]` -> `2/13`."""
    return '/'.join(segment.split('[')[0] for segment in address.split('/'))


def _text_resource(package: str, ident: str, kind: str, original: bytes, data_file: str, payload: bytes, declaration: dict) -> dict:
    return {'package': package, 'id_hex': ident, 'kind': kind, 'original': original, 'original_sha256': bp.sha_bytes(original),
            'items': [], 'data_file': data_file, 'alias': f'/Pilot/{ident}.bin', 'payload': payload, 'sha256': bp.sha_bytes(payload),
            'rows_changed': len(declaration['edits']), 'rows_total': 0, 'declaration': declaration}


def prepare_text(game: Path, package: str, edits: list[dict], catalog: dict, *, glyphs: frozenset | None = None,
                 latin: frozenset = latin_allow.BUILTIN, fit_styles: bool = False) -> tuple[list[dict], list[dict], dict]:
    """Ресурсы kind=pb-table-text (пакет 1) и kind=unity-text (пакет 19) из файла правок; исключённые строки и статистика."""
    mine = [row for row in edits if row['pkg'] == package]
    resources, excluded, stats = [], [], collections.Counter()
    pins = pilot_contract.text_resource_pins().get(package, {})
    expected = {pkg: ('table_id' if kind == 'pb-table-text' else 'path_id') for kind, (pkg, _) in pilot_contract.TEXT_KINDS.items()}
    if mine and (package not in expected or any(expected[package] not in row for row in mine)):
        raise CarrierError(f'text-edits: правки не подходят пакету {package}')
    bad = lambda row, ident, key, *reasons: excluded.append({'namespace': 'pb-table' if 'table_id' in row else 'unity-text', 'resource': ident,
                                                             'key': key, 'reasons': list(reasons), 'ru': row['ru']})
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for row in mine:
        groups[row['table_id'] if 'table_id' in row else row['resource']].append(row)
    originals = read_originals(game, {int(ident, 16) for ident in groups}, package) if groups else {}
    for ident, group in sorted(groups.items()):
        if ident not in pins or int(ident, 16) not in originals:
            for row in group:
                bad(row, ident, row.get('path') or row.get('addr') or f'{row["path_id"]}:{row["field"]}', 'resource-not-pinned-or-missing')
            continue
        original, data_file = originals[int(ident, 16)]
        if bp.sha_bytes(original) != pins[ident]:
            raise CarrierError(f'{package}/{ident}: оригинал в игре отличается от пина (сборка другая?)')
        if package == '1':
            leaves = dict(pb_table_codec.list_text_leaves(original))
            chosen: dict[str, str] = {}
            for row in group:
                if 'addr' in row:
                    address = pb_table_codec.normalize_address(row['addr'])
                    targets = [address] if leaves.get(address) == row['cn'] else []
                else:
                    targets = [a for a, t in leaves.items() if column_path(a) == row['path'] and t == row['cn']]
                label = row.get('path') or row['addr']
                if not targets:
                    bad(row, ident, label, 'source-text-not-in-table')
                    continue
                if catalog['columns'].get((ident, column_path(targets[0]))) != 'include':
                    bad(row, ident, label, 'column-not-include-in-catalog')
                    continue
                ru_game, issues = row_issues('pb-table', row['cn'], row['ru'], None, glyphs, latin)
                if issues:
                    bad(row, ident, label, *issues)
                    continue
                for address in targets:
                    if chosen.get(address, ru_game) != ru_game:
                        raise CarrierError(f'{ident}/{address}: две разные правки одного листа')
                    chosen[address] = ru_game
            chosen = {a: t for a, t in chosen.items() if t != leaves[a]}
            if not chosen:
                continue
            payload = pb_table_codec.replace(original, chosen)
            pb_table_codec.prove_text_only(original, payload, chosen)
            declaration = {'package': package, 'id_hex': ident, 'original_sha256': bp.sha_bytes(original), 'sha256': bp.sha_bytes(payload),
                           'edits': [{'address': a, 'source_sha256': core.digest(leaves[a]), 'sha256': core.digest(t)} for a, t in sorted(chosen.items())],
                           'translations': [{'address': a, 'cn': leaves[a], 'ru': t} for a, t in sorted(chosen.items())]}
            resources.append(_text_resource(package, ident, 'pb-table-text', original, data_file, payload, declaration))
        else:
            fields = {(pid, path): text for pid, path, text in unity_text_codec.list_text_fields(original)}
            chosen_u: dict[tuple[int, str], str] = {}
            for row in group:
                key = (row['path_id'], row['field'])
                label = f'{row["path_id"]}:{row["field"]}'
                if fields.get(key) != row['cn']:
                    bad(row, ident, label, 'source-text-not-in-resource')
                    continue
                if catalog['unity'].get(row['cn']) != 'include':
                    bad(row, ident, label, 'unity-group-not-include-in-catalog')
                    continue
                ru_game, issues = row_issues('unity-text', row['cn'], row['ru'], None, glyphs, latin)
                if issues:
                    bad(row, ident, label, *issues)
                    continue
                if chosen_u.get(key, ru_game) != ru_game:
                    raise CarrierError(f'{ident}/{label}: две разные правки одного поля')
                chosen_u[key] = ru_game
            chosen_u = {k: t for k, t in chosen_u.items() if t != fields[k]}
            if not chosen_u:
                continue
            styles = unity_fit.plan_styles(original, chosen_u, fields) if fit_styles else {}
            payload = unity_text_codec.replace(original, {**chosen_u, **styles})
            unity_text_codec.prove_text_only(original, payload, {**chosen_u, **styles})
            declaration = {'package': package, 'id_hex': ident, 'original_sha256': bp.sha_bytes(original), 'sha256': bp.sha_bytes(payload),
                           'edits': [{'path_id': pid, 'field': path, 'source_sha256': core.digest(fields[(pid, path)]), 'sha256': core.digest(t)}
                                     for (pid, path), t in sorted(chosen_u.items())],
                           'translations': [{'path_id': pid, 'field': path, 'cn': fields[(pid, path)], 'ru': t} for (pid, path), t in sorted(chosen_u.items())]}
            if styles:
                before = unity_text_codec.read_field_values(original, styles)
                declaration['styles'] = [{'path_id': pid, 'field': path, 'source': before[(pid, path)], 'value': value}
                                         for (pid, path), value in sorted(styles.items())]
                stats['fit_labels'] += sum(1 for _, path in styles if path.endswith('m_BestFit'))
            resources.append(_text_resource(package, ident, 'unity-text', original, data_file, payload, declaration))
        stats['text_edits'] += resources[-1]['rows_changed']
    stats['text_resources'] = len(resources)
    return resources, excluded, dict(stats)


def import_textures(game: Path, package: str, candidate: Path, taken: set[str]) -> list[dict]:
    """Текстуры (rgba32-texture, bc7-texture) пакета из готового кандидата. Байты читаются из его payloads/ и сверяются с SHA в манифесте,
    оригинал берётся из prime и сверяется с original_sha256 манифеста; для bc7 дополнительно белый список и длина потока."""
    try:
        manifest = json.loads((candidate / 'manifest.json').read_text('utf-8-sig'))
    except (OSError, ValueError) as error:
        raise CarrierError(f'--import-textures: манифест не читается: {candidate}: {error}') from error
    if manifest.get('resource_contract') != pilot_contract.CONTRACT:
        raise CarrierError('--import-textures: не кандидат контракта пилота')
    rows = [r for r in manifest.get('resources', []) if r.get('package') == package and r.get('kind') in TEXTURE_KINDS]
    if not rows:
        raise CarrierError(f'--import-textures: в {candidate.name} нет текстур пакета {package}')
    clash = sorted(r['id_hex'] for r in rows if r['id_hex'] in taken)
    if clash:
        raise CarrierError(f'--import-textures: ресурс уже меняется другим видом правок: {", ".join(clash)}')
    originals = read_originals(game, {int(r['id_hex'], 16) for r in rows}, package)
    resources = []
    for row in rows:
        ident = row['id_hex']
        path = candidate / 'payloads' / f'{package}-{ident}.bin'
        if not path.is_file():
            raise CarrierError(f'--import-textures: нет файла {path.name}')
        payload = path.read_bytes()
        if bp.sha_bytes(payload) != row['sha256']:
            raise CarrierError(f'--import-textures: SHA файла {path.name} не равен манифесту')
        if int(ident, 16) not in originals:
            raise CarrierError(f'--import-textures: ресурса {ident} нет в prime пакета {package}')
        original, data_file = originals[int(ident, 16)]
        if bp.sha_bytes(original) != row['original_sha256']:
            raise CarrierError(f'--import-textures: оригинал {ident} в игре отличается от манифеста кандидата')
        if row['kind'] == 'bc7-texture':
            if pilot_contract.BC7_TEXTURE_ALLOWED.get(package, {}).get(ident) != row['original_sha256']:
                raise CarrierError(f'--import-textures: bc7 {ident} не в белом списке')
            try:
                pilot_contract.require_bc7_stream_size(row, payload)
            except ValueError as error:
                raise CarrierError(f'--import-textures: {error}') from error
        resources.append({'package': package, 'id_hex': ident, 'kind': row['kind'], 'original': original, 'original_sha256': row['original_sha256'],
                          'items': row.get('items', []), 'data_file': data_file, 'alias': row['alias'], 'payload': payload,
                          'sha256': row['sha256'], 'rows_changed': 0, 'rows_total': 0})
    return resources


def parse_raw_specs(specs: list[str], package: str = PACKAGE) -> list[tuple[str, Path]]:
    """`<16 hex>=<путь>` -> (id, путь). Только id из белого списка pilot_contract.RAW_REPLACEMENT_ALLOWED[package]."""
    result, seen = [], set()
    allowed = pilot_contract.RAW_REPLACEMENT_ALLOWED.get(package, {})
    for spec in specs:
        ident, sep, raw_path = spec.partition('=')
        ident = ident.strip().lower()
        if not sep or not RESOURCE.fullmatch(ident) or not raw_path.strip():
            raise CarrierError(f'--raw-resource: ожидается <16 hex>=<путь>, получено {spec!r}')
        if ident not in allowed:
            raise CarrierError(f'--raw-resource: ресурс {ident} не в белом списке замен целиком (пакет {package})')
        if ident in seen:
            raise CarrierError(f'--raw-resource: повтор {ident}')
        seen.add(ident)
        path = Path(raw_path.strip())
        if not path.is_file():
            raise CarrierError(f'--raw-resource: файл не найден: {path}')
        result.append((ident, path))
    return result


def raw_resources(game: Path, specs: list[tuple[str, Path]], taken: set[str], package: str = PACKAGE) -> list[dict]:
    """Ресурсы, заменяемые целиком готовыми байтами (kind=raw-replacement). Оригинал читается из prime, SHA сверяется с закреплённым."""
    if not specs:
        return []
    clash = sorted(ident for ident, _ in specs if ident in taken)
    if clash:
        raise CarrierError(f'--raw-resource: ресурс уже меняется строками carrier: {", ".join(clash)}')
    originals = read_originals(game, {int(ident, 16) for ident, _ in specs}, package)
    resources = []
    for ident, path in specs:
        if int(ident, 16) not in originals:
            raise CarrierError(f'--raw-resource: ресурса {ident} нет в prime пакета {package}')
        original, data_file = originals[int(ident, 16)]
        pinned = pilot_contract.RAW_REPLACEMENT_ALLOWED[package][ident]
        if bp.sha_bytes(original) != pinned:
            raise CarrierError(f'--raw-resource: оригинал {ident} в игре отличается от закреплённого (сборка другая?)')
        payload = path.read_bytes()
        if not payload:
            raise CarrierError(f'--raw-resource: пустой файл {path}')
        resources.append({'package': package, 'id_hex': ident, 'kind': 'raw-replacement', 'original': original,
                          'original_sha256': bp.sha_bytes(original), 'items': [], 'data_file': data_file,
                          'alias': f'/Pilot/{ident}.bin', 'payload': payload, 'sha256': bp.sha_bytes(payload),
                          'rows_changed': 0, 'rows_total': 0, 'source_path': str(path.resolve())})
    return resources


def pick_subset(entries: list[dict], tables: int, seed: int) -> list[dict]:
    """tables случайных .lang-таблиц + Global (если в carrier есть)."""
    lang = sorted({r['resource'] for r in entries if r['namespace'] == 'pb-lang'})
    chosen = set(random.Random(seed).sample(lang, min(tables, len(lang))))
    return [r for r in entries if r['namespace'] == 'global' or r['resource'] in chosen]


# ---------------------------------------------------------------- состояние установки и сборка

def installed_state(game: Path, packages: list[str]) -> dict:
    """Как у пилота, но override может уже содержать записи (установленный пилот): берём заголовок из каталога установки.

    Что именно лежит в установленном override, проверяет game_layer_deploy при plan/apply, а не сборщик.
    """
    cache = game / 'QtsDataFolder/QtsVFSCache/packages'
    hashes = pilot_contract.read_prime_pkg_hashes(game / 'QtsDataFolder/QtsVFSCache/QtsVFS.log')
    infos, headers, versions = {}, {}, set()
    for package in packages:
        if package not in hashes:
            raise CarrierError(f'В QtsVFS.log нет pkgHash prime-пакета {package}')
        info_path = cache / package / f'{package}.info'
        info = info_path.read_bytes()
        data = pilot_contract.installed_info(cache, package)
        versions.add(data['version'])
        infos[package] = {'source': str(info_path), 'sha256': bp.sha_bytes(info), 'data': json.loads(info.decode('utf-8-sig'))}
        with QtsContainer(cache / package / f'{package}.db', raw_values=True) as container:
            if bp.HEADER_ID not in container.entries or container.invalid_record_count:
                raise CarrierError(f'У override пакета {package} нет заголовка или есть повреждённые записи')
            header = container.raw_record_values(bp.HEADER_ID)[0].hex()
        if set(pilot_contract.header_hashes(header).values()) != {hashes[package]}:
            raise CarrierError(f'Базовый хеш в заголовке override пакета {package} не равен pkgHash prime')
        headers[package] = header
    if len(versions) != 1:
        raise CarrierError('Пакеты кандидата имеют разные версии сборки')
    base_info = pilot_contract.installed_info(cache, '0')
    return {'hashes': {p: hashes[p] for p in packages}, 'infos': infos, 'headers': headers, 'version': versions.pop(),
            'revision': base_info.get('revision', '0')}


def build(game: Path, resources: list[dict], output: Path, stub: Path, keep_lab: bool, package: str = PACKAGE) -> dict:
    """Сборка override одного пакета (1, 4, 19...; Lua-пакет 10 собирает build_lua_package.py тем же кодом)."""
    state = installed_state(game, [package])
    all_packages = ['0', package]
    prime_root = game / 'WXQ_Data/Package/builtin'
    prime_files = [f'{p}/{f.name}' for p in all_packages for f in sorted((prime_root / p).glob('*.db'))]
    total = sum((prime_root / rel).stat().st_size for rel in prime_files)
    thread_id = uuid.uuid4().hex
    lab = Path(tempfile.gettempdir()) / 'wxq-pilot' / 'tasks' / thread_id / 'work' / ('pilot-' + uuid.uuid4().hex[:8])
    lab.mkdir(parents=True)
    if shutil.disk_usage(lab).free < total * 1.3 + (256 << 20):
        bp.cleanup_lab_task(lab, thread_id)
        raise CarrierError(f'Не хватает места во временной папке: нужно около {int(total * 1.3 + (256 << 20)) >> 20} МБ')
    try:
        return bp._build_in_lab(game, resources, {}, [], output, stub, state, all_packages, [package], prime_files, thread_id, lab)
    finally:
        if not keep_lab:
            bp.cleanup_lab_task(lab, thread_id)


# ---------------------------------------------------------------- отчёт и манифест

def reason_code(reason: str) -> str:
    return reason.split(':', 1)[0]


def finalize(output: Path, manifest: dict, carrier_path: Path, meta: dict, stats: dict, excluded: list[dict], resources: list[dict],
             seconds: float, subset: dict | None, pending: list[dict] | None = None, text_edits_path: Path | None = None) -> None:
    """Заменяет пилотные поля манифеста на описание массового носителя и пишет REPORT.md/excluded.json.

    carrier_path - файл, чей SHA попадает в content_sha256 (carrier либо, если carrier нет, файл текстовых правок)."""
    manifest['content_sha256'] = bp.sha(carrier_path)
    for kind, (_package, key) in pilot_contract.TEXT_KINDS.items():
        declared = [{k: v for k, v in r['declaration'].items() if k != 'translations'} for r in resources if r['kind'] == kind]
        if declared:
            manifest[key] = declared
    manifest['raw_replacements'] = [{'package': r['package'], 'id_hex': r['id_hex'], 'kind': 'raw-replacement', 'original_sha256': r['original_sha256'],
                                     'sha256': r['sha256'], 'bytes': len(r['payload']), 'original_bytes': len(r['original']),
                                     'source': r['source_path']} for r in resources if r['kind'] == 'raw-replacement']
    manifest['carrier'] = {'input': str(carrier_path), 'tm': meta.get('tm'), 'tm_status': meta.get('tm_status'),
                           'rows_changed': stats['rows_changed'], 'resources': len(resources), 'excluded_rows': len(excluded),
                           'subset': subset, 'text_edits': ({'input': str(text_edits_path), 'sha256': bp.sha(text_edits_path)} if text_edits_path else None),
                           'per_resource': [{'id_hex': r['id_hex'], 'kind': r['kind'], 'rows_changed': r['rows_changed'],
                                                               'rows_total': r['rows_total'], 'payload_bytes': len(r['payload'])} for r in resources]}
    raw_note = ['Ресурсы kind=raw-replacement подменяют целый ресурс пакета 1 (правила переноса строк); читаются ли они игрой из override до его монтирования — офлайн не доказать'] if manifest['raw_replacements'] else []
    text_note = ['Текстовые правки таблиц (pb-table-text) и Unity-текста (unity-text): кодек доказывает только «изменились заявленные тексты»; читает ли клиент эти ресурсы из override, '
                 'как ложится русский текст по ширине кнопок и полей, офлайн не доказать'] if any(r['kind'] in pilot_contract.TEXT_KINDS for r in resources) else []
    manifest['limitations'] = raw_note + text_note + [
        'Игра и лаунчер не запускались: отображение, ACE/SGuard и перенос слов в клиенте не проверены',
        'Размер/шрифт игры не менялись; места вывода (слоты) неизвестны, вёрстка проверена без слота: символы шрифта, невидимые знаки, разрыв слов',
        ('Память переводов утверждена: RU349 с каноном терминов + переводы 13.10.2026 (независимое ревью выборки Opus); качество в игре не проверено'
         if meta.get('tm_status') == 'approved' else 'Память переводов временная (не утверждена): к установке не годится'),
        'Пилот (override пакетов 1 и 19) снимается restore по receipt перед установкой; текстуры пакета 19 ставятся отдельным кандидатом',
        'Язык клиента остаётся zh_cn']
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', 'utf-8')
    source = output / 'pilot-content.used.json'
    if source.exists():
        source.replace(output / 'carrier-input.json')
    (output / 'excluded.json').write_text(json.dumps(excluded, indent=1, ensure_ascii=False) + '\n', 'utf-8')
    (output / 'REPORT.md').write_text(render_report(manifest, stats, excluded, resources, seconds, subset, pending or []), 'utf-8')


def render_report(manifest: dict, stats: dict, excluded: list[dict], resources: list[dict], seconds: float, subset: dict | None,
                  pending: list[dict] | None = None) -> str:
    codes = collections.Counter(reason_code(r) for row in excluded for r in row['reasons'])
    reads = manifest['native_verification']['reads']
    lines = ['# Отчёт массового носителя', '',
             f'Версия игры: {manifest["game_version"]}; пакет: {', '.join(manifest['packages'])}; собрано: {manifest["built_at"]}; время сборки: {seconds:.0f} с', '',
             'Статус: кандидат собран и проверен нативным SDK офлайн, в игру НЕ установлен.', '']
    if subset:
        lines += [f'Режим: ПОДМНОЖЕСТВО ({subset["tables"]} случайных таблиц + Global, seed {subset["seed"]}); не для установки.', '']
    lines += ['## Размеры', '',
              f'- ресурсов: {len(resources)} ({sum(r["kind"] == "lang" for r in resources)} .lang + {sum(r["kind"] == "global-localize" for r in resources)} Global'
              f' + {sum(r["kind"] == "raw-replacement" for r in resources)} raw-replacement + {sum(r["kind"] == "pb-table-text" for r in resources)} pb-table-text'
              f' + {sum(r["kind"] == "unity-text" for r in resources)} unity-text + {sum(r["kind"] in TEXTURE_KINDS for r in resources)} текстур)',
              f'- строк вставлено: {stats["rows_changed"]}; исключено из-за проверок: {len(excluded)}; равны оригиналу: {stats.get("equal_to_original", 0)}',
              f'- байт нагрузок: {sum(len(r["payload"]) for r in resources)}; файлы пакета: ' + ', '.join(f'{f["relative"]} {f["bytes"]}' for f in manifest['files']),
              '', '## Исключено (оригинал остаётся)', '']
    if not excluded:
        lines += ['Нет.', '']
    else:
        lines += ['| причина | строк |', '|---|---|'] + [f'| {code} | {count} |' for code, count in codes.most_common(15)]
        lines += ['', 'Полный список с русским текстом: excluded.json. Примеры:', '']
        for row in excluded[:20]:
            lines.append(f'- {row["resource"]}/{row["key"]}: {"; ".join(row["reasons"])[:160]}')
        lines.append('')
    if manifest['raw_replacements']:
        lines += ['## Замены ресурсов целиком (raw-replacement)', '', '| ресурс | байт было -> стало | sha256 оригинала | sha256 замены |', '|---|---|---|---|']
        lines += [f'| {r["package"]}/{r["id_hex"]} | {r["original_bytes"]} -> {r["bytes"]} | {r["original_sha256"]} | {r["sha256"]} |' for r in manifest['raw_replacements']]
        lines += ['', 'Байты берутся из файла как есть; нативная проверка SDK прочитала их из собранного override.', '']
    texts = [r for r in resources if r['kind'] in pilot_contract.TEXT_KINDS]
    if texts:
        lines += ['## Текстовые правки таблиц и Unity-текста', '', '| ресурс | адрес | китайский | русский | знаков cn -> ru |', '|---|---|---|---|---|']
        for r in texts:
            for item in r['declaration']['translations']:
                where = item.get('address') or f'{item["path_id"]}:{item["field"]}'
                lines.append(f'| {r["package"]}/{r["id_hex"]} | {where} | {item["cn"]} | {item["ru"]} | {len(item["cn"])} -> {len(item["ru"])} |')
        lines += ['']
    if pending:
        lines += ['## Нет утверждённого перевода (в кандидат НЕ вставлено)', '', '| таблица/колонка | китайский | предложение | примечание |', '|---|---|---|---|']
        lines += [f'| {p.get("table_id", "")} {p.get("path", p.get("resource", ""))} | {p.get("cn", "")} | {p.get("proposal", "")} | {p.get("note", "")} |' for p in pending]
        lines += ['']
    lines += ['## Проверки', '',
              f'- нативных чтений совпало: {sum(1 for r in reads if r["matches"])}/{len(reads)}',
              '- VFS_CheckPackage: ' + ', '.join(sorted(c['function'].rsplit('_', 1)[1] for c in manifest['native_verification']['calls']
                                                       if c['function'].startswith('VFS_CheckPackage_') and c['result'] is True)),
              f'- GlobalIndexPrime.data не менялся: {manifest["prime_global_index_sha256"]}',
              '- каждая нагрузка: LangDocument validate_rewrite / GlobalMapDocument prove_text_only / pb_table_codec и unity_text_codec prove_text_only (остальное байт-в-байт)',
              '- заголовок override равен установленному, записей удаления нет, vendor diff не вызывался', '', '## Не проверено', '']
    lines += [f'- {text}' for text in manifest['limitations']]
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-root', type=Path, default=None, help='По умолчанию установка, которую использует лаунчер')
    parser.add_argument('--package', default=PACKAGE, help='Пакет кандидата: один кандидат на пакет (1, 4, 19...)')
    parser.add_argument('--carrier', type=Path, default=None, help='Строки .lang/Global (только пакет 1; по умолчанию DEFAULT_CARRIER для пакета 1)')
    parser.add_argument('--text-edits', type=Path, default=None, help='Файл правок таблиц и Unity-текста (формат - в шапке скрипта)')
    parser.add_argument('--fit-styles', action='store_true', help='Для Unity-надписей, которые по геометрии не влезут по-русски, включить автоподбор размера (m_BestFit, localization/unity_fit.py)')
    parser.add_argument('--catalog', type=Path, default=DEFAULT_CATALOG, help='Каталог колонок: правка допускается при status=include')
    parser.add_argument('--import-textures', type=Path, default=None, metavar='DIR',
                        help='Взять rgba32/bc7-текстуры пакета из готового кандидата build/pilot-... (SHA сверяются)')
    parser.add_argument('--slots', type=Path, default=None, help='Необязательно: {"<namespace>/<resource>/<key>": {slot}}')
    parser.add_argument('--output', type=Path, default=None, help='build/carrier-<дата>-<id>; для --subset — любая новая папка вне игры')
    parser.add_argument('--subset', type=int, default=0, metavar='N', help='Собрать только N случайных .lang-таблиц и Global (лаборатория)')
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--raw-resource', action='append', default=[], metavar='HEXID=PATH',
                        help='Заменить целый ресурс пакета 1 готовыми байтами (повторяемо; только id из белого списка pilot_contract)')
    parser.add_argument('--check', action='store_true', help='Только проверить строки и построить нагрузки, ничего не записывая')
    parser.add_argument('--keep-lab', action='store_true')
    args = parser.parse_args(argv)
    started = time.time()
    subset = {'tables': args.subset, 'seed': args.seed} if args.subset else None
    try:
        package = args.package
        if not pilot_contract.PACKAGE_ID.fullmatch(package):
            raise CarrierError(f'--package: неверный пакет {package!r}')
        raw_specs = parse_raw_specs(args.raw_resource, package)
        carrier_path = args.carrier
        if carrier_path is None and package == PACKAGE and not args.text_edits and not args.import_textures:
            carrier_path = DEFAULT_CARRIER  # прежнее поведение: без других входов собирается носитель по умолчанию
        if carrier_path is not None and package != PACKAGE:
            raise CarrierError('--carrier: строки .lang/Global есть только в пакете 1')
        text_edits, pending = load_text_edits(args.text_edits)
        entries, meta = load_carrier(carrier_path) if carrier_path else ([], {})
        if not entries and not text_edits and not args.import_textures and not raw_specs:
            raise CarrierError('Нечего собирать: нужен carrier, --text-edits, --import-textures или --raw-resource')
        game = wxq_install.require_install_structure(args.game_root if args.game_root else wxq_install.detect_active_install(), full=True)
        if subset:
            entries = pick_subset(entries, args.subset, args.seed)
            output = (args.output or Path(tempfile.gettempdir()) / 'wxq-carrier-subset' / uuid.uuid4().hex[:8]).resolve()
        else:
            prefix = 'carrier-' if entries else 'pilot-'  # без carrier нет утверждённой памяти переводов: контракт carrier-* к такому кандидату неприменим
            output = (args.output or PROJECT / 'build' / f'{prefix}{datetime.date.today().isoformat()}-{uuid.uuid4().hex[:6]}').resolve()
            if output.parent != (PROJECT / 'build').resolve() or not output.name.startswith(prefix):
                print(f'Выход должен быть build/{prefix}<имя> внутри проекта', file=sys.stderr)
                return 2
        if output.is_relative_to(game):
            raise CarrierError('Выход не может лежать внутри игры')
        if (output / 'manifest.json').exists() or (output / 'packages').exists():
            raise CarrierError(f'Каталог уже содержит сборку: {output}')
        latin = latin_allow.default_allowlist(ru_cli.GLOSSARY)
        glyphs = load_glyphs()
        print(f'строк carrier: {len(entries)}; шрифты: {"есть" if glyphs else "НЕ загружены"}', flush=True)
        if entries:
            resources, excluded, stats = prepare(game, entries, slots=load_slots(args.slots), glyphs=glyphs, latin=latin,
                                                 progress=lambda text: print(text, flush=True))
        else:
            resources, excluded, stats = [], [], {'resources': 0, 'rows_changed': 0, 'excluded': 0}
        stats = collections.Counter(stats)
        text_resources, text_excluded, text_stats = prepare_text(game, package, text_edits, load_catalog(args.catalog), glyphs=glyphs, latin=latin, fit_styles=args.fit_styles)
        clash = sorted({r['id_hex'] for r in resources} & {r['id_hex'] for r in text_resources})
        if clash:
            raise CarrierError(f'Ресурс меняется и строками carrier, и текстовой правкой: {", ".join(clash)}')
        resources += text_resources
        excluded += text_excluded
        stats.update(text_stats)
        stats['excluded'] = len(excluded)
        raw = raw_resources(game, raw_specs, {r['id_hex'] for r in resources}, package)
        resources += raw
        if args.import_textures:
            resources += import_textures(game, package, args.import_textures.resolve(), {r['id_hex'] for r in resources})
        if not resources:
            raise CarrierError('Ни одной строки не прошло проверку: собирать нечего')
        print(json.dumps({'resources': len(resources), 'rows_changed': stats['rows_changed'], 'text_edits': stats['text_edits'],
                          'excluded': stats['excluded'], 'prepare_seconds': round(time.time() - started)}), flush=True)
        if args.check:
            return 0
        output.mkdir(parents=True, exist_ok=True)
        stub = output.with_name(output.name + '.input-stub.json')
        content_path = carrier_path or args.text_edits or (args.import_textures / 'manifest.json' if args.import_textures else None)
        if content_path is None:
            raise CarrierError('Нет входного файла для content_sha256: нужен --carrier, --text-edits или --import-textures')
        content_path = content_path.resolve()
        stub.write_text(json.dumps({'carrier': str(carrier_path) if carrier_path else None, 'content': str(content_path), 'sha256': bp.sha(content_path),
                                    'rows': len(entries), 'text_edits': len(text_edits)}) + '\n', 'utf-8')
        try:
            manifest = build(game, resources, output, stub, args.keep_lab, package)
        finally:
            stub.unlink(missing_ok=True)
        finalize(output, manifest, content_path, meta, stats, excluded, resources, time.time() - started, subset, pending,
                 args.text_edits.resolve() if args.text_edits else None)
    except bp.PilotError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps({'output': str(output), 'resources': len(resources), 'rows_changed': stats['rows_changed'], 'excluded': len(excluded),
                      'native_reads_match': all(r['matches'] for r in manifest['native_verification']['reads']), 'installed': False,
                      'seconds': round(time.time() - started)}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
