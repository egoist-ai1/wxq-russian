"""Чистый адаптер масок пикселей к замене диапазонов raw RGBA8.

Оба входа — целые плоскости одного mip в порядке source-row-zero, без
заголовка, padding и хвоста: байт канала c пикселя (x, y) имеет индекс
((y * width + x) * 4 + c). Строки не переворачиваются и не транспонируются.
Размер стороны ограничен 8192, плоскости — 32 MiB, прямоугольники — 256,
итоговые канонические диапазоны — 4096. Хотя бы один целый RGBA-пиксель
остаётся вне объединения масок; каждый его канал сохраняется точно.

Геометрия переданных байтов не доказывает native-происхождение, кодировку
или метаданные текстуры, рецензию масок и смысл перевода. Модуль не читает
и не пишет файлы, не генерирует изображение и не разрешает установку.
"""
from __future__ import annotations

import hashlib

from localization.native_range_patch import apply_range_patch


MAX_DIMENSION = 8192
MAX_PLANE_BYTES = 32 * 1024 * 1024
MAX_MASKS = 256
MAX_RANGES = 4096
PATCH_SCHEMA = "wxq-rgba32-pixel-patch/v1"
PROOF_SCHEMA = "wxq-rgba32-pixel-patch-proof/v1"
ROW_ORIGIN = "source-row-zero"

_SPEC_KEYS = frozenset({"schema", "width", "height", "mip_count", "row_origin",
                        "source_sha256", "candidate_sha256", "text_masks"})
_HEX_DIGITS = frozenset("0123456789abcdef")


def _pin(value: object, role: str) -> str:
    if (type(value) is not str or len(value) != 64
            or any(character not in _HEX_DIGITS for character in value)):
        raise ValueError(f"rgba32-{role}-invalid-sha256")
    return value


def _canonical_ranges(width: int, height: int,
                      rectangles: list[tuple[int, int, int, int]]) -> tuple[list, int]:
    ranges: list[list[int]] = []
    union_pixels = 0
    for y in range(height):
        intervals = sorted((x0, x1) for x0, y0, x1, y1 in rectangles
                           if y0 <= y < y1)
        row_spans: list[list[int]] = []
        for x0, x1 in intervals:
            if row_spans and x0 <= row_spans[-1][1]:
                row_spans[-1][1] = max(row_spans[-1][1], x1)
            else:
                row_spans.append([x0, x1])
        for x0, x1 in row_spans:
            union_pixels += x1 - x0
            start = (y * width + x0) * 4
            end = (y * width + x1) * 4
            if ranges and start == ranges[-1][1]:
                ranges[-1][1] = end
            else:
                ranges.append([start, end])
                # Порядок байтов возрастает: будущая строка уже не закроет
                # прежний разрыв. Лимит относится к объединённым диапазонам.
                if len(ranges) > MAX_RANGES:
                    raise ValueError("rgba32-range-count-budget")
    return ranges, union_pixels


def build_rgba32_patch(source: bytes, candidate: bytes, spec: dict) -> tuple[bytes, dict]:
    """Проверяет candidate и воспроизводит его через apply_range_patch.

    spec содержит ровно schema, width, height, mip_count, row_origin,
    source_sha256, candidate_sha256, text_masks. Маска — обычный список
    [x0, y0, x1, y1] с half-open границами в координатах исходных строк.
    Пересечения и касания разрешены, полностью покрытая плоскость — нет.
    Пустые маски допустимы только для идентичных плоскостей.

    unchanged_pixels считает пиксели вне union, а changed_pixel_count —
    фактические изменения любого RGBA-канала внутри него. Входы неизменны;
    receipt содержит только SHA, размеры, счётчики и узкие факты о байтах.
    """
    if type(source) is not bytes:
        raise ValueError("rgba32-source-must-be-plain-bytes")
    if type(candidate) is not bytes:
        raise ValueError("rgba32-candidate-must-be-plain-bytes")
    if type(spec) is not dict:
        raise ValueError("rgba32-spec-must-be-plain-dict")
    if (any(type(key) is not str for key in spec)
            or set(spec) != _SPEC_KEYS):
        raise ValueError("rgba32-spec-keys")
    if type(spec["schema"]) is not str or spec["schema"] != PATCH_SCHEMA:
        raise ValueError("rgba32-schema")
    width, height = spec["width"], spec["height"]
    for role, value in (("width", width), ("height", height)):
        if type(value) is not int or not 1 <= value <= MAX_DIMENSION:
            raise ValueError(f"rgba32-{role}-must-be-plain-int-in-1-8192")
    plane_bytes = width * height * 4
    if plane_bytes > MAX_PLANE_BYTES:
        raise ValueError("rgba32-plane-byte-budget")
    if type(spec["mip_count"]) is not int or spec["mip_count"] != 1:
        raise ValueError("rgba32-single-mip-required")
    if type(spec["row_origin"]) is not str or spec["row_origin"] != ROW_ORIGIN:
        raise ValueError("rgba32-source-row-zero-required")
    if len(source) != plane_bytes:
        raise ValueError("rgba32-source-plane-length-mismatch")
    if len(candidate) != plane_bytes:
        raise ValueError("rgba32-candidate-plane-length-mismatch")
    source_pin = _pin(spec["source_sha256"], "source")
    candidate_pin = _pin(spec["candidate_sha256"], "candidate")
    masks = spec["text_masks"]
    if type(masks) is not list:
        raise ValueError("rgba32-text-masks-must-be-plain-list")
    if len(masks) > MAX_MASKS:
        raise ValueError("rgba32-mask-count-budget")

    rectangles: list[tuple[int, int, int, int]] = []
    for mask in masks:
        if type(mask) is not list or len(mask) != 4:
            raise ValueError("rgba32-mask-must-be-four-element-plain-list")
        if any(type(value) is not int for value in mask):
            raise ValueError("rgba32-mask-coordinates-must-be-plain-int")
        x0, y0, x1, y1 = mask
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            raise ValueError("rgba32-mask-out-of-bounds-or-empty")
        rectangles.append((x0, y0, x1, y1))

    # Лимит финальной union проверяется до SHA, проверки изменений и payload.
    ranges, union_pixels = _canonical_ranges(width, height, rectangles)
    total_pixels = width * height
    if union_pixels == total_pixels:
        raise ValueError("rgba32-no-unmasked-pixel")

    source_sha256 = hashlib.sha256(source).hexdigest()
    if source_sha256 != source_pin:
        raise ValueError("rgba32-source-sha256-mismatch")
    candidate_sha256 = hashlib.sha256(candidate).hexdigest()
    if candidate_sha256 != candidate_pin:
        raise ValueError("rgba32-candidate-sha256-mismatch")
    if not masks and candidate != source:
        raise ValueError("rgba32-empty-masks-require-identical-planes")

    outside_position = 0
    for start, end in ranges:
        if source[outside_position:start] != candidate[outside_position:start]:
            raise ValueError("rgba32-outside-mask-rgba-changed")
        outside_position = end
    if source[outside_position:] != candidate[outside_position:]:
        raise ValueError("rgba32-outside-mask-rgba-changed")

    changed_byte_count = 0
    changed_pixel_count = 0
    for start, end in ranges:
        changed_byte_count += sum(before != after for before, after
                                  in zip(source[start:end], candidate[start:end]))
        changed_pixel_count += sum(source[index:index + 4] != candidate[index:index + 4]
                                   for index in range(start, end, 4))
    payload = b"".join(candidate[start:end] for start, end in ranges)
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    patched, _ = apply_range_patch(source, payload, {
        "schema": "wxq-native-range-patch/v1",
        "source_sha256": source_sha256,
        "payload_sha256": payload_sha256,
        "target_sha256": candidate_sha256,
        "ranges": ranges,
    })
    if type(patched) is not bytes or patched != candidate:
        raise ValueError("rgba32-candidate-equality-verification-failed")

    receipt = {
        "schema": PROOF_SCHEMA,
        "format": "RGBA32-raw-single-mip",
        "source_sha256": source_sha256,
        "source_bytes": len(source),
        "candidate_sha256": candidate_sha256,
        "candidate_bytes": len(candidate),
        "payload_sha256": payload_sha256,
        "payload_bytes": len(payload),
        "width": width,
        "height": height,
        "mip_count": 1,
        "row_origin": ROW_ORIGIN,
        "bytes_per_pixel": 4,
        "row_stride_bytes": width * 4,
        "header_bytes": 0,
        "row_padding_bytes": 0,
        "tail_bytes": 0,
        "mask_count": len(masks),
        "union_mask_pixels": union_pixels,
        "unchanged_pixels": total_pixels - union_pixels,
        "range_count": len(ranges),
        "changed_pixel_count": changed_pixel_count,
        "changed_byte_count": changed_byte_count,
        "rgba_pixel_to_byte_mapping_verified": True,
        "outside_mask_rgba_byte_exact": True,
        "fixed_length_verified": True,
        "candidate_byte_exact": True,
        "caller_profile_is_native_provenance": False,
        "native_texture_encoding_verified": False,
        "native_metadata_verified": False,
        "mask_review_verified": False,
        "semantic_approval_created": False,
        "native_install_allowed": False,
        "new_translation_bindings": 0,
        "game_writes": 0,
    }
    return patched, receipt
