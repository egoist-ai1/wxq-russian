"""Частичная замена диапазонов в непрозрачном ресурсе равной длины.

Модуль работает только с переданными байтами и SHA-256. Формат ресурса
не определяется; сохранность байтов не подтверждает кодировку текстуры,
геометрию, отображение или смысл перевода и не разрешает установку.
"""
from __future__ import annotations

import hashlib


MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_PAYLOAD_BYTES = 32 * 1024 * 1024
MAX_RANGES = 4096
PATCH_SCHEMA = "wxq-native-range-patch/v1"
PROOF_SCHEMA = "wxq-native-range-patch-proof/v1"

_SPEC_KEYS = frozenset({"schema", "source_sha256", "payload_sha256",
                        "target_sha256", "ranges"})
_HEX_DIGITS = frozenset("0123456789abcdef")


def _pin(value: object, role: str) -> str:
    if (type(value) is not str or len(value) != 64
            or any(character not in _HEX_DIGITS for character in value)):
        raise ValueError(f"native-range-{role}-invalid-sha256")
    return value


def apply_range_patch(source: bytes, payload: bytes, spec: dict) -> tuple[bytes, dict]:
    """Заменяет half-open диапазоны последовательными частями payload.

    spec имеет ровно schema, source_sha256, payload_sha256, target_sha256,
    ranges. Каждый диапазон — обычный список [start, end] с целыми границами;
    диапазоны упорядочены, не пересекаются и оставляют хотя бы байт вне себя.
    Сумма их длин равна длине payload. Пустой ranges с пустым payload допустим
    как неизменённый результат с тем же target SHA.

    changed_byte_count считает фактические различия внутри диапазонов.
    unchanged_byte_count считает только сохранённые байты вне диапазонов,
    включая префикс, промежутки и суффикс. Входы не изменяются; receipt
    не содержит исходный текст, payload или координаты диапазонов.
    """
    if type(source) is not bytes:
        raise ValueError("native-range-source-must-be-plain-bytes")
    if type(payload) is not bytes:
        raise ValueError("native-range-payload-must-be-plain-bytes")
    source_bytes = len(source)
    payload_bytes = len(payload)
    if not 0 < source_bytes <= MAX_SOURCE_BYTES:
        raise ValueError("native-range-source-budget")
    if payload_bytes > MAX_PAYLOAD_BYTES:
        raise ValueError("native-range-payload-budget")
    if type(spec) is not dict:
        raise ValueError("native-range-spec-must-be-plain-dict")
    if (any(type(key) is not str for key in spec)
            or set(spec) != _SPEC_KEYS):
        raise ValueError("native-range-spec-keys")
    if type(spec["schema"]) is not str or spec["schema"] != PATCH_SCHEMA:
        raise ValueError("native-range-schema")
    source_pin = _pin(spec["source_sha256"], "source")
    payload_pin = _pin(spec["payload_sha256"], "payload")
    target_pin = _pin(spec["target_sha256"], "target")
    raw_ranges = spec["ranges"]
    if type(raw_ranges) is not list:
        raise ValueError("native-range-ranges-must-be-plain-list")
    if len(raw_ranges) > MAX_RANGES:
        raise ValueError("native-range-count-budget")

    ranges: list[tuple[int, int]] = []
    range_bytes = 0
    previous_end = 0
    for item in raw_ranges:
        if type(item) is not list or len(item) != 2:
            raise ValueError("native-range-item-must-be-two-element-plain-list")
        start, end = item
        if type(start) is not int or type(end) is not int:
            raise ValueError("native-range-boundaries-must-be-plain-int")
        if not 0 <= start < end <= source_bytes:
            raise ValueError("native-range-boundaries-out-of-bounds-or-empty")
        if start < previous_end:
            raise ValueError("native-range-unordered-or-overlapping")
        ranges.append((start, end))
        range_bytes += end - start
        previous_end = end
    if range_bytes >= source_bytes:
        raise ValueError("native-range-no-unchanged-outside-bytes")
    if range_bytes != payload_bytes:
        raise ValueError("native-range-payload-length-mismatch")

    source_sha256 = hashlib.sha256(source).hexdigest()
    if source_sha256 != source_pin:
        raise ValueError("native-range-source-sha256-mismatch")
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    if payload_sha256 != payload_pin:
        raise ValueError("native-range-payload-sha256-mismatch")

    output = bytearray(source)
    payload_position = 0
    changed_byte_count = 0
    for start, end in ranges:
        length = end - start
        replacement = payload[payload_position:payload_position + length]
        changed_byte_count += sum(before != after
                                  for before, after in zip(source[start:end], replacement))
        output[start:end] = replacement
        payload_position += length
    candidate = bytes(output)
    if len(candidate) != source_bytes:
        raise ValueError("native-range-fixed-length-verification-failed")

    # Независимо сверяем каждый участок вне заменённых диапазонов.
    outside_position = 0
    unchanged_byte_count = 0
    for start, end in ranges:
        if candidate[outside_position:start] != source[outside_position:start]:
            raise ValueError("native-range-outside-byte-verification-failed")
        unchanged_byte_count += start - outside_position
        outside_position = end
    if candidate[outside_position:] != source[outside_position:]:
        raise ValueError("native-range-outside-byte-verification-failed")
    unchanged_byte_count += source_bytes - outside_position

    target_sha256 = hashlib.sha256(candidate).hexdigest()
    if target_sha256 != target_pin:
        raise ValueError("native-range-target-sha256-mismatch")
    receipt = {
        "schema": PROOF_SCHEMA,
        "source_sha256": source_sha256,
        "source_bytes": source_bytes,
        "payload_sha256": payload_sha256,
        "payload_bytes": payload_bytes,
        "target_sha256": target_sha256,
        "target_bytes": len(candidate),
        "range_count": len(ranges),
        "range_bytes": range_bytes,
        "changed_byte_count": changed_byte_count,
        "unchanged_byte_count": unchanged_byte_count,
        "unchanged_bytes_byte_exact": True,
        "fixed_length_verified": True,
        "format_identification": "unknown",
        "source_text_exported": False,
        "native_texture_encoding_verified": False,
        "semantic_approval_created": False,
        "native_install_allowed": False,
        "new_translation_bindings": 0,
        "game_writes": 0,
    }
    return candidate, receipt
