"""Конечный lossless codec direct Global map: только замена text payload.

Wire-проверка не подтверждает загрузку клиентом или отображение интерфейса.
Смысл, числа и placeholders проверяет adapter по актуальному оригиналу.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from scripts.game_lang_codec import CodecError, WireField, _field, varint

MAX_MAP_BYTES = 2 * 1024 * 1024
MAX_MAP_ROWS = 32768
MAX_MAP_FIELDS = 100000
MAX_VALUE_BYTES = 16384
MAX_KEY_BYTES = 512


@dataclass
class _FieldBudget:
    used: int = 0

    def take(self, data: bytes, position: int) -> tuple[WireField, int]:
        # Общий лимит root и row проверяется до создания следующего объекта.
        if self.used >= MAX_MAP_FIELDS:
            raise CodecError("global-map-field-budget")
        field, end = _field(data, position)
        self.used += 1
        return field, end


def _decode(payload: bytes, maximum: int, role: str) -> str:
    if len(payload) > maximum:
        raise CodecError(f"global-map-{role}-budget")
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise CodecError(f"global-map-invalid-{role}-utf8") from error


def _encoded_value(value: str) -> bytes:
    if type(value) is not str:
        raise CodecError("global-map-replacement-must-be-string")
    # UTF-8 занимает не меньше байта на code point: не кодируем огромную строку.
    if len(value) > MAX_VALUE_BYTES:
        raise CodecError("global-map-value-budget")
    try:
        payload = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise CodecError("global-map-invalid-replacement-utf8") from error
    if len(payload) > MAX_VALUE_BYTES:
        raise CodecError("global-map-value-budget")
    return payload


@dataclass(frozen=True)
class GlobalMapRow:
    outer_index: int
    fields: tuple[WireField, ...]
    key_index: int
    text_index: int
    key: str
    text: str

    @property
    def unknown_fields(self) -> tuple[WireField, ...]:
        return tuple(field for index, field in enumerate(self.fields)
                     if index not in (self.key_index, self.text_index))


@dataclass(frozen=True)
class GlobalMapDocument:
    data: bytes
    fields: tuple[WireField, ...]
    rows: tuple[GlobalMapRow, ...]

    @classmethod
    def parse(cls, data: bytes) -> GlobalMapDocument:
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_MAP_BYTES:
            raise CodecError("global-map-document-budget")
        budget = _FieldBudget()
        outer: list[WireField] = []
        rows: list[GlobalMapRow] = []
        seen: set[str] = set()
        position = 0
        while position < len(data):
            field, position = budget.take(data, position)
            outer_index = len(outer)
            outer.append(field)
            if field.number != 1:
                continue
            if field.wire != 2:
                raise CodecError("global-map-root-row-wrong-wire")
            if len(rows) >= MAX_MAP_ROWS:
                raise CodecError("global-map-row-budget")
            row_fields: list[WireField] = []
            key_index = text_index = None
            inner_position = 0
            while inner_position < len(field.payload):
                inner, inner_position = budget.take(field.payload, inner_position)
                inner_index = len(row_fields)
                row_fields.append(inner)
                if inner.number not in (1, 2):
                    continue
                if inner.wire != 2:
                    raise CodecError("global-map-known-row-field-wrong-wire")
                if inner.number == 1:
                    if key_index is not None:
                        raise CodecError("global-map-duplicate-key-field")
                    key_index = inner_index
                else:
                    if text_index is not None:
                        raise CodecError("global-map-duplicate-text-field")
                    text_index = inner_index
            if key_index is None or text_index is None:
                raise CodecError("global-map-row-missing-key-or-text")
            key = _decode(row_fields[key_index].payload, MAX_KEY_BYTES, "key")
            text = _decode(row_fields[text_index].payload, MAX_VALUE_BYTES, "value")
            if not key:
                raise CodecError("global-map-empty-key")
            if key in seen:
                raise CodecError("global-map-duplicate-key")
            seen.add(key)
            rows.append(GlobalMapRow(outer_index, tuple(row_fields), key_index,
                                     text_index, key, text))
        if not rows:
            raise CodecError("global-map-no-text-rows")
        return cls(data, tuple(outer), tuple(rows))

    @property
    def unknown_fields(self) -> tuple[WireField, ...]:
        row_indices = {row.outer_index for row in self.rows}
        return tuple(field for index, field in enumerate(self.fields)
                     if index not in row_indices)

    def replace_text(self, translations: Mapping[str, str]) -> bytes:
        if not isinstance(translations, Mapping):
            raise CodecError("global-map-replacements-must-be-mapping")
        known = {row.key for row in self.rows}
        if set(translations) - known:
            raise CodecError("global-map-selected-key-missing")
        row_by_index = {row.outer_index: row for row in self.rows}
        output: list[bytes] = []
        output_size = 0
        changed = False
        for index, field in enumerate(self.fields):
            row = row_by_index.get(index)
            raw = field.raw
            if row is not None and row.key in translations:
                encoded = _encoded_value(translations[row.key])
                old_text = row.fields[row.text_index]
                if encoded != old_text.payload:
                    text_frame = old_text.replace_payload(encoded)
                    payload_size = len(field.payload) - len(old_text.raw) + len(text_frame)
                    length_size = (len(field.length) if payload_size == len(field.payload)
                                   else len(varint(payload_size)))
                    frame_size = len(field.tag) + length_size + payload_size
                    # Проверка до row join и до накопления следующего root frame.
                    if output_size + frame_size > MAX_MAP_BYTES:
                        raise CodecError("global-map-output-budget")
                    payload = b"".join(text_frame if inner_index == row.text_index else inner.raw
                                       for inner_index, inner in enumerate(row.fields))
                    raw = field.replace_payload(payload)
                    changed = True
            output_size += len(raw)
            if output_size > MAX_MAP_BYTES:
                raise CodecError("global-map-output-budget")
            output.append(raw)
        return b"".join(output) if changed else self.data


def _expected_frame(field: WireField, payload: bytes) -> bytes:
    """Ожидаемый frame выводится из исходных байтов без вызова writer."""
    if payload == field.payload:
        return field.raw
    length = field.length if len(payload) == len(field.payload) else varint(len(payload))
    return field.tag + length + payload


def prove_text_only(before: bytes, after: bytes,
                    selected: Mapping[str, str] | None = None) -> dict:
    """Проверяет text-only delta и controlled inverse исходных wire frames.

    selected=None допускает исторический baseline с разными text payload;
    структурная проверка не является semantic approval этих строк.
    """
    source = GlobalMapDocument.parse(before)
    result = GlobalMapDocument.parse(after)
    if len(source.fields) != len(result.fields) or len(source.rows) != len(result.rows):
        raise CodecError("global-map-field-or-row-count-drift")
    identities = lambda document: [(row.outer_index, row.key, row.key_index, row.text_index)
                                   for row in document.rows]
    if identities(source) != identities(result):
        raise CodecError("global-map-key-order-or-position-drift")
    if selected is not None:
        if not isinstance(selected, Mapping) or set(selected) - {row.key for row in source.rows}:
            raise CodecError("global-map-selected-key-missing")
        for value in selected.values():
            _encoded_value(value)
    row_indices = {row.outer_index for row in source.rows}
    restored = [field.raw for field in result.fields]
    for index, (old, new) in enumerate(zip(source.fields, result.fields)):
        if index not in row_indices and old.raw != new.raw:
            raise CodecError("global-map-unknown-outer-field-drift")
    changed = 0
    for old, new in zip(source.rows, result.rows):
        if len(old.fields) != len(new.fields):
            raise CodecError("global-map-row-field-count-drift")
        expected_text = new.text if selected is None else selected.get(old.key, old.text)
        if new.text != expected_text:
            raise CodecError("global-map-unselected-or-target-text-drift")
        for index, (old_field, new_field) in enumerate(zip(old.fields, new.fields)):
            if index != old.text_index and old_field.raw != new_field.raw:
                raise CodecError("global-map-key-or-unknown-row-field-drift")
        encoded = new.text.encode("utf-8", errors="strict")
        payload = b"".join(_expected_frame(field, encoded) if index == old.text_index else field.raw
                           for index, field in enumerate(old.fields))
        old_outer = source.fields[old.outer_index]
        if result.fields[new.outer_index].raw != _expected_frame(old_outer, payload):
            raise CodecError("global-map-text-or-row-framing-drift")
        # Берём неприкосновенные поля из after, старый text/tag/length из before.
        inverse_payload = b"".join(old.fields[index].raw if index == old.text_index else field.raw
                                   for index, field in enumerate(new.fields))
        restored[new.outer_index] = old_outer.tag + old_outer.length + inverse_payload
        changed += old.text != new.text
    if b"".join(restored) != before:
        raise CodecError("global-map-controlled-inverse-failed")
    if source.replace_text({row.key: row.text for row in source.rows}) != before:
        raise CodecError("global-map-source-noop-failed")
    return {"rows": len(source.rows), "changed_text_rows": changed,
            "source_noop_byte_exact": True, "controlled_inverse_byte_exact": True,
            "unknown_fields_byte_exact": True, "key_order_byte_exact": True,
            "unknown_outer_fields": len(source.unknown_fields),
            "unknown_row_fields": sum(len(row.unknown_fields) for row in source.rows)}
