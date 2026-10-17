"""Русские надписи на спрайтах атласов BC7 (Texture2D без mip, поток блоков 4x4 по 16 байт).

Вход — поток текстуры и список правок: прямоугольник надписи (в координатах «как на экране», строки сверху вниз),
русский текст и стиль. Выход — тот же по размеру поток, где перекодированы только блоки 4x4, пересекающиеся
с прямоугольниками правок; все остальные блоки побайтно исходные (доказательство в отчёте).

Стили надписи снимаются с самого оригинала (область правки как RGBA-плоскость):
- `button`  — вертикальный градиент заливки и мягкий тёмный край альфы (как у кнопок, pilot_start_button);
- `plate`   — белый текст и тёмная тень (плашки рангов);
- `footer`  — подпись внизу карточки пакета 15 (footer_label_patch: стирание фона по соседним пикселям, цвет с оригинала);
- `layers`  — заливка (градиент по строкам или цвет), обводка и тень заданных параметров поверх друг друга;
  цвет заливки берётся из оригинала по строкам, обводка/тень задаются в параметрах правки.
Шрифт — OSGameFont_Title игры (пакет 2), растеризация PIL. Ничего не пишет на диск и не читает игру.
"""
from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
for _path in (str(PROJECT), str(PROJECT / 'scripts')):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bc7_codec as bc7  # noqa: E402
import pilot_start_button as psb  # noqa: E402

SS = psb.SUPERSAMPLE
EM_MIN_RATIO = psb.EM_MIN_RATIO


class AtlasBlocker(RuntimeError):
    """Надпись нельзя нарисовать этим способом: причина в тексте."""


def atlas_size(stream_length: int, size: list[int] | tuple[int, int] | None = None) -> tuple[int, int]:
    """Размер текстуры BC7 без mip: квадрат, если не задан явно; длина потока обязана сойтись."""
    if size:
        width, height = int(size[0]), int(size[1])
    else:
        side = int(round((stream_length) ** 0.5))
        width = height = side
    if width <= 0 or height <= 0 or bc7.stream_size(width, height) != stream_length:
        raise AtlasBlocker(f'Поток {stream_length} байт не равен BC7 {width}x{height} без mip')
    return width, height


def decode_upright(stream: bytes, width: int, height: int) -> np.ndarray:
    """Независимое чтение потока Pillow; строки сверху вниз (поток хранит их снизу вверх)."""
    return bc7.pillow_decode(stream, width, height)[::-1].copy()


def to_stream_rect(rect: list[int], height: int) -> list[int]:
    x0, y0, x1, y1 = rect
    return [x0, height - y1, x1, height - y0]


def check_rect(rect, width: int, height: int) -> list[int]:
    if (not isinstance(rect, (list, tuple)) or len(rect) != 4 or not all(isinstance(v, int) for v in rect)
            or not (0 <= rect[0] < rect[2] <= width and 0 <= rect[1] < rect[3] <= height)):
        raise AtlasBlocker(f'Прямоугольник {rect} вне текстуры {width}x{height}')
    return list(rect)


# ---------------------------------------------------------------- растеризация

def _fit_mask(font_bytes: bytes, text: str, box_w: int, box_h: int, min_condense: float, cap: int):
    """Маска надписи (L, суперсэмплинг) и кегль; сжатие по горизонтали не меньше min_condense."""
    absent = psb.missing_glyphs(font_bytes, text)
    if absent:
        raise AtlasBlocker('В шрифте OSGameFont_Title нет глифов: ' + ' '.join(f'U+{ord(c):04X}' for c in absent))
    try:
        return psb._fit_font(font_bytes, text, box_w, box_h, min_condense=min_condense, max_size=cap)
    except psb.ButtonBlocker as error:
        raise AtlasBlocker(str(error)) from error


def _ink_box(region: np.ndarray, threshold: int = 0):
    ys, xs = np.nonzero(region[..., 3] > threshold)
    if not len(xs):
        raise AtlasBlocker('В области нет видимых пикселей: надписи нет')
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _fill_box(region: np.ndarray, bright: float = 0.6):
    """Рамка пикселей заливки (почти непрозрачные и светлые) и их маска."""
    alpha = region[..., 3]
    lum = region[..., :3].max(axis=2).astype(float)
    solid = alpha >= 230
    if not solid.any():
        raise AtlasBlocker('В области нет почти непрозрачных пикселей заливки')
    mask = solid & (lum >= bright * lum[solid].max())
    ys, xs = np.nonzero(mask)
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1), mask


def _profile(region: np.ndarray, mask: np.ndarray, y0: int, y1: int) -> np.ndarray:
    """Цвет заливки по строкам [y0, y1): среднее по пикселям заливки; пропуски — ближайшая строка; шумный профиль сглаживается."""
    rows = []
    for y in range(y0, y1):
        selected = region[y][mask[y]][:, :3]
        rows.append(selected.mean(axis=0) if len(selected) else None)
    known = [i for i, row in enumerate(rows) if row is not None]
    if not known:
        raise AtlasBlocker('Нет строк заливки')
    filled = [rows[min(known, key=lambda k: abs(k - i))] if row is None else row for i, row in enumerate(rows)]
    return np.array(psb._smooth_profile([tuple(r) for r in filled]), dtype=float)


def _shadow_layer(layer: np.ndarray, offset: tuple[int, int], blur: float) -> np.ndarray:
    """Сдвиг (в пикселях 1x) и гауссово размытие альфы; layer — float 0..1 (H,W)."""
    from PIL import Image, ImageFilter
    height, width = layer.shape
    shifted = np.zeros_like(layer)
    dx, dy = offset
    ys, ye = max(0, dy), min(height, height + dy)
    xs, xe = max(0, dx), min(width, width + dx)
    shifted[ys:ye, xs:xe] = layer[ys - dy:ye - dy, xs - dx:xe - dx]
    if blur <= 0:
        return shifted
    image = Image.fromarray((shifted * 255).round().astype(np.uint8), 'L').filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(image, dtype=float) / 255.0


def _dilate(label_ss: np.ndarray, radius_px: float) -> np.ndarray:
    """Расширение маски на суперсэмпле (круглый элемент) и возврат к float 0..1 на суперсэмпле."""
    from scipy import ndimage
    r = max(1, int(round(radius_px * SS)))
    yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
    structure = (xx * xx + yy * yy) <= r * r
    grown = ndimage.binary_dilation(label_ss >= 0.5, structure=structure)
    # мягкий край: сглаживание на суперсэмпле
    return ndimage.gaussian_filter(grown.astype(float), SS * 0.25)


def _downsample(layer_ss: np.ndarray) -> np.ndarray:
    height, width = layer_ss.shape
    return layer_ss.reshape(height // SS, SS, width // SS, SS).mean(axis=(1, 3))


def render_layers(region: np.ndarray, text: str, font_bytes: bytes, outline: float = 0.0, outline_color=(20, 22, 32),
                  shadow=(0, 0), shadow_blur: float = 0.0, shadow_alpha: float = 0.0, shadow_color=(0, 0, 0),
                  min_condense: float = 0.6, max_em: float = 1.0, fill_color=None, bright: float = 0.6) -> tuple[np.ndarray, dict]:
    """Область (H,W,4, строки сверху вниз): заливка (профиль по строкам оригинала или fill_color) + обводка + тень."""
    text = text.strip()
    if not text or '\n' in text:
        raise AtlasBlocker('Надпись — одна непустая строка')
    height, width = region.shape[:2]
    fill_box, fill_mask = _fill_box(region, bright)
    ink = _ink_box(region)
    fx0, fy0, fx1, fy1 = fill_box
    box_w, box_h = fx1 - fx0, fy1 - fy0
    cap = round(box_h / 0.93 * SS * max_em)
    big, size = _fit_mask(font_bytes, text, box_w, box_h, min_condense, cap)
    sx = min(1.0, box_w * SS / big.width)
    label_w, label_h = max(1, round(big.width * sx)), big.height
    # Суперсэмпл-холст всей области: надпись по центру рамки заливки оригинала.
    from PIL import Image
    wide = big.resize((label_w, label_h), Image.Resampling.BOX)
    canvas = np.zeros((height * SS, width * SS), dtype=float)
    left = fx0 * SS + (box_w * SS - label_w) // 2
    top = fy0 * SS + (box_h * SS - label_h) // 2
    left = max(0, min(left, width * SS - label_w))
    top = max(0, min(top, height * SS - label_h))
    canvas[top:top + label_h, left:left + label_w] = np.asarray(wide, dtype=float) / 255.0
    fill_a = _downsample(canvas)
    layers = []
    if shadow_alpha > 0:
        base = _downsample(_dilate(canvas, outline)) if outline > 0 else fill_a
        layers.append((np.array(shadow_color, dtype=float), _shadow_layer(base, tuple(shadow), shadow_blur) * shadow_alpha))
    if outline > 0:
        layers.append((np.array(outline_color, dtype=float), np.clip(_downsample(_dilate(canvas, outline)), 0, 1)))
    # Цвет заливки по строкам внутри нарисованной надписи.
    ys, xs = np.nonzero(fill_a > 0.02)
    ly0, ly1 = int(ys.min()), int(ys.max()) + 1
    colors = np.zeros((height, width, 3), dtype=float)
    if fill_color is not None:
        colors[:] = np.array(fill_color, dtype=float)
    else:
        profile = _profile(region, fill_mask, fy0, fy1)
        for y in range(height):
            t = min(1.0, max(0.0, (y - ly0) / max(1, ly1 - ly0 - 1)))
            colors[y, :] = profile[min(len(profile) - 1, round(t * (len(profile) - 1)))]
    layers.append((colors, fill_a))
    # «over» снизу вверх в премультиплицированном виде.
    acc_rgb = np.zeros((height, width, 3), dtype=float)
    acc_a = np.zeros((height, width), dtype=float)
    for color, alpha in layers:
        alpha = np.clip(alpha, 0, 1)
        acc_rgb = acc_rgb * (1 - alpha)[..., None] + (color if np.ndim(color) == 3 else color[None, None, :]) * alpha[..., None]
        acc_a = acc_a + alpha * (1 - acc_a)
    out = np.zeros((height, width, 4), dtype=np.uint8)
    visible = acc_a > 1 / 510
    straight = np.where(visible[..., None], acc_rgb / np.maximum(acc_a, 1e-6)[..., None], 0)
    out[..., :3] = np.clip(np.round(straight), 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(np.round(acc_a * 255), 0, 255).astype(np.uint8)
    out[~visible] = 0
    # Рисунок не выходит за рамку видимых пикселей оригинала.
    clip = np.zeros((height, width), dtype=bool)
    clip[ink[1]:ink[3], ink[0]:ink[2]] = True
    out[~clip] = 0
    metrics = {'font_px': size / SS, 'cn_ink_height': box_h, 'em_ratio': round(size / SS / (box_h / 0.93), 3), 'condense': round(sx, 3),
               'ink_box': [left // SS, top // SS, (left + label_w) // SS, (top + label_h) // SS], 'box': [fx0, fy0, fx1, fy1]}
    return out, metrics


def render_button(region: np.ndarray, text: str, font_bytes: bytes) -> tuple[np.ndarray, dict]:
    """Стиль кнопки (градиент заливки и тёмный край); использует код pilot_start_button на плоскости области."""
    height, width = region.shape[:2]
    plane = region[::-1].tobytes()
    style = psb.analyze_style(plane, width, height)
    cn_h = style['solid_rows'][1] - style['solid_rows'][0]
    for condense in (1.0, psb.BUTTON_MIN_CONDENSE):
        try:
            drawn, font_px, sx = psb._render_label_ex(plane, text, font_bytes, width, height, condense)
        except psb.ButtonBlocker as error:
            raise AtlasBlocker(str(error)) from error
        ratio = font_px / (cn_h / 0.93)
        if ratio >= EM_MIN_RATIO:
            break
    out = np.frombuffer(drawn, dtype=np.uint8).reshape(height, width, 4)[::-1].copy()
    return out, {'font_px': font_px, 'cn_ink_height': cn_h, 'em_ratio': round(ratio, 3), 'condense': round(sx, 3)}


def render_plate(region: np.ndarray, text: str, font_bytes: bytes) -> tuple[np.ndarray, dict]:
    height, width = region.shape[:2]
    plane = region[::-1].tobytes()
    try:
        drawn, metrics = psb.render_plate(plane, text, font_bytes, width, height)
    except psb.ButtonBlocker as error:
        raise AtlasBlocker(str(error)) from error
    return np.frombuffer(drawn, dtype=np.uint8).reshape(height, width, 4)[::-1].copy(), metrics


def _inpaint(region: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """Заполнение пикселей hole цветом окружения: гауссово усреднение по известным пикселям, от мелкого масштаба к крупному."""
    from scipy import ndimage
    rgb = region[..., :3].astype(float)
    known = (~hole).astype(float)
    out = rgb.copy()
    todo = hole.copy()
    for sigma in (3, 6, 12, 24, 48):
        if not todo.any():
            break
        weight = ndimage.gaussian_filter(known, sigma)
        for c in range(3):
            est = ndimage.gaussian_filter(rgb[..., c] * known, sigma) / np.maximum(weight, 1e-6)
            take = todo & (weight > 0.02)
            out[..., c][take] = est[take]
        todo &= ~(weight > 0.02)
    if todo.any():
        raise AtlasBlocker('Фон под надписью нечем заполнить: вокруг нет известных пикселей')
    return out


def render_inpaint(region: np.ndarray, text: str, font_bytes: bytes, white_min: int = 200, box=None, grow: int = 3,
                   min_condense: float = 0.6, max_em: float = 1.0, fill_color=(255, 255, 255), shadow=(2, 3),
                   shadow_blur: float = 1.5, shadow_alpha: float = 0.45, shadow_color=(10, 10, 20)) -> tuple[np.ndarray, dict]:
    """Светлая надпись на непрозрачной плашке: старая надпись стирается заливкой фона из окружения, русская — поверх с тенью.

    Старая надпись — светлые пиксели (все каналы >= white_min). box=[x0,y0,x1,y1] в координатах области — где разместить
    новую надпись (по умолчанию рамка старой). Кегль меряется от высоты старой надписи.
    """
    from PIL import Image
    from scipy import ndimage
    text = text.strip()
    if not text or '\n' in text:
        raise AtlasBlocker('Надпись — одна непустая строка')
    height, width = region.shape[:2]
    old = (region[..., :3].min(axis=2) >= white_min) & (region[..., 3] > 200)
    ys, xs = np.nonzero(old)
    if not len(xs):
        raise AtlasBlocker('В области нет светлой надписи')
    ox0, oy0, ox1, oy1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    bx0, by0, bx1, by1 = box if box else (ox0, oy0, ox1, oy1)
    box_w, box_h = bx1 - bx0, by1 - by0
    cn_h = oy1 - oy0
    hole = ndimage.binary_dilation(old, structure=np.ones((3, 3), bool), iterations=max(1, int(grow)))
    base = region.copy()
    base[..., :3] = np.clip(np.round(_inpaint(region, hole)), 0, 255).astype(np.uint8)
    cap = round(cn_h / 0.93 * SS * max_em)
    big, size = _fit_mask(font_bytes, text, box_w, box_h, min_condense, cap)
    sx = min(1.0, box_w * SS / big.width)
    label_w, label_h = max(1, round(big.width * sx)), big.height
    wide = big.resize((label_w, label_h), Image.Resampling.BOX)
    canvas = np.zeros((height * SS, width * SS), dtype=float)
    left = max(0, min(bx0 * SS + (box_w * SS - label_w) // 2, width * SS - label_w))
    top = max(0, min(by0 * SS + (box_h * SS - label_h) // 2, height * SS - label_h))
    canvas[top:top + label_h, left:left + label_w] = np.asarray(wide, dtype=float) / 255.0
    fill_a = np.clip(_downsample(canvas), 0, 1)
    shade = np.clip(_shadow_layer(fill_a, tuple(shadow), shadow_blur) * shadow_alpha, 0, 1)
    rgb = base[..., :3].astype(float)
    rgb = rgb * (1 - shade)[..., None] + np.array(shadow_color, dtype=float) * shade[..., None]
    rgb = rgb * (1 - fill_a)[..., None] + np.array(fill_color, dtype=float) * fill_a[..., None]
    out = base.copy()
    out[..., :3] = np.clip(np.round(rgb), 0, 255).astype(np.uint8)
    metrics = {'font_px': size / SS, 'cn_ink_height': cn_h, 'em_ratio': round(size / SS / (cn_h / 0.93), 3), 'condense': round(sx, 3),
               'ink_box': [left // SS, top // SS, (left + label_w) // SS, (top + label_h) // SS], 'old_ink_box': [ox0, oy0, ox1, oy1]}
    return out, metrics


STYLES = ('button', 'plate', 'layers', 'inpaint', 'footer')


def render_item(region: np.ndarray, text: str, font_bytes: bytes, style: str, params: dict | None = None) -> tuple[np.ndarray, dict]:
    params = dict(params or {})
    if style == 'button':
        return render_button(region, text, font_bytes)
    if style == 'plate':
        return render_plate(region, text, font_bytes)
    if style == 'layers':
        return render_layers(region, text, font_bytes, **params)
    if style == 'inpaint':
        return render_inpaint(region, text, font_bytes, **params)
    if style == 'footer':
        import footer_label_patch as flp  # подпись внизу карточки пакета 15 (стирание фона и рисование)
        try:
            return flp.render_footer(region, text, font_bytes, **params)
        except (flp.FooterBlocker, TypeError) as error:
            raise AtlasBlocker(str(error)) from error
    raise AtlasBlocker(f'Стиль {style!r} не из {STYLES}')


# ---------------------------------------------------------------- атлас целиком

def compose_atlas(stream: bytes, items: list[dict], font_bytes: bytes, size=None) -> tuple[bytes, dict, np.ndarray, np.ndarray]:
    """Новый поток и отчёт; также картинки «до» и «после» (H,W,4, строки сверху вниз).

    items: [{'id', 'rect': [x0,y0,x1,y1], 'ru', 'style', 'params'?}], прямоугольники не пересекаются.
    """
    width, height = atlas_size(len(stream), size)
    before = decode_upright(stream, width, height)
    after = before.copy()
    rects, results = [], {}
    for item in items:
        rect = check_rect(item['rect'], width, height)
        for other in rects:
            if rect[0] < other[2] and other[0] < rect[2] and rect[1] < other[3] and other[1] < rect[3]:
                raise AtlasBlocker(f'{item["id"]}: прямоугольник {rect} пересекается с {other}')
        rects.append(rect)
        region = before[rect[1]:rect[3], rect[0]:rect[2]]
        drawn, metrics = render_item(region, item['ru'], font_bytes, item.get('style', 'button'), item.get('params'))
        after[rect[1]:rect[3], rect[0]:rect[2]] = drawn
        results[item['id']] = {'rect': rect, 'ru': item['ru'].strip(), 'style': item.get('style', 'button'), **{k: v for k, v in metrics.items() if k in ('font_px', 'em_ratio', 'condense', 'cn_ink_height', 'ink_box')}}
    target = after[::-1].copy()
    stream_rects = [to_stream_rect(r, height) for r in rects]
    blocks = bc7.blocks_touching(stream_rects, width, height)
    new_stream, report = bc7.reencode_stream(stream, target, blocks, width, height)
    # Независимая проверка: Pillow читает новый поток; вне правок пиксели равны исходным.
    decoded = decode_upright(new_stream, width, height)
    touched = np.zeros((height, width), dtype=bool)
    for bx, by in blocks:
        touched[height - 4 * (by + 1):height - 4 * by, bx * 4:bx * 4 + 4] = True
    inside = np.zeros((height, width), dtype=bool)
    for x0, y0, x1, y1 in rects:
        inside[y0:y1, x0:x1] = True
    untouched_equal = bool((decoded[~touched] == before[~touched]).all())
    # Пиксели блоков, попавшие в перекодирование, но вне прямоугольников правок (соседние спрайты): ошибка относительно оригинала.
    border = touched & ~inside
    border_error = float(np.abs(decoded[border].astype(int) - before[border].astype(int)).mean()) if border.any() else 0.0
    border_max = int(np.abs(decoded[border].astype(int) - before[border].astype(int)).max()) if border.any() else 0
    diff = (decoded.astype(int) - after.astype(int))
    in_rects = diff[inside]
    mse_inside = float((in_rects ** 2).mean()) if len(in_rects) else 0.0
    report.update({
        'size': [width, height], 'rects': rects, 'stream_rects': stream_rects,
        'unchanged_blocks_byte_exact': report['blocks_outside_set_byte_exact'],
        'pixels_outside_changed_blocks_equal_original': untouched_equal,
        'border_pixels_in_changed_blocks_outside_rects': int(border.sum()), 'border_mean_abs_error': round(border_error, 3), 'border_max_abs_error': border_max,
        'psnr_inside_rects_db': round(float(99.0 if mse_inside == 0 else 10 * np.log10(255.0 ** 2 / mse_inside)), 2),
        'source_sha256': hashlib.sha256(stream).hexdigest(), 'candidate_sha256': hashlib.sha256(new_stream).hexdigest(),
        'items': results,
    })
    if not untouched_equal or not report['unchanged_blocks_byte_exact'] or len(new_stream) != len(stream):
        raise AtlasBlocker('Проверка целостности атласа не пройдена: изменились блоки вне правок или размер потока')
    return new_stream, report, before, decoded


def png(image: np.ndarray) -> bytes:
    """PNG RGBA плоскости «как на экране» для листов проверки."""
    from PIL import Image
    buffer = io.BytesIO()
    Image.fromarray(image, 'RGBA').save(buffer, 'PNG')
    return buffer.getvalue()


def preview_sheet(before: np.ndarray, after: np.ndarray, rects: list[list[int]], labels: list[str], scale: int = 2,
                  background=(40, 42, 64)) -> bytes:
    """Лист «до/после»: каждая правка — две полосы (оригинал сверху, результат ниже) на тёмном фоне, как на экране."""
    from PIL import Image, ImageDraw
    pad, label_h = 6, 14
    tiles = []
    for rect, label in zip(rects, labels):
        x0, y0, x1, y1 = rect
        pair = []
        for image in (before, after):
            crop = Image.fromarray(image[y0:y1, x0:x1], 'RGBA')
            canvas = Image.new('RGBA', crop.size, background + (255,))
            canvas.alpha_composite(crop)
            pair.append(canvas.resize((crop.width * scale, crop.height * scale), Image.Resampling.NEAREST))
        tiles.append((label, pair))
    width = max(t[1][0].width for t in tiles) + pad * 2
    height = sum(label_h + t[1][0].height * 2 + pad * 3 for t in tiles) + pad
    sheet = Image.new('RGB', (width, height), (16, 16, 24))
    draw = ImageDraw.Draw(sheet)
    y = pad
    for label, (src, dst) in tiles:
        draw.text((pad, y), label, fill=(220, 220, 230))
        y += label_h
        sheet.paste(src.convert('RGB'), (pad, y))
        y += src.height + pad
        sheet.paste(dst.convert('RGB'), (pad, y))
        y += dst.height + pad
    buffer = io.BytesIO()
    sheet.save(buffer, 'PNG')
    return buffer.getvalue()
