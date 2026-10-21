"""Кодек текстовых правок в protobuf-таблицах .bytes пакета 1.

Устройство таблицы (проверено по всем таблицам prime-пакета 1, см. scripts/audit_pb_tables.py):
  поле 1 — MD5 (заглавный hex, 32 символа) от склейки НАГРУЗОК (без тега и длины) всех полей 2 по порядку;
  поле 2 — повторяющиеся записи-сообщения, внутри вложенные поля, тексты на путях вроде 2[i]/13[0], 2[i]/5[0]/3[0];
  поле 3 — индекс (хеши ключей, номера строк), поле 4 — u32 неизвестного смысла: оба переносятся побайтно.

Правится только текст-лист записи, содержащий CJK. Длины всех предков пересчитываются, MD5 обновляется.
Адрес листа — путь из `номер[вхождение]`, например `2[3]/13[0]`; сегмент без индекса означает `[0]`.
"""
from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from scripts.game_lang_codec import CodecError, fields, varint  # noqa: E402

CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
MD5_HEX = re.compile(rb'[0-9A-F]{32}')
SEGMENT = re.compile(r'([1-9][0-9]*)(?:\[([0-9]+)\])?')
MAX_DEPTH = 24
MESSAGE_TAGS = (0x08, 0x0a, 0x10, 0x12, 0x18, 0x1a)
FORBIDDEN_PATH = re.compile(r'ArtHub', re.IGNORECASE)


class PbTableError(ValueError):
    """Таблица или правка не годятся: сообщение объясняет причину."""


@dataclass
class Node:
    number: int
    wire: int
    raw: bytes
    tag: bytes
    length: bytes
    payload: bytes
    kind: str  # 'msg' | 'text' | 'opaque' | 'scalar'
    children: list = field(default_factory=list)
    address: str = ''
    text: str | None = None


@dataclass
class Table:
    data: bytes
    top: list
    md5_declared: str
    md5_actual: str
    index: dict

    @property
    def md5_ok(self) -> bool:
        return self.md5_declared == self.md5_actual

    @property
    def rows(self) -> list:
        return [node for node in self.top if node.number == 2]


def _is_text(payload: bytes):
    if not payload:
        return None
    try:
        text = payload.decode('utf-8')
    except UnicodeDecodeError:
        return None
    if all(ch >= ' ' or ch in '\n\r\t' for ch in text) and '\x7f' not in text:
        return text
    return None


def _make_node(wire_field, address: str) -> Node:
    return Node(wire_field.number, wire_field.wire, wire_field.raw, wire_field.tag, wire_field.length,
                wire_field.payload, 'scalar', address=address)


def _plausible_message(wires) -> bool:
    """Целое сообщение с «человеческими» номерами полей: так отличаем вложенное сообщение от текста."""
    return bool(wires) and all(1 <= w.number <= 200 and w.wire in (0, 1, 2, 5) for w in wires)


def _build_nodes(data: bytes, path: str, depth: int, index: dict, like=None):
    """Разбирает сообщение; None, если байты не образуют целое сообщение.

    like — узлы оригинала: при разборе нового файла вид каждого поля берётся из оригинала, а не угадывается заново
    (иначе подставленный текст мог бы случайно разобраться как сообщение или наоборот).
    """
    try:
        wires = fields(data)
    except CodecError:
        return None
    if not wires:
        return None
    if like is not None and len(like) != len(wires):
        raise PbTableError(f'{path or "корень"}: число полей изменилось')
    nodes = []
    seen: dict = {}
    for position, wire_field in enumerate(wires):
        occurrence = seen.get(wire_field.number, 0)
        seen[wire_field.number] = occurrence + 1
        address = f'{path}/{wire_field.number}[{occurrence}]' if path else f'{wire_field.number}[{occurrence}]'
        node = _make_node(wire_field, address)
        if like is not None and (like[position].number, like[position].wire) != (wire_field.number, wire_field.wire):
            raise PbTableError(f'{address}: изменился тег или порядок полей')
        if wire_field.wire == 2:
            if like is not None:
                ref = like[position]
                if ref.kind == 'text':
                    try:
                        node.kind, node.text = 'text', wire_field.payload.decode('utf-8')
                    except UnicodeDecodeError as error:
                        raise PbTableError(f'{address}: текст не UTF-8') from error
                elif ref.kind == 'msg':
                    children = _build_nodes(wire_field.payload, address, depth + 1, index, ref.children)
                    if children is None:
                        raise PbTableError(f'{address}: сообщение перестало разбираться')
                    node.kind, node.children = 'msg', children
                else:
                    node.kind = 'opaque'
            else:
                node.kind = 'opaque'
                payload = wire_field.payload
                text = _is_text(payload)
                message = False
                if payload and depth < MAX_DEPTH:
                    try:
                        inner = fields(payload)
                    except CodecError:
                        inner = ()
                    # сообщение, начинающееся управляющим тегом (поля 1-3), не путаем с текстом;
                    # печатный текст с «похожим» разбором остаётся текстом
                    message = _plausible_message(inner) and (text is None or payload[0] in MESSAGE_TAGS)
                if message:
                    node.kind, node.children = 'msg', _build_nodes(payload, address, depth + 1, index)
                elif text is not None:
                    node.kind, node.text = 'text', text
        elif wire_field.wire == 3:
            node.kind = 'opaque'
        index[address] = node
        nodes.append(node)
    return nodes


def _parse(original: bytes, like=None) -> Table:
    if not isinstance(original, (bytes, bytearray)) or not original:
        raise PbTableError('Таблица пуста или не bytes')
    original = bytes(original)
    index: dict = {}
    try:
        wires = fields(original)
    except CodecError as error:
        raise PbTableError(f'Не protobuf: {error}') from error
    if not wires or wires[0].number != 1 or wires[0].wire != 2 or not MD5_HEX.fullmatch(wires[0].payload):
        raise PbTableError('Поле 1 не MD5 в заглавном hex')
    if like is not None and len(like) != len(wires):
        raise PbTableError('Число полей верхнего уровня изменилось')
    top = []
    seen: dict = {}
    for position, wire_field in enumerate(wires):
        occurrence = seen.get(wire_field.number, 0)
        seen[wire_field.number] = occurrence + 1
        address = f'{wire_field.number}[{occurrence}]'
        node = _make_node(wire_field, address)
        ref = like[position] if like is not None else None
        if ref is not None and (ref.number, ref.wire) != (wire_field.number, wire_field.wire):
            raise PbTableError(f'{address}: изменились тег или порядок полей')
        if wire_field.wire == 2:
            node.kind = 'opaque'
            if wire_field.number == 2 and wire_field.payload:
                children = _build_nodes(wire_field.payload, address, 1, index,
                                        ref.children if ref is not None and ref.kind == 'msg' else None)
                if children is not None:
                    node.kind, node.children = 'msg', children
                elif ref is not None and ref.kind == 'msg':
                    raise PbTableError(f'{address}: запись перестала разбираться')
        elif wire_field.wire == 3:
            node.kind = 'opaque'
        index[address] = node
        top.append(node)
    declared = wires[0].payload.decode('ascii')
    actual = hashlib.md5(b''.join(node.payload for node in top if node.number == 2)).hexdigest().upper()
    return Table(original, top, declared, actual, index)


def parse(original: bytes) -> Table:
    return _parse(original)


def normalize_address(address: str) -> str:
    parts = []
    for segment in str(address).split('/'):
        match = SEGMENT.fullmatch(segment)
        if not match:
            raise PbTableError(f'Неверный адрес: {address!r}')
        parts.append(f'{int(match[1])}[{int(match[2] or 0)}]')
    return '/'.join(parts)


def _walk_text(nodes):
    for node in nodes:
        if node.kind == 'text':
            yield node
        elif node.kind == 'msg':
            yield from _walk_text(node.children)


def list_text_leaves(original: bytes) -> list:
    """Тексты-листья записей (поле 2) с CJK: [(адрес, строка)]."""
    table = parse(original)
    return [(node.address, node.text) for node in _walk_text(table.rows) if CJK.search(node.text)]


def _rebuild(node: Node, edits: dict) -> tuple:
    """Возвращает (raw, payload) поля с учётом правок; без изменений — исходные байты."""
    if node.kind == 'text' and node.address in edits:
        payload = edits[node.address].encode('utf-8')
    elif node.kind == 'msg':
        payload = b''.join(_rebuild(child, edits)[0] for child in node.children)
    else:
        return node.raw, node.payload
    if payload == node.payload:
        return node.raw, node.payload
    length = node.length if len(payload) == len(node.payload) else varint(len(payload))
    return node.tag + length + payload, payload


def _apply(table: Table, edits: dict) -> bytes:
    pieces = []
    rows_payload = []
    md5_position = None
    for position, node in enumerate(table.top):
        if node.number == 2:
            raw, payload = _rebuild(node, edits)
            rows_payload.append(payload)
            pieces.append(raw)
        else:
            pieces.append(node.raw)
        if node.number == 1 and md5_position is None:
            md5_position = position
    digest = hashlib.md5(b''.join(rows_payload)).hexdigest().upper().encode('ascii')
    first = table.top[md5_position]
    pieces[md5_position] = first.raw if digest == first.payload else first.tag + first.length + digest
    return b''.join(pieces)


def _check_edits(table: Table, edits) -> dict:
    if not isinstance(edits, dict):
        raise PbTableError('edits должен быть словарём адрес -> строка')
    result = {}
    for address, value in edits.items():
        key = normalize_address(address)
        if key in result:
            raise PbTableError(f'Адрес повторён: {key}')
        node = table.index.get(key)
        if node is None or node.kind != 'text' or not key.startswith('2['):
            raise PbTableError(f'{key}: не текстовый лист записи')
        if not isinstance(value, str):
            raise PbTableError(f'{key}: значение не строка')
        try:
            value.encode('utf-8')
        except UnicodeEncodeError as error:
            raise PbTableError(f'{key}: текст не кодируется в UTF-8') from error
        if FORBIDDEN_PATH.search(node.text):
            raise PbTableError(f'{key}: путь ресурса (ArtHub) не правится')
        if not CJK.search(node.text):
            raise PbTableError(f'{key}: исходный лист без CJK не правится')
        result[key] = value
    return result


def replace(original: bytes, edits: dict) -> bytes:
    """Подставляет тексты в CJK-листья, пересчитывает длины предков и MD5."""
    table = parse(original)
    if not table.md5_ok:
        raise PbTableError('MD5 оригинала не сходится с нагрузкой полей 2')
    checked = _check_edits(table, edits)
    checked = {key: value for key, value in checked.items() if value != table.index[key].text}
    return _apply(table, checked) if checked else table.data


def _same_shape(a_nodes, b_nodes, edits: dict, stats: dict) -> None:
    if len(a_nodes) != len(b_nodes):
        raise PbTableError('Число полей изменилось')
    for a, b in zip(a_nodes, b_nodes):
        if (a.number, a.wire, a.tag) != (b.number, b.wire, b.tag):
            raise PbTableError(f'{a.address}: изменился тег или порядок полей')
        if a.kind == 'text' and a.address in edits:
            if b.kind != 'text' or b.text != edits[a.address]:
                raise PbTableError(f'{a.address}: в новом файле не заявленный текст')
            stats['seen'] += 1
        elif a.kind == 'msg':
            if b.kind != 'msg':
                raise PbTableError(f'{a.address}: сообщение стало листом')
            _same_shape(a.children, b.children, edits, stats)
            if len(a.payload) == len(b.payload) and a.length != b.length:
                raise PbTableError(f'{a.address}: изменились байты длины при той же длине')
        elif a.raw != b.raw:
            raise PbTableError(f'{a.address}: изменён нелистовой или не заявленный лист')


def prove_text_only(original: bytes, new: bytes, edits: dict) -> dict:
    """Доказывает, что `new` отличается от `original` только заявленными текстами; иначе PbTableError."""
    before = parse(original)
    after = _parse(new, like=before.top)
    if not before.md5_ok:
        raise PbTableError('MD5 оригинала не сходится')
    if not after.md5_ok:
        raise PbTableError('MD5 нового файла не сходится')
    checked = _check_edits(before, edits)
    if len(before.top) != len(after.top):
        raise PbTableError('Число полей верхнего уровня изменилось')
    for a, b in zip(before.top, after.top):
        if (a.number, a.wire, a.tag) != (b.number, b.wire, b.tag):
            raise PbTableError(f'{a.address}: изменились тег или порядок полей')
        if a.number != 2 and a.number != 1 and a.raw != b.raw:
            raise PbTableError(f'{a.address}: поле {a.number} изменилось')
    stats = {'seen': 0}
    _same_shape(before.rows, after.rows, checked, stats)
    if stats['seen'] != len(checked):
        raise PbTableError('Не все правки найдены в новом файле')
    reverse = {key: before.index[key].text for key in checked}
    if _apply(after, reverse) != before.data:
        raise PbTableError('Обратная подстановка исходных строк не даёт оригинал побайтно')
    changed = sum(1 for key, value in checked.items() if value != before.index[key].text)
    return {'edits': len(checked), 'changed': changed, 'bytes_before': len(before.data), 'bytes_after': len(after.data)}
