"""Метаданные повторного применения перевода по ключу и точному SHA исходной строки.

Вход — проверенная пара оригинал/перевод. Выход не содержит исходных текстов:
только ключи, SHA исходных UTF-8 payload и утверждённые переводы. Числа, ключи,
порядок и неизвестные protobuf-поля остаются побайтно прежними.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from scripts.game_lang_codec import CodecError, LangDocument, _field  # noqa: E402
from localization.global_map_codec import GlobalMapDocument  # noqa: E402

MAX_LANG_BYTES = 128 * 1024 * 1024
MAX_GLOBAL_BYTES = 2 * 1024 * 1024
MAX_ROWS = 32768
MAX_FIELDS = 100000
MAX_KEY_BYTES = 512
MAX_VALUE_BYTES = 16384
CODECS = {'lang': 'lang-map-v1', 'global-localize': 'global-map-v1'}


def _preflight(data: bytes, maximum: int) -> None:
    """Ограничиваем число root/row полей до создания документа кодека."""
    if type(data) is not bytes or not 0 < len(data) <= maximum:
        raise CodecError('compat-map-document-budget')
    used = rows = position = 0
    while position < len(data):
        if used >= MAX_FIELDS:
            raise CodecError('compat-map-field-budget')
        outer, position = _field(data, position)
        used += 1
        if outer.number != 1:
            continue
        if outer.wire != 2:
            raise CodecError('compat-map-root-row-wrong-wire')
        rows += 1
        if rows > MAX_ROWS:
            raise CodecError('compat-map-row-budget')
        inner_position = 0
        while inner_position < len(outer.payload):
            if used >= MAX_FIELDS:
                raise CodecError('compat-map-field-budget')
            inner, inner_position = _field(outer.payload, inner_position)
            used += 1
            if inner.number in (1, 2) and inner.wire != 2:
                raise CodecError('compat-map-known-row-field-wrong-wire')


def _parse(kind: str, data: bytes):
    _preflight(data, MAX_LANG_BYTES if kind == 'lang' else MAX_GLOBAL_BYTES)
    document = LangDocument.parse(data) if kind == 'lang' else GlobalMapDocument.parse(data)
    if not document.rows:
        raise CodecError('compat-map-no-text-rows')
    for row in document.rows:
        if not row.key or len(row.key.encode('utf-8', errors='strict')) > MAX_KEY_BYTES:
            raise CodecError('compat-map-key-budget')
        if len(row.text.encode('utf-8', errors='strict')) > MAX_VALUE_BYTES:
            raise CodecError('compat-map-value-budget')
    return document


def _rewrite(document, replacements: dict[str, str]) -> bytes:
    """Только wire-замена: смысл перевода уже проверен сборщиком кандидата."""
    rows = {row.outer_index: row for row in document.rows}
    if set(replacements) - {row.key for row in document.rows}:
        raise CodecError('compat-map-selected-key-missing')
    output = []
    for index, outer in enumerate(document.fields):
        row = rows.get(index)
        if row is None or row.key not in replacements:
            output.append(outer.raw)
            continue
        encoded = replacements[row.key].encode('utf-8', errors='strict')
        payload = b''.join(field.replace_payload(encoded) if field_index == row.text_index else field.raw
                           for field_index, field in enumerate(row.fields))
        output.append(outer.replace_payload(payload))
    return b''.join(output)


def _prove_pair(source, target, original: bytes, translated: bytes) -> dict[str, str]:
    if len(source.fields) != len(target.fields) or len(source.rows) != len(target.rows):
        raise CodecError('compat-map-field-or-row-count-drift')
    identity = lambda row: (row.outer_index, row.key, row.key_index, row.text_index)
    if [identity(row) for row in source.rows] != [identity(row) for row in target.rows]:
        raise CodecError('compat-map-key-order-or-position-drift')
    row_indices = {row.outer_index for row in source.rows}
    for index, (before, after) in enumerate(zip(source.fields, target.fields)):
        if index not in row_indices and before.raw != after.raw:
            raise CodecError('compat-map-unknown-outer-field-drift')
    restored = [field.raw for field in target.fields]
    changes = {}
    for before, after in zip(source.rows, target.rows):
        if len(before.fields) != len(after.fields):
            raise CodecError('compat-map-row-field-count-drift')
        for index, (old_field, new_field) in enumerate(zip(before.fields, after.fields)):
            if index != before.text_index and old_field.raw != new_field.raw:
                raise CodecError('compat-map-key-or-unknown-row-field-drift')
        encoded = after.text.encode('utf-8', errors='strict')
        payload = b''.join(field.replace_payload(encoded) if index == before.text_index else field.raw
                           for index, field in enumerate(before.fields))
        before_outer = source.fields[before.outer_index]
        if before_outer.replace_payload(payload) != target.fields[after.outer_index].raw:
            raise CodecError('compat-map-text-or-row-framing-drift')
        inverse = b''.join(before.fields[index].raw if index == before.text_index else field.raw
                           for index, field in enumerate(after.fields))
        restored[after.outer_index] = before_outer.tag + before_outer.length + inverse
        if before.text != after.text:
            changes[before.key] = after.text
    if b''.join(restored) != original:
        raise CodecError('compat-map-controlled-inverse-failed')
    if _rewrite(source, changes) != translated:
        raise CodecError('compat-map-replay-failed')
    return changes


def make_reuse(kind: str, original: bytes, translated: bytes) -> dict | None:
    """Создаёт source-bound карту; неизвестный kind не объявляет текстовым."""
    codec = CODECS.get(kind)
    if codec is None:
        return None
    source, target = _parse(kind, original), _parse(kind, translated)
    changes = _prove_pair(source, target, original, translated)
    rows = [{'key': row.key, 'source_sha256': hashlib.sha256(row.text.encode('utf-8')).hexdigest(),
             'target': changes[row.key]} for row in source.rows if row.key in changes]
    # Повторное проигрывание именно выходной карты исключает скрытые поля и иной порядок выбора.
    replay = {row['key']: row['target'] for row in rows}
    if _rewrite(source, replay) != translated:
        raise CodecError('compat-map-metadata-replay-failed')
    return {'schema': 1, 'codec': codec, 'rows': rows}
