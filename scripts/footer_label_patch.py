"""Русская подпись внизу карточки (пакет 15): стирание китайской подписи с подложки и рисование русской.

Стиль `footer` для atlas_text_patch.render_item (region — прямоугольник правки в координатах «как на экране», строки сверху вниз).
Подпись карточки — одноцветный текст без обводки и тени прямо на подложке (градиент, радужная или тёмная полоса).

Шаги на одну текстуру (всё по пикселям оригинала, никаких ручных масок):
1. Окно вокруг рамки OCR (`hint` в координатах области). Фон окна — построчная линейная интерполяция между колонками
   слева и справа от окна: вертикальный градиент подложки сохраняется точно, горизонтальный (радужные полосы) — линейно.
2. Старая подпись — пиксели окна, отличающиеся от фона (порог от максимума отличия); дыра = она, расширенная на 2 px.
3. Дыра закрашивается тем же фоном плюс шум, повторяющий зерно подложки (сид — от содержимого, результат детерминирован).
4. Цвет новой подписи — среднее ярчайших 20% пикселей старой подписи (по отличию от фона): подпись остаётся того же тона.
5. Русская строка рисуется шрифтом игры (OSGameFont_Title) без сжатия по горизонтали: кегль по высоте китайской подписи,
   не мельче EM_MIN_RATIO; выравнивание `center` или `right` по китайской подписи; ширина ограничена `max_w`.
Область правки (rect) заводится по границам блоков 4x4, поэтому пиксели вне её после BC7 остаются побайтно прежними.
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

SS = 4                  # суперсэмплинг растеризации
EM_RATIO = 0.95         # желаемый кегль русской подписи от «кегля» китайской (высота чернил / 0.93)
EM_MIN_RATIO = 0.9      # меньше — отказ (как в psb.EM_MIN_RATIO)
INK_THRESHOLD = 0.18    # доля максимального отличия от фона, с которой пиксель считается чернилами
HOLE_GROW = 2           # расширение дыры, px
SOFT = 0.35             # сглаживание края новой подписи, px (оригинал мягкий: ресайз игры)


class FooterBlocker(RuntimeError):
    """Подпись нельзя нарисовать: причина в тексте сообщения."""


def plate_limits(img: np.ndarray, hint) -> tuple[int, int]:
    """Границы подложки по горизонтали рядом с подписью: первая колонка справа/слева от рамки, где медиана по строкам подписи скачет
    (рамка карточки, чёрный край, прозрачность). Фон окна берётся только внутри этих границ."""
    x0, y0, x1, y1 = hint
    height, width = img.shape[:2]
    rows = img[max(0, y0):min(height, y1)]
    med = np.median(rows[..., :3].astype(float), axis=0)
    alpha = np.median(rows[..., 3].astype(float), axis=0)
    right = width
    for x in range(min(width - 1, x1 + 2), width):
        if alpha[x] < 240 or np.abs(med[x] - med[x - 1]).max() > 10:
            right = x
            break
    left = 0
    for x in range(max(1, x0 - 2), 0, -1):
        if alpha[x] < 240 or np.abs(med[x] - med[x + 1]).max() > 10:
            left = x + 1
            break
    return left, right


def window_for(hint, width: int, height: int, limits, pad_x: int = 8, pad_y: int = 3, ring: int = 3):
    x0, y0, x1, y1 = hint
    left, right = limits
    return (max(left + ring + 1, x0 - pad_x), max(0, y0 - pad_y), min(right - ring - 1, x1 + pad_x), min(height, y1 + pad_y))


def row_background(img: np.ndarray, win, ring: int = 3) -> np.ndarray:
    """Фон окна по строкам: линейная интерполяция между средним `ring` колонок слева и справа от окна (float, H_w x W_w x 3)."""
    x0, y0, x1, y1 = win
    rgb = img[..., :3].astype(float)
    left = rgb[y0:y1, x0 - ring:x0].mean(axis=1)
    right = rgb[y0:y1, x1:x1 + ring].mean(axis=1)
    t = ((np.arange(x1 - x0) + 0.5) / (x1 - x0))[None, :, None]
    return left[:, None, :] * (1 - t) + right[:, None, :] * t


def locate_ink(img: np.ndarray, hint, max_pad_y: int = 9):
    """Окно, фон, маска чернил старой подписи. Окно растёт по вертикали, пока чернила касаются его верха или низа."""
    height, width = img.shape[:2]
    pad_y = 3
    limits = plate_limits(img, hint)
    while True:
        win = window_for(hint, width, height, limits, pad_y=pad_y)
        x0, y0, x1, y1 = win
        bg = row_background(img, win)
        diff = np.abs(img[y0:y1, x0:x1, :3].astype(float) - bg).max(axis=2)
        peak = float(diff.max())
        if peak < 24:
            raise FooterBlocker(f'В окне {win} нет контрастной подписи (максимум отличия от фона {peak:.0f})')
        ink = diff > max(10.0, INK_THRESHOLD * peak)
        rows = np.nonzero(ink.any(axis=1))[0]
        cols = np.nonzero(ink.any(axis=0))[0]
        touch_y = rows[0] == 0 or rows[-1] == ink.shape[0] - 1
        touch_x = cols[0] == 0 or cols[-1] == ink.shape[1] - 1
        if touch_x:
            raise FooterBlocker(f'Чернила подписи упираются в левый/правый край окна {win}: рядом другой рисунок')
        if touch_y and pad_y < max_pad_y:
            pad_y += 3
            continue
        if touch_y:
            raise FooterBlocker(f'Чернила подписи упираются в верх/низ окна {win}: подложка не однородна')
        return win, bg, diff, ink, peak


def grow(mask: np.ndarray, radius: int) -> np.ndarray:
    from scipy import ndimage
    return ndimage.binary_dilation(mask, structure=np.ones((3, 3), bool), iterations=radius)


def erase_old(img: np.ndarray, win, bg, ink, seed: int) -> np.ndarray:
    """Копия картинки со стёртой старой подписью (фон + зерно подложки)."""
    from scipy import ndimage
    x0, y0, x1, y1 = win
    hole = grow(ink, HOLE_GROW)
    region = img[y0:y1, x0:x1, :3].astype(float)
    resid = (region - bg)[~hole]
    std = resid.std(axis=0) if resid.size else np.zeros(3)
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, 1.0, region.shape) * np.minimum(std, 6.0)[None, None, :]
    filled = bg + noise
    soft = np.clip(ndimage.gaussian_filter(hole.astype(float), 0.7), 0, 1)
    soft[hole] = 1.0
    out = img.copy()
    mixed = region * (1 - soft[..., None]) + filled * soft[..., None]
    out[y0:y1, x0:x1, :3] = np.clip(np.round(mixed), 0, 255).astype(np.uint8)
    return out


def ink_color(region_rgb: np.ndarray, bg: np.ndarray, diff: np.ndarray, ink: np.ndarray) -> np.ndarray:
    """Цвет подписи: среднее ярчайших 20% пикселей чернил (по отличию от фона)."""
    values = diff[ink]
    cut = np.quantile(values, 0.8)
    pick = ink & (diff >= cut)
    return region_rgb[pick].mean(axis=0)


def rasterize(font_bytes: bytes, text: str, em_px: float):
    """Маска строки на суперсэмпле и метрики (в пикселях суперсэмпла): ширина, высота заглавной, расстояния от базовой линии."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(io.BytesIO(font_bytes), max(8, int(round(em_px * SS))))
    left, top, right, bottom = font.getbbox(text, anchor='ls')
    cap_left, cap_top, cap_right, cap_bottom = font.getbbox('Т', anchor='ls')
    pad = 4 * SS
    width = right - left + 2 * pad
    height = (bottom - top) + 2 * pad
    canvas = Image.new('L', (width, height), 0)
    baseline = pad - top
    ImageDraw.Draw(canvas).text((pad - left, baseline), text, fill=255, font=font, anchor='ls')
    return np.asarray(canvas, dtype=float) / 255.0, {'ink_w': right - left, 'pad': pad, 'baseline': baseline, 'cap_h': cap_bottom - cap_top, 'left': left}


def fit_text(font_bytes: bytes, text: str, cn_h: int, max_w: int, ratio: float = EM_RATIO):
    """Наибольший кегль не выше ratio·(cn_h/0.93), при котором строка без сжатия помещается в max_w; не мельче EM_MIN_RATIO."""
    cn_em = cn_h / 0.93
    size = cn_em * ratio
    floor = cn_em * EM_MIN_RATIO
    while True:
        mask, metrics = rasterize(font_bytes, text, size)
        if metrics['ink_w'] / SS <= max_w:
            return mask, metrics, size, size / cn_em
        size -= 0.25
        if size < floor:
            raise FooterBlocker(f'«{text}» не помещается в {max_w} px при кегле не мельче {EM_MIN_RATIO} китайского (нужна короткая форма)')


def downsample(layer: np.ndarray) -> np.ndarray:
    height, width = layer.shape
    return layer[:height // SS * SS, :width // SS * SS].reshape(height // SS, SS, width // SS, SS).mean(axis=(1, 3))


def render_footer(region: np.ndarray, text: str, font_bytes: bytes, hint, align: str = 'center', max_w: int = 110, ratio: float = EM_RATIO,
                  color=None, soft: float = SOFT, mode: str = 'plain', **extra) -> tuple[np.ndarray, dict]:
    """region (H,W,4) -> (новая область того же размера, метрики). hint=[x0,y0,x1,y1] рамки китайской подписи в координатах области.

    mode='v2' — расширение для всего пакета 15 (footer_label_v2: орнаменты, пол/потолок кегля, заливка рисунка); extra — его параметры."""
    if mode == 'v2':
        import footer_label_v2
        return footer_label_v2.render(region, text, font_bytes, hint, align=align, max_w=max_w, soft=soft, **extra)
    if extra or mode != 'plain':
        raise FooterBlocker(f'Неизвестные параметры подписи: mode={mode!r}, {sorted(extra)}')
    from PIL import Image, ImageFilter
    text = text.strip()
    if not text or '\n' in text:
        raise FooterBlocker('Подпись — одна непустая строка')
    if align not in ('center', 'right', 'left'):
        raise FooterBlocker(f'align {align!r} не из center/right/left')
    height, width = region.shape[:2]
    win, bg, diff, ink, peak = locate_ink(region, hint)
    x0, y0, x1, y1 = win
    seed = int.from_bytes(hashlib.sha256(region.tobytes()).digest()[:4], 'little')
    base = erase_old(region, win, bg, ink, seed)
    ys, xs = np.nonzero(ink)
    ink_box = (x0 + int(xs.min()), y0 + int(ys.min()), x0 + int(xs.max()) + 1, y0 + int(ys.max()) + 1)
    cn_h = ink_box[3] - ink_box[1]
    rgb = ink_color(region[y0:y1, x0:x1, :3].astype(float), bg, diff, ink) if color is None else np.array(color, dtype=float)
    mask, metrics, size, ratio_done = fit_text(font_bytes, text, cn_h, max_w, ratio)
    pad = metrics['pad']
    ink_w = metrics['ink_w'] / SS
    cx = (ink_box[0] + ink_box[2]) / 2
    if align == 'center':
        left_px = cx - ink_w / 2
    elif align == 'right':
        left_px = ink_box[2] - ink_w
    else:
        left_px = ink_box[0]
    cy = (ink_box[1] + ink_box[3]) / 2
    baseline_y = cy + metrics['cap_h'] / SS / 2
    # Положение холста маски в пикселях области: пиксель суперсэмпла (pad, baseline) -> (left_px - 0, baseline_y).
    origin_x = int(round(left_px * SS)) - pad
    origin_y = int(round(baseline_y * SS)) - metrics['baseline']
    # Холст кратен SS: сдвиг маски под сетку пикселей.
    gx0, gy0 = origin_x // SS, origin_y // SS
    shift_x, shift_y = origin_x - gx0 * SS, origin_y - gy0 * SS
    gw = (mask.shape[1] + shift_x + SS - 1) // SS
    gh = (mask.shape[0] + shift_y + SS - 1) // SS
    canvas = np.zeros((gh * SS, gw * SS))
    canvas[shift_y:shift_y + mask.shape[0], shift_x:shift_x + mask.shape[1]] = mask
    cov = downsample(canvas)
    if soft > 0:
        img = Image.fromarray((np.clip(cov, 0, 1) * 255).round().astype(np.uint8), 'L').filter(ImageFilter.GaussianBlur(soft))
        cov = np.asarray(img, dtype=float) / 255.0
    text_box = [gx0, gy0, gx0 + gw, gy0 + gh]
    if text_box[0] < 4 or text_box[2] > width - 4 or text_box[1] < 0 or text_box[3] > height:
        raise FooterBlocker(f'Подпись выходит за область правки: {text_box} в {width}x{height}')
    out = base.copy()
    # Видимые чернила новой подписи.
    nz = cov > 0.02
    yy, xx = np.nonzero(nz)
    visible = [int(xx.min()) + gx0, int(yy.min()) + gy0, int(xx.max()) + 1 + gx0, int(yy.max()) + 1 + gy0]
    sub = out[gy0:gy0 + gh, gx0:gx0 + gw, :3].astype(float)
    sub = sub * (1 - cov[..., None]) + rgb[None, None, :] * cov[..., None]
    out[gy0:gy0 + gh, gx0:gx0 + gw, :3] = np.clip(np.round(sub), 0, 255).astype(np.uint8)
    # Всё, что менялось, должно лежать внутри области правки.
    changed = np.any(out != region, axis=2)
    if not changed.any():
        raise FooterBlocker('Подпись не изменила ни одного пикселя')
    metrics_out = {'font_px': round(size, 2), 'cn_ink_height': cn_h, 'em_ratio': round(ratio_done, 3), 'condense': 1.0,
                   'ink_box': visible, 'old_ink_box': list(ink_box), 'color': [int(round(v)) for v in rgb], 'peak_diff': round(peak, 1),
                   'ink_width': round(ink_w, 1)}
    return out, metrics_out


def block_rect(x0: int, y0: int, x1: int, y1: int, width: int, height: int) -> list[int]:
    """Прямоугольник, расширенный до границ блоков 4x4 (текстура кратна 4)."""
    return [max(0, x0 // 4 * 4), max(0, y0 // 4 * 4), min(width, -(-x1 // 4) * 4), min(height, -(-y1 // 4) * 4)]
