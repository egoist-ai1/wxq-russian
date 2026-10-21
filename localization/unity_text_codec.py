"""Замена строк m_Text/text/m_text в Unity SerializedFile v22 (Qts) без изменения остальных байт.

Правятся только строковые спаны (int32 длина + UTF-8 + нулевое выравнивание до 4). Объекты
перекладываются с выравниванием 8 относительно data_offset, в таблице объектов обновляются
byte_start/byte_size (ширина записей фиксирована), в заголовке - file_size. Всё остальное
(порядок данных, metadata_size, data_offset, типы, прочие объекты) сохраняется побайтно.
Файлы игры не читаются и не пишутся: на входе и выходе bytes.
"""
from __future__ import annotations

import functools
import json
import re
import struct
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT / 'scripts') not in sys.path:
    sys.path.insert(0, str(PROJECT / 'scripts'))

import game_unity_fields as guf  # noqa: E402

TEXT_FIELDS = ('m_Text', 'text', 'm_text')
CJK = re.compile('[㐀-䶿一-鿿　-〿＀-￯]')
CJK_BYTES = re.compile(rb'[\xe3-\xef][\x80-\xbf]{2}')
FILE_SIZE_OFFSET = 24  # '>Q' в заголовке v22


class UnityTextError(ValueError):
    """Файл или правка не годятся для безопасной замены текста."""


class ProofError(UnityTextError):
    """Новый файл не доказан как «изменился только текст»."""


@functools.lru_cache(maxsize=1)
def _catalog() -> dict:
    raw = Path(guf.DEFAULT_SCHEMAS).read_bytes()
    catalog = json.loads(raw.decode('utf-8-sig'))
    by_id: dict[str, list[dict]] = {}
    for item in catalog['schemas']:
        by_id.setdefault(item['id_hex'], []).append(item)
    return {'sha': guf.sha256(raw), 'by_id': by_id}


def schema_for(obj: dict) -> dict:
    sid = guf.schema_id_from_old_hash(obj['old_type_hash'])
    found = _catalog()['by_id'].get(sid, [])
    if len(found) != 1:
        raise UnityTextError(f'schema {sid}: ' + ('нет в каталоге' if not found else 'неоднозначна'))
    result = dict(found[0])
    result['catalog_sha256'] = _catalog()['sha']
    return result


def _body(raw: bytes, obj: dict) -> bytes:
    return raw[obj['byte_offset']:obj['byte_offset'] + obj['byte_size']]


def _parse_object(raw: bytes, obj: dict) -> dict:
    try:
        return guf.read_fields(_body(raw, obj), schema_for(obj))
    except UnityTextError:
        raise
    except (ValueError, UnicodeError, struct.error, KeyError) as error:
        raise UnityTextError(f'разбор объекта {obj["path_id"]}: {error}') from error


def _is_text_path(path: str) -> bool:
    """Последний компонент пути - m_Text/text/m_text (не элемент массива)."""
    last = path.rsplit('.', 1)[-1]
    return last in TEXT_FIELDS


def _hash_key_empty(spans: dict, path: str) -> bool:
    parent = path.rsplit('.', 1)[0] + '.' if '.' in path else ''
    sibling = spans.get(parent + 'm_textHashKey')
    return sibling is None or sibling['value'] == ''


def iter_objects(original: bytes):
    """(obj, parsed|None, error|None); объекты без CJK-байт не разбираются; ошибка не прерывает обход."""
    index = guf.read_index(original)
    for obj in index['objects']:
        if not CJK_BYTES.search(_body(original, obj)):
            yield obj, None, None
            continue
        try:
            yield obj, _parse_object(original, obj), None
        except UnityTextError as error:
            yield obj, None, str(error)


def list_text_fields(original: bytes) -> list[tuple[int, str, str]]:
    """(path_id, field_path, text) для m_Text/text/m_text с CJK и пустым соседним m_textHashKey."""
    out = []
    for obj, parsed, _ in iter_objects(original):
        if parsed is None:
            continue
        spans = {s['path']: s for s in parsed['spans']}
        for path, span in spans.items():
            if span['type'] == 'string' and _is_text_path(path) and CJK.search(span['value']) and _hash_key_empty(spans, path):
                out.append((obj['path_id'], path, span['value']))
    return out


def read_field_values(data: bytes, keys) -> dict[tuple[int, str], str]:
    """{(path_id, field_path): строка} для перечисленных полей; объекта или строкового поля нет - UnityTextError.

    В отличие от list_text_fields не требует CJK: нужно, чтобы прочитать уже переведённый файл."""
    by_object = _group(dict.fromkeys(keys))
    objects = {o['path_id']: o for o in guf.read_index(data)['objects']}
    out = {}
    for path_id, paths in by_object.items():
        if path_id not in objects:
            raise UnityTextError(f'объект {path_id} отсутствует')
        spans = {s['path']: s for s in _parse_object(data, objects[path_id])['spans']}
        for path in paths:
            span = spans.get(path)
            if span is None or span['type'] != 'string':
                raise UnityTextError(f'{path_id}:{path}: не строковое поле объекта')
            out[(path_id, path)] = span['value']
    return out


def _encode_string(text: str) -> bytes:
    data = text.encode('utf-8')
    return struct.pack('<i', len(data)) + data + b'\0' * (-len(data) % 4)


def _apply_to_object(body: bytes, parsed: dict, fields: dict, path_id: int) -> bytes:
    spans = {s['path']: s for s in parsed['spans']}
    for path in fields:
        span = spans.get(path)
        if span is None or span['type'] != 'string':
            raise UnityTextError(f'{path_id}:{path}: не строковое поле объекта')
        if not _is_text_path(path):
            raise UnityTextError(f'{path_id}:{path}: правка разрешена только для ' + '/'.join(TEXT_FIELDS))
        if any(body[span['value_end']:span['span_end']]):
            raise UnityTextError(f'{path_id}:{path}: выравнивание строки не нулевое')
        if not isinstance(fields[path], str):
            raise UnityTextError(f'{path_id}:{path}: ожидается str')
    out = bytearray(body)
    for path in sorted(fields, key=lambda p: spans[p]['offset'], reverse=True):
        span = spans[path]
        out[span['offset']:span['span_end']] = _encode_string(fields[path])
    return bytes(out)


def _layout(raw: bytes, index: dict) -> list:
    """Объекты по возрастанию смещения; между ними - нулевой зазор до выравнивания 8."""
    base = index['data_offset']
    ordered = sorted(index['objects'], key=lambda o: o['byte_offset'])
    prev_end = None
    for obj in ordered:
        if prev_end is not None:
            expected = base + ((prev_end - base + 7) & ~7)
            if obj['byte_offset'] != expected or any(raw[prev_end:obj['byte_offset']]):
                raise UnityTextError('раскладка объектов не «выравнивание 8 с нулевым зазором»')
        prev_end = obj['byte_offset'] + obj['byte_size']
    return ordered


def _group(edits: dict) -> dict:
    by_object: dict[int, dict[str, str]] = {}
    for (path_id, path), text in edits.items():
        by_object.setdefault(path_id, {})[path] = text
    return by_object


def replace(original: bytes, edits: dict) -> bytes:
    """Заменить строки; без правок результат побайтно равен оригиналу."""
    index = guf.read_index(original)
    ordered = _layout(original, index)
    by_object = _group(edits)
    known = {o['path_id'] for o in index['objects']}
    if len(known) != len(index['objects']):
        raise UnityTextError('повторяющиеся path_id')
    for path_id in by_object:
        if path_id not in known:
            raise UnityTextError(f'объект {path_id} отсутствует')
    header = bytearray(original[:index['data_offset']])
    body = bytearray()
    base = index['data_offset']
    cursor = base
    if ordered:
        first = ordered[0]['byte_offset']
        body += original[base:first]
        cursor = first
    for obj in ordered:
        pad = (-(cursor - base)) % 8
        body += b'\0' * pad
        cursor += pad
        data = _body(original, obj)
        if obj['path_id'] in by_object:
            data = _apply_to_object(data, _parse_object(original, obj), by_object[obj['path_id']], obj['path_id'])
        rec = obj['index_record_offset']
        struct.pack_into('<q', header, rec + 8, cursor - base)
        struct.pack_into('<I', header, rec + 16, len(data))
        body += data
        cursor += len(data)
    tail = original[ordered[-1]['byte_offset'] + ordered[-1]['byte_size']:] if ordered else b''
    body += tail
    cursor += len(tail)
    struct.pack_into('>Q', header, FILE_SIZE_OFFSET, cursor)
    return bytes(header) + bytes(body)


def _fail(message: str):
    raise ProofError(message)


def prove_text_only(original: bytes, new: bytes, edits: dict) -> dict:
    """Доказать, что new отличается от original только заменами edits; иначе ProofError."""
    try:
        a = guf.read_index(original)
        b = guf.read_index(new)
    except ValueError as error:
        raise ProofError(f'индекс: {error}') from error
    for key in ('engine', 'metadata_size', 'data_offset', 'object_table_end', 'custom_QTS_type_trailer'):
        if a[key] != b[key]:
            _fail(f'заголовок: {key} изменился')
    if [o['path_id'] for o in a['objects']] != [o['path_id'] for o in b['objects']]:
        _fail('таблица объектов: состав или порядок изменился')
    for x, y in zip(a['objects'], b['objects']):
        for key in ('type_id', 'class_id', 'index_record_offset', 'old_type_hash', 'script_id'):
            if x[key] != y[key]:
                _fail(f'объект {x["path_id"]}: {key} изменился')
    head_a, head_b = bytearray(original[:a['data_offset']]), bytearray(new[:a['data_offset']])
    for lo, hi in [(FILE_SIZE_OFFSET, FILE_SIZE_OFFSET + 8)] + [(o['index_record_offset'] + 8, o['index_record_offset'] + 20) for o in a['objects']]:
        head_a[lo:hi] = head_b[lo:hi] = b'\0' * (hi - lo)
    if head_a != head_b:
        _fail('метаданные изменились вне file_size и byte_start/byte_size')
    by_object = _group(edits)
    old_by = {o['path_id']: o for o in a['objects']}
    new_by = {o['path_id']: o for o in b['objects']}
    for path_id in by_object:
        if path_id not in old_by:
            _fail(f'правка объекта {path_id}: его нет в оригинале')
    originals = {}
    for path_id, o in old_by.items():
        n = new_by[path_id]
        if (n['byte_offset'] - b['data_offset']) % 8:
            _fail(f'объект {path_id}: смещение не кратно 8')
        old_body, new_body = _body(original, o), _body(new, n)
        if path_id not in by_object:
            if old_body != new_body:
                _fail(f'неправленый объект {path_id} изменился')
            continue
        try:
            po, pn = _parse_object(original, o), _parse_object(new, n)
        except UnityTextError as error:
            raise ProofError(str(error)) from error
        if pn['consumed'] != len(new_body) or not pn['all_bytes_consumed']:
            _fail(f'объект {path_id}: разбор нового не поглотил все байты')
        if [s['path'] for s in po['spans']] != [s['path'] for s in pn['spans']]:
            _fail(f'объект {path_id}: состав полей изменился')
        for so, sn in zip(po['spans'], pn['spans']):
            path = so['path']
            edited = path in by_object[path_id]
            if sn['value'] != (by_object[path_id][path] if edited else so['value']):
                _fail(f'объект {path_id}:{path}: значение не совпало с ожидаемым')
            if not edited and old_body[so['offset']:so['span_end']] != new_body[sn['offset']:sn['span_end']]:
                _fail(f'объект {path_id}:{path}: байты неправленого поля изменились')
            if sn['type'] == 'string':
                if any(new_body[sn['value_end']:sn['span_end']]):
                    _fail(f'объект {path_id}:{path}: ненулевое выравнивание')
                new_body[sn['offset'] + 4:sn['value_end']].decode('utf-8')
        originals[path_id] = {s['path']: s['value'] for s in po['spans']}
    if len(new) != b['file_size']:
        _fail('file_size не равен длине файла')
    last_a = max(a['objects'], key=lambda o: o['byte_offset'])
    last_b = max(b['objects'], key=lambda o: o['byte_offset'])
    if (len(original) - last_a['byte_offset'] - last_a['byte_size']) != (len(new) - last_b['byte_offset'] - last_b['byte_size']):
        _fail('хвост после последнего объекта изменился')
    back = {(pid, path): originals[pid][path] for pid, fields in by_object.items() for path in fields}
    if replace(new, back) != original:
        _fail('обратная замена не вернула оригинал')
    return {'objects': len(old_by), 'edited_objects': len(by_object), 'edits': len(edits), 'old_size': len(original), 'new_size': len(new)}
