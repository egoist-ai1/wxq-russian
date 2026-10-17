"""Русская надпись кнопки StartGame (текстура 863114A42F12EF76, RGBA32 372x102) для пилота.

Остальные байты текстуры не меняются: кандидат собирается как копия оригинала, в которой заменена
прямоугольная область надписи, и проходит `build_rgba32_patch` (маска + native range patch).
Шрифт — OSGameFont_Title игры (пакет 2), растеризация PIL; стиль (вертикальный градиент заливки,
мягкий тёмный край альфы, отсутствие обводки) снят с самого оригинала. Если PIL, шрифт или глифы
недоступны, поднимается `ButtonBlocker` с описанием причины. Ничего не пишет в игру.
"""
from __future__ import annotations

import hashlib
import io
import struct
import sys
from pathlib import Path

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from localization.rgba32_pixel_patch import PATCH_SCHEMA, ROW_ORIGIN, build_rgba32_patch  # noqa: E402

TEXTURE_ID = '863114a42f12ef76'
TEXTURE_PACKAGE = '19'
FONT_ID = 'c236135428ec9686'
FONT_PACKAGE = '2'
WIDTH, HEIGHT = 372, 102
SUPERSAMPLE = 4
MASK_PAD = 2


class ButtonBlocker(RuntimeError):
    """Кнопку нельзя русифицировать этим способом: причина в тексте."""


def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as error:  # pragma: no cover - зависит от окружения
        raise ButtonBlocker('PIL (Pillow) недоступен: растеризация шрифта невозможна') from error
    return Image, ImageDraw, ImageFont


def extract_font(asset: bytes) -> bytes:
    """OTF внутри Unity Font asset: u32-длина прямо перед сигнатурой OTTO."""
    offset = asset.find(b'OTTO')
    if offset < 4:
        raise ButtonBlocker('В ресурсе шрифта нет встроенного OTF (сигнатура OTTO)')
    size = struct.unpack_from('<I', asset, offset - 4)[0]
    if size <= 0 or offset + size > len(asset):
        raise ButtonBlocker('Длина встроенного OTF не сходится с ресурсом')
    return asset[offset:offset + size]


def check_plane(plane: bytes, width: int = WIDTH, height: int = HEIGHT) -> None:
    if type(plane) is not bytes or len(plane) != width * height * 4:
        raise ButtonBlocker('Текстура не RGBA32 нужного размера 372x102 без mip/хвоста')


def _upright(plane: bytes, width: int, height: int):
    Image, _, _ = _pil()
    return Image.frombytes('RGBA', (width, height), plane).transpose(Image.Transpose.FLIP_TOP_BOTTOM)


def analyze_style(plane: bytes, width: int = WIDTH, height: int = HEIGHT) -> dict:
    """Рамка надписи и вертикальный профиль цвета заливки (по строкам, сверху вниз как на экране)."""
    check_plane(plane, width, height)
    image = _upright(plane, width, height)
    pixels = image.load()
    # Надпись обычно полностью непрозрачна; у серой (недоступной) кнопки заливка полупрозрачна — берём её наибольшую альфу.
    max_alpha = max((p[3] for p in image.getdata()), default=0)
    full_from = 255 if max_alpha == 255 else max_alpha - 2
    visible, opaque = [], []
    for y in range(height):
        row = [pixels[x, y] for x in range(width)]
        if any(p[3] for p in row):
            visible.append(y)
        full = [p for p in row if p[3] >= full_from]
        opaque.append(tuple(sum(p[c] for p in full) / len(full) for c in range(3)) if full else None)
    if not visible:
        raise ButtonBlocker('Исходная текстура пуста: надписи нет')
    solid = [y for y, color in enumerate(opaque) if color]
    if len(solid) < 8:
        raise ButtonBlocker('В оригинале нет непрозрачной заливки, стиль снять нельзя')
    columns = [x for x in range(width) if any(pixels[x, y][3] for y in visible)]
    # Пропуски (нет полностью непрозрачных пикселей) заполняем ближайшей строкой.
    profile = []
    for y in range(solid[0], solid[-1] + 1):
        nearest = min(solid, key=lambda s: abs(s - y))
        profile.append(opaque[nearest])
    return {'columns': (columns[0], columns[-1] + 1), 'rows': (visible[0], visible[-1] + 1),
            'solid_rows': (solid[0], solid[-1] + 1), 'profile': profile, 'alpha': max_alpha}


PROFILE_ROUGH_LIMIT = 2.0   # средняя вторая разность цвета по строкам; выше — радужная заливка даёт на новой надписи полосы
PROFILE_WINDOW = 4          # половина окна скользящего среднего


def _smooth_profile(profile: list) -> list:
    """Профиль гладкой заливки не трогаем; шумный (радужный) усредняем по строкам, иначе на буквах видны горизонтальные полосы."""
    n = len(profile)
    if n < 3:
        return profile
    rough = sum(abs(profile[k - 1][c] - 2 * profile[k][c] + profile[k + 1][c]) for k in range(1, n - 1) for c in range(3)) / (3 * (n - 2))
    if rough <= PROFILE_ROUGH_LIMIT:
        return profile
    out = []
    for k in range(n):
        lo, hi = max(0, k - PROFILE_WINDOW), min(n, k + PROFILE_WINDOW + 1)
        out.append(tuple(sum(profile[j][c] for j in range(lo, hi)) / (hi - lo) for c in range(3)))
    return out


def _edge_factor(alpha: int) -> float:
    """Края альфы в оригинале темнее заливки (до ~0.1 при a<48, полная яркость с a>=120)."""
    return max(0.1, min(1.0, (alpha - 40) / 80))


def _fit_font(font_bytes: bytes, text: str, box_w: int, box_h: int, min_condense: float = 1.0, max_size: int = 0):
    Image, ImageDraw, ImageFont = _pil()
    scale = SUPERSAMPLE

    def measure(size: int):
        font = ImageFont.truetype(io.BytesIO(font_bytes), size)
        canvas = Image.new('L', (box_w * scale * (2 if min_condense >= 1.0 else 5), box_h * scale * 3), 0)
        ImageDraw.Draw(canvas).text((box_w * scale // 2, box_h * scale), text, fill=255, font=font)
        return font, canvas, canvas.getbbox()

    low, high, best = 8, min(box_h * scale * 2, max_size) if max_size else box_h * scale * 2, None
    while low <= high:
        middle = (low + high) // 2
        _, _, bbox = measure(middle)
        if bbox is None:
            raise ButtonBlocker('Шрифт не нарисовал ни одного пикселя для этой строки (нет глифов)')
        if (bbox[2] - bbox[0]) * min_condense <= box_w * scale and bbox[3] - bbox[1] <= box_h * scale:
            best, low = middle, middle + 1
        else:
            high = middle - 1
    if best is None:
        raise ButtonBlocker('Строка не помещается в область надписи даже при минимальном кегле')
    font, canvas, bbox = measure(best)
    return canvas.crop(bbox), best


def missing_glyphs(font_bytes: bytes, text: str) -> list[str]:
    """Символы строки, которых нет в cmap шрифта (проверка до растеризации)."""
    sys.path.insert(0, str(PROJECT))
    from localization import fit_rules
    glyphs = fit_rules.glyph_codepoints(font_bytes)
    return sorted({c for c in text if not c.isspace() and ord(c) not in glyphs})


def render_label(plane: bytes, text: str, font_bytes: bytes, width: int = WIDTH, height: int = HEIGHT, min_condense: float = 1.0) -> bytes:
    """Новая плоскость (порядок строк как в файле): область надписи заменена русской надписью."""
    return _render_label_ex(plane, text, font_bytes, width, height, min_condense)[0]


def _render_label_ex(plane: bytes, text: str, font_bytes: bytes, width: int, height: int, min_condense: float) -> tuple[bytes, float, float]:
    """render_label плюс кегль (px) и коэффициент горизонтального сжатия (1.0 — без сжатия)."""
    Image, _, _ = _pil()
    check_plane(plane, width, height)
    text = text.strip()
    if not text or '\n' in text:
        raise ButtonBlocker('Надпись кнопки — одна непустая строка')
    absent = missing_glyphs(font_bytes, text)
    if absent:
        raise ButtonBlocker('В шрифте OSGameFont_Title нет глифов: ' + ' '.join(f'U+{ord(c):04X}' for c in absent))
    style = analyze_style(plane, width, height)
    col0, col1 = style['columns']
    solid0, solid1 = style['solid_rows']
    box_w, box_h = col1 - col0, solid1 - solid0
    # Без сжатия кегль не ограничиваем сверху (как в первом пилоте); со сжатием — не крупнее китайского, чтобы сжимать минимально.
    cap = round(box_h / 0.93 * SUPERSAMPLE) if min_condense < 1.0 else 0
    big, font_size = _fit_font(font_bytes, text, box_w, box_h, min_condense=min_condense, max_size=cap)
    sx = min(1.0, box_w * SUPERSAMPLE / big.width)
    label = big.resize((max(1, round(big.width * sx / SUPERSAMPLE)), max(1, round(big.height / SUPERSAMPLE))), Image.Resampling.BOX)
    if label.width > width or label.height > height:
        raise ButtonBlocker('Надпись больше текстуры')
    left = col0 + (box_w - label.width) // 2
    top = solid0 + (box_h - label.height) // 2
    profile = _smooth_profile(style['profile'])
    fill_alpha = style['alpha']
    alpha = label.load()
    upright = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    out = upright.load()
    for y in range(label.height):
        t = y / max(1, label.height - 1)
        r, g, b = profile[min(len(profile) - 1, round(t * (len(profile) - 1)))]
        for x in range(label.width):
            a = alpha[x, y]
            if not a:
                continue
            f = _edge_factor(a)
            out[left + x, top + y] = (round(r * f), round(g * f), round(b * f), round(a * fill_alpha / 255))
    return upright.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes(), font_size / SUPERSAMPLE, sx


def changed_box(source: bytes, candidate: bytes, width: int = WIDTH, height: int = HEIGHT, pad: int = MASK_PAD) -> list[int]:
    """Минимальный прямоугольник [x0,y0,x1,y1] (порядок строк файла), покрывающий оба рисунка, с запасом pad."""
    xs, ys = [], []
    for plane in (source, candidate):
        for y in range(height):
            row = plane[y * width * 4:(y + 1) * width * 4]
            for x in range(width):
                if row[x * 4 + 3]:
                    xs.append(x)
                    ys.append(y)
    if not xs:
        raise ButtonBlocker('Нет непрозрачных пикселей ни в оригинале, ни в кандидате')
    return [max(0, min(xs) - pad), max(0, min(ys) - pad), min(width, max(xs) + 1 + pad), min(height, max(ys) + 1 + pad)]


def compose(source: bytes, text: str, font_bytes: bytes, width: int = WIDTH, height: int = HEIGHT, min_condense: float = 1.0) -> tuple[bytes, dict]:
    """Готовая текстура-кандидат и доказательство: за пределами маски байты равны оригиналу."""
    drawn = render_label(source, text, font_bytes, width, height, min_condense)
    return _finish(source, drawn, text, width, height)


def _finish(source: bytes, drawn: bytes, text: str, width: int, height: int) -> tuple[bytes, dict]:
    """Маска вокруг изменённой области, копия оригинала с заменой внутри маски и native range patch."""
    # Native-проверка требует хотя бы один пиксель вне маски: если запас pad закрывает всю текстуру, сужаем его.
    for pad in range(MASK_PAD, -1, -1):
        mask = changed_box(source, drawn, width, height, pad)
        if mask != [0, 0, width, height]:
            break
    else:
        raise ButtonBlocker('Рисунок занимает всю текстуру до краёв: маска без пикселей снаружи невозможна')
    candidate = bytearray(source)
    for y in range(mask[1], mask[3]):
        start = (y * width + mask[0]) * 4
        end = (y * width + mask[2]) * 4
        candidate[start:end] = drawn[start:end]
    candidate = bytes(candidate)
    spec = {'schema': PATCH_SCHEMA, 'width': width, 'height': height, 'mip_count': 1, 'row_origin': ROW_ORIGIN,
            'source_sha256': hashlib.sha256(source).hexdigest(), 'candidate_sha256': hashlib.sha256(candidate).hexdigest(),
            'text_masks': [mask]}
    try:
        patched, proof = build_rgba32_patch(source, candidate, spec)
    except ValueError as error:
        raise ButtonBlocker(f'Маска/патч текстуры отвергнуты: {error}') from error
    if patched != candidate:
        raise ButtonBlocker('Native range patch не воспроизводит кандидат')
    return candidate, {'mask': mask, 'spec': spec, 'proof': proof, 'text': text}


# ---------------------------------------------------------------- плашки рангов (белый текст + тёмная тень)

# Размеры известных текстур пакета 19: кнопки и плашки рангов (RGBA32, один mip).
KNOWN_SIZES = {WIDTH * HEIGHT * 4: (WIDTH, HEIGHT), 120 * 46 * 4: (120, 46), 120 * 47 * 4: (120, 47)}
PLATE_PAD_X = 3            # поле слева и справа до края текстуры
PLATE_PAD_Y = 0            # поле сверху и снизу от рамки белого текста оригинала
SHADOW_OFFSET = (0, 2)     # смещение тени относительно белого текста (подобрано по оригиналу 秩序白银)
SHADOW_BLUR = 0.6
SHADOW_ALPHA = 0.65
MIN_CONDENSE = 0.60        # наименьшее горизонтальное сжатие надписи плашки (1.0 — без сжатия)
BUTTON_MIN_CONDENSE = 0.80  # кнопка сжимается только если без сжатия кегль меньше EM_MIN_RATIO
EM_MIN_RATIO = 0.9         # допустимое отношение кегля русской надписи к кеглю китайской


def texture_size(plane: bytes) -> tuple[int, int]:
    size = KNOWN_SIZES.get(len(plane)) if type(plane) is bytes else None
    if size is None:
        raise ButtonBlocker('Размер текстуры не из известных (372x102, 120x46, 120x47 RGBA32 без mip)')
    return size


def analyze_plate(plane: bytes, width: int, height: int) -> dict:
    """Рамка белого текста плашки (пиксели с почти непрозрачным белым) в порядке строк «как на экране»."""
    image = _upright(plane, width, height)
    pixels = image.load()
    text = [(x, y) for y in range(height) for x in range(width) if pixels[x, y][3] >= 128 and pixels[x, y][0] >= 250]
    solid = [(x, y) for y in range(height) for x in range(width) if pixels[x, y][3] == 255]
    if not text or len(text) < 0.9 * len(solid):
        raise ButtonBlocker('Плашка не «белый текст с тенью»: стиль снять нельзя')
    xs, ys = [p[0] for p in text], [p[1] for p in text]
    return {'columns': (min(xs), max(xs) + 1), 'rows': (min(ys), max(ys) + 1)}


def _alpha_box_upright(plane: bytes, width: int, height: int) -> tuple[int, int, int, int]:
    """Рамка пикселей с ненулевой альфой в порядке строк «как на экране»."""
    box = _upright(plane, width, height).getchannel('A').getbbox()
    if box is None:
        raise ButtonBlocker('Исходная текстура пуста: надписи нет')
    return box


def render_plate(plane: bytes, text: str, font_bytes: bytes, width: int, height: int) -> tuple[bytes, dict]:
    """Белая надпись плашки с мягкой тенью; возвращает плоскость (порядок строк файла) и метрики кегля."""
    Image, _, _ = _pil()
    from PIL import ImageChops, ImageFilter
    text = text.strip()
    if not text or '\n' in text:
        raise ButtonBlocker('Надпись плашки — одна непустая строка')
    absent = missing_glyphs(font_bytes, text)
    if absent:
        raise ButtonBlocker('В шрифте OSGameFont_Title нет глифов: ' + ' '.join(f'U+{ord(c):04X}' for c in absent))
    style = analyze_plate(plane, width, height)
    cn_h = style['rows'][1] - style['rows'][0]
    box_w, box_h = width - 2 * PLATE_PAD_X, cn_h - 2 * PLATE_PAD_Y  # тень уходит вниз на 2-3 пикселя и должна остаться в рамке оригинала
    # Оригинал — узкие высокие иероглифы; кириллица шрифта игры шире, поэтому по ширине её допустимо сжать до MIN_CONDENSE.
    big, font_size = _fit_font(font_bytes, text, box_w, box_h, min_condense=MIN_CONDENSE, max_size=round(cn_h / 0.93 * SUPERSAMPLE))
    sx = min(1.0, box_w * SUPERSAMPLE / big.width)
    label = big.resize((max(1, round(big.width * sx / SUPERSAMPLE)), max(1, round(big.height / SUPERSAMPLE))), Image.Resampling.BOX)
    left = (width - label.width) // 2
    top = style['rows'][0] + PLATE_PAD_Y + (box_h - label.height) // 2
    layer = Image.new('L', (width, height), 0)
    layer.paste(label, (left, top))
    shadow = ImageChops.offset(layer, *SHADOW_OFFSET).filter(ImageFilter.GaussianBlur(SHADOW_BLUR)).point(lambda v: round(v * SHADOW_ALPHA))
    upright = Image.new('RGBA', (width, height), (255, 255, 255, 0))
    out, text_a, shadow_a = upright.load(), layer.load(), shadow.load()
    for y in range(height):
        for x in range(width):
            at, sh = text_a[x, y] / 255, shadow_a[x, y] / 255
            total = at + sh * (1 - at)
            if total > 0:
                out[x, y] = (round(255 * at / total),) * 3 + (round(255 * total),)
    # Хвосты размытия тени за рамкой видимых пикселей оригинала отбрасываем: рисунок не выходит за ту же рамку.
    ax0, ay0, ax1, ay1 = _alpha_box_upright(plane, width, height)
    for y in range(height):
        for x in range(width):
            if not (ax0 <= x < ax1 and ay0 <= y < ay1):
                out[x, y] = (255, 255, 255, 0)
    metrics = {'font_px': font_size / SUPERSAMPLE, 'cn_ink_height': cn_h, 'em_ratio': round(font_size / SUPERSAMPLE / (cn_h / 0.93), 3), 'condense': round(sx, 3),
               'ink_box': [left, top, left + label.width, top + label.height], 'box': [PLATE_PAD_X, style['rows'][0], width - PLATE_PAD_X, style['rows'][1]]}
    return upright.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes(), metrics


def compose_texture(source: bytes, text: str, font_bytes: bytes) -> tuple[bytes, dict]:
    """Кнопка 372x102 или плашка ранга 120x46/47: размер берётся из длины текстуры; info['metrics'] — кегль."""
    width, height = texture_size(source)
    if (width, height) == (WIDTH, HEIGHT):
        style = analyze_style(source)
        cn_h = style['solid_rows'][1] - style['solid_rows'][0]
        for condense in (1.0, BUTTON_MIN_CONDENSE):
            drawn, font_px, sx = _render_label_ex(source, text, font_bytes, width, height, condense)
            ratio = font_px / (cn_h / 0.93)
            if ratio >= EM_MIN_RATIO:
                break
        candidate, info = _finish(source, drawn, text, width, height)
        info['metrics'] = {'font_px': font_px, 'cn_ink_height': cn_h, 'em_ratio': round(ratio, 3), 'condense': round(sx, 3)}
        return candidate, info
    drawn, metrics = render_plate(source, text, font_bytes, width, height)
    candidate, info = _finish(source, drawn, text, width, height)
    info['metrics'] = metrics
    return candidate, info


def preview_png(plane: bytes, width: int = 0, height: int = 0, background=(40, 42, 64)) -> bytes:
    """Просмотр на тёмном фоне, как на экране (для проверки глазами; в игру не идёт). Размер по умолчанию — по длине текстуры."""
    Image, _, _ = _pil()
    if not width or not height:
        width, height = texture_size(plane)
    shown = _upright(plane, width, height)
    canvas = Image.new('RGBA', (width, height), background + (255,))
    canvas.alpha_composite(shown)
    buffer = io.BytesIO()
    canvas.convert('RGB').save(buffer, 'PNG')
    return buffer.getvalue()
