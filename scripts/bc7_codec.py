"""BC7: собственный энкодер (режимы 6, 5, 4 — один набор цветов на блок) и декодер тех же режимов.

Нужен для русификации атласов 1024x1024 BC7 (пакет 19). Принципы:
- поток и заголовок текстуры не меняются: размер потока = (W/4)*(H/4)*16 байт;
- перекодируются только блоки 4x4, которые попросил вызывающий (пересечение с маской правки); остальные
  байты потока остаются побайтно исходными — `reencode_stream` это проверяет и возвращает доказательство;
- энкодер выбирает для блока лучший из режимов 6 (RGBA 7+1 бит, 4-битные индексы), 5 (RGB 7 бит и отдельная
  альфа 8 бит, повороты каналов, 2-битные индексы) и 4 (RGB 5 бит, альфа 6 бит, повороты, 2+3-битные индексы)
  по сумме квадратов ошибки и возвращает ошибку блока.
Декодер здесь покрывает только режимы 4-6 (нужны для самопроверки); независимое чтение всего потока — Pillow
(`pillow_decode`), который реализует все 8 режимов отдельным кодом на C.
Ничего не пишет на диск и не читает игру.
"""
from __future__ import annotations

import numpy as np

BLOCK_BYTES = 16

WEIGHTS2 = (0, 21, 43, 64)
WEIGHTS3 = (0, 9, 18, 27, 37, 46, 55, 64)
WEIGHTS4 = (0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64)
WEIGHTS = {2: np.array(WEIGHTS2, dtype=np.int64), 3: np.array(WEIGHTS3, dtype=np.int64), 4: np.array(WEIGHTS4, dtype=np.int64)}


class Bc7Error(ValueError):
    pass


# ---------------------------------------------------------------- биты

class _BitWriter:
    def __init__(self) -> None:
        self.value = 0
        self.position = 0

    def put(self, value: int, bits: int) -> None:
        if value < 0 or value >> bits:
            raise Bc7Error(f'Значение {value} не помещается в {bits} бит')
        self.value |= value << self.position
        self.position += bits

    def finish(self) -> bytes:
        if self.position != 128:
            raise Bc7Error(f'Блок BC7 содержит {self.position} бит, нужно 128')
        return self.value.to_bytes(16, 'little')


def _bits(block: int, position: int, count: int) -> int:
    return (block >> position) & ((1 << count) - 1)


def _expand(value: int, bits: int) -> int:
    """Расширение квантованного канала до 8 бит (как в спецификации)."""
    value <<= 8 - bits
    return value | (value >> bits)


# ---------------------------------------------------------------- декодер режимов 4-6

def decode_block(raw: bytes) -> np.ndarray | None:
    """Блок 4x4 RGBA (uint8, форма (4,4,4)) для режимов 4-6; None для остальных режимов."""
    block = int.from_bytes(raw, 'little')
    mode = (block & -block).bit_length() - 1 if block else 8
    if mode == 6:
        # порядок полей: R0 R1 G0 G1 B0 B1 A0 A1 (по 7 бит), затем p-биты концов 0 и 1, затем индексы
        r0, r1, g0, g1, b0, b1, a0, a1 = _read_pairs(block, 7, 7, 4)
        p0, p1 = _bits(block, 7 + 56, 1), _bits(block, 7 + 57, 1)
        c0 = [(v << 1) | p0 for v in (r0, g0, b0, a0)]
        c1 = [(v << 1) | p1 for v in (r1, g1, b1, a1)]
        indices = _read_indices(block, 7 + 58, 4, anchors=(0,))
        pix = _interpolate([_expand(v, 8) for v in c0], [_expand(v, 8) for v in c1], indices, WEIGHTS[4])
        return pix.reshape(4, 4, 4).astype(np.uint8)
    if mode == 5:
        rotation = _bits(block, 6, 2)
        r0, r1, g0, g1, b0, b1 = _read_values(block, 8, 6, 7)
        a0, a1 = _bits(block, 8 + 42, 8), _bits(block, 8 + 50, 8)
        position = 8 + 58
        color_idx = _read_indices(block, position, 2, anchors=(0,))
        position += 31
        alpha_idx = _read_indices(block, position, 2, anchors=(0,))
        c0 = [_expand(v, 7) for v in (r0, g0, b0)]
        c1 = [_expand(v, 7) for v in (r1, g1, b1)]
        rgb = _interpolate(c0 + [0], c1 + [0], color_idx, WEIGHTS[2])[:, :3]
        alpha = _interpolate([0, 0, 0, a0], [0, 0, 0, a1], alpha_idx, WEIGHTS[2])[:, 3]
        return _rotate_back(rgb, alpha, rotation)
    if mode == 4:
        rotation = _bits(block, 5, 2)
        index_mode = _bits(block, 7, 1)
        r0, r1, g0, g1, b0, b1 = _read_values(block, 8, 6, 5)
        a0, a1 = _bits(block, 8 + 30, 6), _bits(block, 8 + 36, 6)
        position = 8 + 42
        first = _read_indices(block, position, 2, anchors=(0,))
        position += 31
        second = _read_indices(block, position, 3, anchors=(0,))
        color_idx, alpha_idx = (second, first) if index_mode else (first, second)
        color_w = WEIGHTS[3] if index_mode else WEIGHTS[2]
        alpha_w = WEIGHTS[2] if index_mode else WEIGHTS[3]
        c0 = [_expand(v, 5) for v in (r0, g0, b0)]
        c1 = [_expand(v, 5) for v in (r1, g1, b1)]
        rgb = _interpolate(c0 + [0], c1 + [0], color_idx, color_w)[:, :3]
        alpha = _interpolate([0, 0, 0, _expand(a0, 6)], [0, 0, 0, _expand(a1, 6)], alpha_idx, alpha_w)[:, 3]
        return _rotate_back(rgb, alpha, rotation)
    return None


def block_mode(raw: bytes) -> int:
    block = int.from_bytes(raw, 'little')
    return (block & -block).bit_length() - 1 if block else 8


def _read_pairs(block: int, position: int, bits: int, channels: int):
    values = []
    for _ in range(channels * 2):
        values.append(_bits(block, position, bits))
        position += bits
    return values


def _read_values(block: int, position: int, count: int, bits: int):
    values = []
    for _ in range(count):
        values.append(_bits(block, position, bits))
        position += bits
    return values


def _read_indices(block: int, position: int, bits: int, anchors: tuple[int, ...]) -> np.ndarray:
    result = []
    for pixel in range(16):
        width = bits - 1 if pixel in anchors else bits
        result.append(_bits(block, position, width))
        position += width
    return np.array(result, dtype=np.int64)


def _interpolate(c0, c1, indices: np.ndarray, weights: np.ndarray) -> np.ndarray:
    w = weights[indices][:, None]
    a, b = np.array(c0, dtype=np.int64)[None, :], np.array(c1, dtype=np.int64)[None, :]
    return (a * (64 - w) + b * w + 32) >> 6


def _rotate_back(rgb: np.ndarray, alpha: np.ndarray, rotation: int) -> np.ndarray:
    pix = np.concatenate([rgb, alpha[:, None]], axis=1).astype(np.int64)
    if rotation:
        channel = rotation - 1
        pix[:, [channel, 3]] = pix[:, [3, channel]]
    return pix.reshape(4, 4, 4).astype(np.uint8)


# ---------------------------------------------------------------- энкодер

def _fit_line(points: np.ndarray, weights: np.ndarray):
    """Два конца (float), аппроксимирующие облако точек линией по главной оси; индексы по проекции."""
    mean = points.mean(axis=0)
    centered = points - mean
    if not np.any(centered):
        return mean.copy(), mean.copy()
    # главная ось через степенной метод на ковариации (достаточно 6 итераций для 4 каналов)
    cov = centered.T @ centered
    axis = np.ones(points.shape[1]) if not np.any(cov) else cov @ np.ones(points.shape[1])
    for _ in range(8):
        axis = cov @ axis
        norm = np.linalg.norm(axis)
        if norm == 0:
            break
        axis /= norm
    t = centered @ axis
    return mean + axis * t.min(), mean + axis * t.max()


def _quantize_endpoints(e0: np.ndarray, e1: np.ndarray, bits: int, pbit: bool):
    """Варианты квантования двух концов (float, 0..255) в целые значения полей и p-бит; возвращает список кандидатов
    (v0, v1, p0, p1, d0, d1) где d — развёрнутые до 8 бит значения."""
    options = []
    for p0, p1 in ((0, 0), (0, 1), (1, 0), (1, 1)) if pbit else ((None, None),):
        decoded = []
        values = []
        for e, p in ((e0, p0), (e1, p1)):
            full = bits + (1 if pbit else 0)
            scale = (1 << full) - 1
            target = np.clip(e / 255.0 * scale, 0, scale)
            if pbit:
                # поле v (bits бит) и p: значение = v*2+p; подбираем ближайшее с нужной чётностью
                v = np.clip(np.round((target - p) / 2.0), 0, (1 << bits) - 1).astype(np.int64)
                q = v * 2 + p
            else:
                v = np.clip(np.round(target), 0, scale).astype(np.int64)
                q = v
            expanded = (q << (8 - full)) | (q >> (2 * full - 8)) if full <= 8 else q
            decoded.append(expanded.astype(np.int64))
            values.append(v)
        options.append((values[0], values[1], p0, p1, decoded[0], decoded[1]))
        if not pbit:
            break
    return options


def _assign(pixels: np.ndarray, d0: np.ndarray, d1: np.ndarray, weights: np.ndarray, channels: slice):
    """Лучший индекс на пиксель при заданных (развёрнутых) концах: возвращает индексы и ошибку по пикселям."""
    w = weights[:, None]
    palette = (d0[None, channels] * (64 - w) + d1[None, channels] * w + 32) >> 6   # (L, C)
    diff = pixels[:, None, channels] - palette[None, :, :]                              # (16, L, C)
    err = (diff * diff).sum(axis=2)                                                     # (16, L)
    indices = err.argmin(axis=1)
    return indices, err[np.arange(16), indices], palette


def _refine(pixels: np.ndarray, indices: np.ndarray, weights: np.ndarray):
    """Наименьшие квадраты: оптимальные концы (float) при фиксированных индексах."""
    w = weights[indices] / 64.0
    a = 1 - w
    aa, bb, ab = (a * a).sum(), (w * w).sum(), (a * w).sum()
    det = aa * bb - ab * ab
    if abs(det) < 1e-9:
        return None
    ax = (pixels * a[:, None]).sum(axis=0)
    bx = (pixels * w[:, None]).sum(axis=0)
    e0 = (bb * ax - ab * bx) / det
    e1 = (aa * bx - ab * ax) / det
    return e0, e1


def _search_pair(pixels: np.ndarray, weights: np.ndarray, bits: int, pbit: bool, channels: slice, rounds: int = 4):
    """Лучшая пара концов для одной группы каналов; возвращает (ошибка, v0, v1, p0, p1, d0, d1, индексы)."""
    sub = pixels[:, channels]
    levels = len(weights)
    start0, start1 = _fit_line(sub.astype(np.float64), weights)
    best = None
    seen = set()
    full_dims = pixels.shape[1]

    def expand(e):
        out = np.zeros(full_dims)
        out[channels] = e
        return out

    candidates = [(start0, start1)]
    for _ in range(rounds):
        added = False
        for e0, e1 in list(candidates):
            for option in _quantize_endpoints(expand(e0), expand(e1), bits, pbit):
                v0, v1, p0, p1, d0, d1 = option
                key = (tuple(v0[channels]), tuple(v1[channels]), p0, p1)
                if key in seen:
                    continue
                seen.add(key)
                indices, per_pixel, _ = _assign(pixels, d0, d1, weights, channels)
                total = int(per_pixel.sum())
                if best is None or total < best[0]:
                    best = (total, v0, v1, p0, p1, d0, d1, indices)
                refined = _refine(sub.astype(np.float64), indices, weights)
                if refined is not None and not np.allclose(refined[0], e0) and not np.allclose(refined[1], e1):
                    candidates.append(refined)
                    added = True
        candidates = candidates[-4:] if added else []
        if not candidates:
            break
    return best


def _pack_mode6(best, pixels: np.ndarray):
    err, v0, v1, p0, p1, d0, d1, indices = best
    indices, v0, v1, p0, p1 = _anchor_swap(indices, v0, v1, p0, p1, 16)
    writer = _BitWriter()
    writer.put(1 << 6, 7)
    for channel in range(4):
        writer.put(int(v0[channel]), 7)
        writer.put(int(v1[channel]), 7)
    writer.put(int(p0), 1)
    writer.put(int(p1), 1)
    for pixel in range(16):
        writer.put(int(indices[pixel]), 3 if pixel == 0 else 4)
    return writer.finish()


def _anchor_swap(indices, v0, v1, p0, p1, levels):
    if indices[0] >= levels // 2:
        return levels - 1 - indices, v1, v0, p1, p0
    return indices, v0, v1, p0, p1


def _mode6(pixels: np.ndarray):
    best = _search_pair(pixels, WEIGHTS[4], 7, True, slice(0, 4))
    block = _pack_mode6(best, pixels)
    return best[0], block


def _mode5(pixels: np.ndarray, rotation: int):
    swapped = pixels.copy()
    if rotation:
        swapped[:, [rotation - 1, 3]] = swapped[:, [3, rotation - 1]]
    color = _search_pair(swapped, WEIGHTS[2], 7, False, slice(0, 3))
    alpha = _search_pair_scalar(swapped[:, 3], WEIGHTS[2], 8)
    total = color[0] + alpha[0]
    c_idx, cv0, cv1 = color[7], color[1], color[2]
    c_idx, cv0, cv1, _, _ = _anchor_swap(c_idx, cv0, cv1, 0, 0, 4)
    a_idx, av0, av1 = alpha[3], alpha[1], alpha[2]
    a_idx, av0, av1, _, _ = _anchor_swap(a_idx, av0, av1, 0, 0, 4)
    writer = _BitWriter()
    writer.put(1 << 5, 6)
    writer.put(rotation, 2)
    for channel in range(3):
        writer.put(int(cv0[channel]), 7)
        writer.put(int(cv1[channel]), 7)
    writer.put(int(av0), 8)
    writer.put(int(av1), 8)
    for pixel in range(16):
        writer.put(int(c_idx[pixel]), 1 if pixel == 0 else 2)
    for pixel in range(16):
        writer.put(int(a_idx[pixel]), 1 if pixel == 0 else 2)
    return total, writer.finish()


def _search_pair_scalar(values: np.ndarray, weights: np.ndarray, bits: int):
    """Альфа-канал одним числом: возвращает (ошибка, v0, v1, индексы)."""
    pixels = np.stack([values, np.zeros(16), np.zeros(16), np.zeros(16)], axis=1).astype(np.int64)
    best = None
    for lo_hi in _scalar_candidates(values):
        d0 = np.array([lo_hi[0], 0, 0, 0], dtype=np.int64)
        d1 = np.array([lo_hi[1], 0, 0, 0], dtype=np.int64)
        indices, per_pixel, _ = _assign(pixels, d0, d1, weights, slice(0, 1))
        total = int(per_pixel.sum())
        if best is None or total < best[0]:
            best = (total, lo_hi[0], lo_hi[1], indices)
    # уточнение методом наименьших квадратов
    for _ in range(3):
        refined = _refine(values.astype(np.float64)[:, None], best[3], weights)
        if refined is None:
            break
        lo = int(np.clip(round(float(refined[0][0])), 0, 255))
        hi = int(np.clip(round(float(refined[1][0])), 0, 255))
        d0 = np.array([lo, 0, 0, 0], dtype=np.int64)
        d1 = np.array([hi, 0, 0, 0], dtype=np.int64)
        indices, per_pixel, _ = _assign(pixels, d0, d1, weights, slice(0, 1))
        total = int(per_pixel.sum())
        if total < best[0]:
            best = (total, lo, hi, indices)
        else:
            break
    return best


def _scalar_candidates(values: np.ndarray):
    lo, hi = int(values.min()), int(values.max())
    return {(lo, hi), (hi, lo)}


def _mode4(pixels: np.ndarray, rotation: int, index_mode: int):
    """Режим 4: RGB 5 бит, альфа 6 бит; индексы 2 бита у одного канала и 3 у другого (index_mode выбирает)."""
    swapped = pixels.copy()
    if rotation:
        swapped[:, [rotation - 1, 3]] = swapped[:, [3, rotation - 1]]
    color_weights = WEIGHTS[3] if index_mode else WEIGHTS[2]
    alpha_weights = WEIGHTS[2] if index_mode else WEIGHTS[3]
    color = _search_pair(swapped, color_weights, 5, False, slice(0, 3))
    # альфа: 6-битные концы, развёртка (v<<2)|(v>>4)
    best = None
    values = swapped[:, 3]
    candidates = {(int(values.min()) >> 2, int(values.max()) >> 2), (int(values.max()) >> 2, int(values.min()) >> 2)}
    for lo6, hi6 in list(candidates):
        for dl in (-1, 0, 1):
            for dh in (-1, 0, 1):
                candidates.add((max(0, min(63, lo6 + dl)), max(0, min(63, hi6 + dh))))
    for lo6, hi6 in candidates:
        d0 = np.array([(lo6 << 2) | (lo6 >> 4), 0, 0, 0], dtype=np.int64)
        d1 = np.array([(hi6 << 2) | (hi6 >> 4), 0, 0, 0], dtype=np.int64)
        pix = np.stack([values, np.zeros(16), np.zeros(16), np.zeros(16)], axis=1).astype(np.int64)
        indices, per_pixel, _ = _assign(pix, d0, d1, alpha_weights, slice(0, 1))
        total = int(per_pixel.sum())
        if best is None or total < best[0]:
            best = (total, lo6, hi6, indices)
    total = color[0] + best[0]
    c_idx, cv0, cv1, _, _ = _anchor_swap(color[7], color[1], color[2], 0, 0, len(color_weights))
    a_idx, av0, av1, _, _ = _anchor_swap(best[3], best[1], best[2], 0, 0, len(alpha_weights))
    first, second = (a_idx, c_idx) if index_mode else (c_idx, a_idx)
    first_bits, second_bits = 2, 3
    writer = _BitWriter()
    writer.put(1 << 4, 5)
    writer.put(rotation, 2)
    writer.put(index_mode, 1)
    for channel in range(3):
        writer.put(int(cv0[channel]), 5)
        writer.put(int(cv1[channel]), 5)
    writer.put(int(av0), 6)
    writer.put(int(av1), 6)
    for pixel in range(16):
        writer.put(int(first[pixel]), first_bits - (1 if pixel == 0 else 0))
    for pixel in range(16):
        writer.put(int(second[pixel]), second_bits - (1 if pixel == 0 else 0))
    return total, writer.finish()


def encode_block(pixels: np.ndarray, modes: tuple[int, ...] = (6, 5, 4)) -> tuple[bytes, int, int]:
    """Лучший блок BC7 для 16 пикселей RGBA (форма (16,4)); возвращает (16 байт, режим, сумма квадратов ошибки).

    Ошибка считается по результату декодирования (развёрнутые концы и веса), а не по идеальным значениям.
    """
    pixels = np.asarray(pixels, dtype=np.int64).reshape(16, 4)
    best = None
    for mode in modes:
        if mode == 6:
            total, block = _mode6(pixels)
        elif mode == 5:
            total, block = min((_mode5(pixels, r) for r in (0, 1, 2, 3)), key=lambda t: t[0])
        elif mode == 4:
            total, block = min((_mode4(pixels, r, i) for r in (0, 1, 2, 3) for i in (0, 1)), key=lambda t: t[0])
        else:
            raise Bc7Error(f'Режим {mode} энкодером не поддерживается')
        if best is None or total < best[0]:
            best = (total, block, mode)
        if total == 0:
            break
    return best[1], best[2], best[0]


# ---------------------------------------------------------------- поток

def blocks_wide(width: int) -> int:
    if width % 4:
        raise Bc7Error('Ширина текстуры BC7 должна быть кратна 4')
    return width // 4


def stream_size(width: int, height: int) -> int:
    return blocks_wide(width) * blocks_wide(height) * BLOCK_BYTES


def pillow_decode(stream: bytes, width: int, height: int) -> np.ndarray:
    """Независимое чтение потока Pillow (C-декодер BCn): массив (H,W,4) uint8 в порядке строк потока."""
    from PIL import Image
    if len(stream) != stream_size(width, height):
        raise Bc7Error('Размер потока не равен размеру BC7 текстуры без mip')
    image = Image.frombuffer('RGBA', (width, height), stream, 'bcn', (7,))
    return np.asarray(image.convert('RGBA'), dtype=np.uint8).copy()


def block_slice(bx: int, by: int, width: int) -> slice:
    start = (by * blocks_wide(width) + bx) * BLOCK_BYTES
    return slice(start, start + BLOCK_BYTES)


def blocks_touching(mask_boxes: list[list[int]], width: int, height: int) -> list[tuple[int, int]]:
    """Блоки 4x4 (bx, by), пересекающиеся хотя бы с одним прямоугольником [x0,y0,x1,y1) (порядок строк потока)."""
    touched = set()
    for x0, y0, x1, y1 in mask_boxes:
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            raise Bc7Error(f'Маска {x0, y0, x1, y1} вне текстуры {width}x{height}')
        for by in range(y0 // 4, (y1 - 1) // 4 + 1):
            for bx in range(x0 // 4, (x1 - 1) // 4 + 1):
                touched.add((bx, by))
    return sorted(touched, key=lambda b: (b[1], b[0]))


def reencode_stream(stream: bytes, target: np.ndarray, blocks: list[tuple[int, int]], width: int, height: int,
                    modes: tuple[int, ...] = (6, 5, 4)) -> tuple[bytes, dict]:
    """Копия потока, где перекодированы только перечисленные блоки по картинке-цели `target` (H,W,4 uint8).

    Возвращает (новый поток, отчёт): число блоков, режимы, PSNR по заменённым пикселям относительно `target`
    и доказательство, что все остальные блоки совпали с исходными побайтно.
    """
    if len(stream) != stream_size(width, height) or target.shape != (height, width, 4):
        raise Bc7Error('Поток или картинка-цель не соответствуют размеру текстуры')
    out = bytearray(stream)
    chosen = {}
    sq_error = 0
    for bx, by in blocks:
        patch = target[by * 4:by * 4 + 4, bx * 4:bx * 4 + 4].reshape(16, 4)
        block, mode, _ = encode_block(patch, modes)
        out[block_slice(bx, by, width)] = block
        chosen[mode] = chosen.get(mode, 0) + 1
    result = bytes(out)
    touched = {by * blocks_wide(width) + bx for bx, by in blocks}
    outside = all(result[i * BLOCK_BYTES:(i + 1) * BLOCK_BYTES] == stream[i * BLOCK_BYTES:(i + 1) * BLOCK_BYTES]
                  for i in range(blocks_wide(width) * blocks_wide(height)) if i not in touched)
    if len(result) != len(stream) or not outside:
        raise Bc7Error('Блоки вне набора изменились или размер потока не сохранён')
    decoded = pillow_decode(result, width, height)
    for bx, by in blocks:
        got = decoded[by * 4:by * 4 + 4, bx * 4:bx * 4 + 4].astype(np.int64)
        want = target[by * 4:by * 4 + 4, bx * 4:bx * 4 + 4].astype(np.int64)
        sq_error += int(((got - want) ** 2).sum())
    count = len(blocks) * 16 * 4
    mse = sq_error / count if count else 0.0
    psnr = 99.0 if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)
    report = {'blocks': len(blocks), 'modes': chosen, 'psnr_db': round(float(psnr), 2), 'mse': round(mse, 3),
              'blocks_outside_set_byte_exact': outside, 'stream_bytes': len(result)}
    return result, report
