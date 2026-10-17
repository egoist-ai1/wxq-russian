"""Подпись карточки пакета 15, расширение для всего пакета (стиль footer, params.mode='v2').

Что добавлено к footer_label_patch (пилот: тёмная плашка, однотонная подпись):
- орнаменты (листики по краям региональной подписи) вырезаются и ставятся вплотную к новой подписи теми же пикселями оригинала;
- кегль: потолок em_cap и пол em_floor в пикселях (а не только доля китайского: на золотых картах китайский вдвое крупнее);
- подложка-рисунок под подписью (золотые карты героев): дыра заполняется гармонически (уравнение Лапласа) по пикселям вокруг, плюс зерно;
- в метриках changed_box — рамка всех изменённых пикселей (по ней pkg15_full.py задаёт минимальный rect).
Без mode='v2' footer_label_patch.render_footer работает как в пилоте.
"""
from __future__ import annotations

import hashlib

import numpy as np

import footer_label_patch as flp

FooterBlocker = flp.FooterBlocker
ORN_MAX_W = 26          # орнамент не шире, px
ORN_MAX_H_RATIO = 0.6   # и не выше этой доли высоты самой высокой группы чернил
ART_STD = 9.0           # разброс остатка от фона по строкам, выше которого подложка считается рисунком


def split_groups(ink: np.ndarray, min_gap: int = 2) -> list[tuple[int, int]]:
    """Группы соседних колонок с чернилами (щели меньше min_gap склеиваются): [(x_first, x_last)]."""
    cols = ink.any(axis=0)
    groups, start, last = [], None, None
    for x, value in enumerate(cols):
        if not value:
            continue
        if start is None:
            start = x
        elif x - last - 1 >= min_gap:
            groups.append((start, last))
            start = x
        last = x
    if start is not None:
        groups.append((start, last))
    return groups


def find_ornaments(ink: np.ndarray, box_x=None):
    """(левый, правый орнамент) как (x_first, x_last) либо None, и все группы колонок.

    box_x — рамка OCR по x в координатах окна: группы целиком вне рамки — орнаменты; иначе крайняя малая группа (рамка включила орнамент)."""
    groups = split_groups(ink)
    if box_x is not None:
        outside_l = [g for g in groups if g[1] < box_x[0]]
        outside_r = [g for g in groups if g[0] > box_x[1]]
        inside = [g for g in groups if g not in outside_l and g not in outside_r]
        if inside and (outside_l or outside_r):
            left = (min(g[0] for g in outside_l), max(g[1] for g in outside_l)) if outside_l else None
            right = (min(g[0] for g in outside_r), max(g[1] for g in outside_r)) if outside_r else None
            if (left is None or left[1] - left[0] + 1 <= ORN_MAX_W) and (right is None or right[1] - right[0] + 1 <= ORN_MAX_W):
                return left, right, groups
    if len(groups) < 3:
        return None, None, groups
    heights = []
    for first, last in groups:
        rows = np.nonzero(ink[:, first:last + 1].any(axis=1))[0]
        heights.append(rows[-1] - rows[0] + 1)
    tall = max(heights)

    def small(index):
        first, last = groups[index]
        return last - first + 1 <= ORN_MAX_W and heights[index] <= ORN_MAX_H_RATIO * tall
    return (groups[0] if small(0) else None), (groups[-1] if small(len(groups) - 1) else None), groups


def laplace_fill(region: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """Гармоническое заполнение дыры по пикселям вокруг: разреженная система Лапласа по каждому каналу."""
    from scipy import sparse
    from scipy.sparse.linalg import splu
    h, w = hole.shape
    index = -np.ones((h, w), dtype=int)
    ys, xs = np.nonzero(hole)
    index[ys, xs] = np.arange(len(ys))
    n = len(ys)
    rows, cols, vals = [], [], []
    rhs = np.zeros((n, region.shape[2]))
    for k, (y, x) in enumerate(zip(ys, xs)):
        neighbours = 0
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            yy, xx = y + dy, x + dx
            if not (0 <= yy < h and 0 <= xx < w):
                continue
            neighbours += 1
            if index[yy, xx] >= 0:
                rows.append(k)
                cols.append(index[yy, xx])
                vals.append(-1.0)
            else:
                rhs[k] += region[yy, xx]
        rows.append(k)
        cols.append(k)
        vals.append(float(neighbours))
    lu = splu(sparse.csc_matrix((vals, (rows, cols)), shape=(n, n)))
    out = region.copy()
    out[ys, xs] = np.stack([lu.solve(rhs[:, c]) for c in range(region.shape[2])], axis=1)
    return out


def is_striped(part: np.ndarray, hole: np.ndarray, ratio: float = 1.5) -> bool:
    """Подложка с вертикальными полосами: перепад между соседними колонками заметно больше перепада между соседними строками (вне дыры)."""
    luma = part @ np.array([0.299, 0.587, 0.114])
    known = ~hole
    kx = known[:, 1:] & known[:, :-1]
    ky = known[1:, :] & known[:-1, :]
    if kx.sum() < 50 or ky.sum() < 50:
        return False
    gx = np.abs(np.diff(luma, axis=1))[kx].mean()
    gy = np.abs(np.diff(luma, axis=0))[ky].mean()
    return bool(gx > ratio * max(gy, 0.3))


def column_fill(part: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """Заливка дыры по колонкам: линейно между ближайшими известными пикселями колонки сверху и снизу (полосы подложки продолжаются сквозь подпись)."""
    out = part.copy()
    h, w = hole.shape
    ys = np.arange(h)
    for x in range(w):
        col = hole[:, x]
        if not col.any():
            continue
        known = ~col
        if not known.any():
            continue
        for c in range(part.shape[2]):
            out[col, x, c] = np.interp(ys[col], ys[known], part[known, x, c])
    return out


def harmonic_background(img: np.ndarray, win) -> np.ndarray:
    """Фон окна для подложки-рисунка: гармоническая интерполяция внутрь окна от его рамки в 1 px (float, H_w x W_w x 3)."""
    x0, y0, x1, y1 = win
    part = img[y0 - 1:y1 + 1, x0 - 1:x1 + 1, :3].astype(float)
    hole = np.ones(part.shape[:2], dtype=bool)
    hole[0, :] = hole[-1, :] = False
    hole[:, 0] = hole[:, -1] = False
    return laplace_fill(part, hole)[1:-1, 1:-1]


def median_background(img: np.ndarray, win, size: int = 11) -> np.ndarray:
    """Фон окна для подложки-рисунка: локальная медиана size x size внутри окна (float). Тонкие штрихи подписи медиана убирает, полосы и градиент подложки оставляет.

    Читает только окно (отражение у краёв), поэтому результат не зависит от размера области правки."""
    from scipy import ndimage
    x0, y0, x1, y1 = win
    part = img[y0:y1, x0:x1, :3].astype(float)
    return ndimage.median_filter(part, size=(size, size, 1), mode='reflect')


def column_percentile_background(img: np.ndarray, win, q: float = 80.0) -> np.ndarray:
    """Фон окна по колонкам для вертикальных полос: на каждый канал колонки берётся q-й процентиль по строкам окна (подпись тёмная, подложка светлая).

    Читает только окно, поэтому результат не зависит от размера области правки."""
    x0, y0, x1, y1 = win
    part = img[y0:y1, x0:x1, :3].astype(float)
    return np.broadcast_to(np.percentile(part, q, axis=0)[None, :, :], part.shape).copy()


def column_background(img: np.ndarray, win, ring: int = 3) -> np.ndarray:
    """Фон окна по колонкам: линейная интерполяция по вертикали между средним `ring` строк над окном и под ним (вертикальные полосы подложки)."""
    x0, y0, x1, y1 = win
    rgb = img[..., :3].astype(float)
    top = rgb[y0 - ring:y0, x0:x1].mean(axis=0)
    bottom = rgb[y1:y1 + ring, x0:x1].mean(axis=0)
    t = ((np.arange(y1 - y0) + 0.5) / (y1 - y0))[:, None, None]
    return top[None, :, :] * (1 - t) + bottom[None, :, :] * t


def locate_ink(img: np.ndarray, hint, pad_x: int, max_pad_y: int = 9, limits=None, harmonic: bool = False, zone_pad: int = 8):
    """Как flp.locate_ink, но окно шире по горизонтали (орнаменты); для рисунка под подписью — заданные границы и гармонический фон."""
    height, width = img.shape[:2]
    pad_y = 3
    if limits is None:
        limits = flp.plate_limits(img, hint)
    while True:
        win = flp.window_for(hint, width, height, limits, pad_x=pad_x, pad_y=pad_y)
        if harmonic:
            win = (win[0], max(1, win[1] - 6), win[2], min(height - 1, win[3]))   # окно выше подписи: чистые строки для заливки колонок
        x0, y0, x1, y1 = win
        bg_model = 'rows'
        if harmonic:
            bg, bg_model = median_background(img, win), 'median'
            if is_striped(img[y0:y1, x0:x1, :3].astype(float), np.zeros((y1 - y0, x1 - x0), dtype=bool)):
                # вертикальные полосы: фон — колонки, продолженные сквозь подпись (медиана плотного иероглифа принимает его за фон)
                bg_model = 'cols'   # чернила по медианному фону (он ловит и светлую кайму), заливка дыры — по колонкам
        else:
            bg = flp.row_background(img, win)
            if y0 >= 3 and y1 + 3 <= height:
                bg_cols = column_background(img, win)
                d_rows = np.abs(img[y0:y1, x0:x1, :3].astype(float) - bg).max(axis=2)
                d_cols = np.abs(img[y0:y1, x0:x1, :3].astype(float) - bg_cols).max(axis=2)
                if np.median(d_rows) > np.median(d_cols) + 2.0:
                    bg, bg_model = bg_cols, 'cols'
        diff = np.abs(img[y0:y1, x0:x1, :3].astype(float) - bg).max(axis=2)
        if harmonic:
            # подложка-рисунок: чернилами считается только зона рамки OCR (с орнаментами), остальной рисунок окна не трогаем
            zone = np.zeros(diff.shape, dtype=bool)
            zone[max(0, hint[1] - 2 - y0):max(0, hint[3] + 2 - y0), max(0, hint[0] - zone_pad - x0):max(0, hint[2] + zone_pad - x0)] = True
            diff = diff * zone
        peak = float(diff.max())
        if peak < 24:
            raise FooterBlocker(f'В окне {win} нет контрастной подписи (максимум отличия от фона {peak:.0f})')
        ink = diff > max(10.0, flp.INK_THRESHOLD * peak)
        rows = np.nonzero(ink.any(axis=1))[0]
        cols = np.nonzero(ink.any(axis=0))[0]
        if harmonic:
            return win, bg, diff, ink, peak, bg_model
        if cols[0] == 0 or cols[-1] == ink.shape[1] - 1:
            raise FooterBlocker(f'Чернила упираются в левый/правый край окна {win}: рядом другой рисунок')
        if rows[0] == 0 or rows[-1] == ink.shape[0] - 1:
            if pad_y < max_pad_y:
                pad_y += 3
                continue
            raise FooterBlocker(f'Чернила подписи упираются в верх/низ окна {win}: подложка не однородна')
        return win, bg, diff, ink, peak, bg_model


def fit_text(font_bytes: bytes, text: str, cn_em: float, max_w: float, cap_em: float, floor_em: float):
    size = min(cn_em * flp.EM_RATIO, cap_em)
    while True:
        mask, metrics = flp.rasterize(font_bytes, text, size)
        if metrics['ink_w'] / flp.SS <= max_w:
            return mask, metrics, size
        size -= 0.25
        if size < floor_em:
            raise FooterBlocker(f'«{text}» не помещается в {max_w} px при кегле не мельче {floor_em} px (нужна короткая форма)')


def render(region: np.ndarray, text: str, font_bytes: bytes, hint, align: str = 'center', max_w: int = 130, ornaments: bool = False,
           em_cap: float = 24.0, em_floor: float = 17.0, orn_gap=(6, 14), pad_x: int | None = None, fill: str = 'auto',
           outline: str = 'auto', art: bool = False, limits_abs=None, origin_x: int = 0, soft: float = flp.SOFT,
           sprite: str | None = None, origin_y: int = 0) -> tuple[np.ndarray, dict]:
    if sprite:
        # золотая карточка: старая подпись и листики известны по спрайтам группы, фон восстанавливается по текстуре (footer_sprite_render)
        import footer_sprite_render
        return footer_sprite_render.render(region, text, font_bytes, sprite, origin_x, origin_y, align=align, max_w=max_w, em_cap=em_cap, em_floor=em_floor,
                                           orn_gap=orn_gap, soft=soft)
    from PIL import Image, ImageFilter
    from scipy import ndimage
    SS = flp.SS
    text = text.strip()
    if not text or '\n' in text:
        raise FooterBlocker('Подпись — одна непустая строка')
    if align not in ('center', 'right', 'left'):
        raise FooterBlocker(f'align {align!r} не из center/right/left')
    height, width = region.shape[:2]
    limits = None
    if art and limits_abs:
        limits = (max(0, limits_abs[0] - origin_x), min(width, limits_abs[1] - origin_x))
    zone_pad = 30 if ornaments else 8
    win, bg, diff, ink, peak, bg_model = locate_ink(region, hint, pad_x if pad_x is not None else (zone_pad + 8 if art else zone_pad), limits=limits, harmonic=art, zone_pad=zone_pad)
    x0, y0, x1, y1 = win
    seed = int.from_bytes(hashlib.sha256(region[y0:y1, x0:x1].tobytes()).digest()[:4], 'little')  # от окна, а не от всей области: не зависит от размера rect
    orn_l = orn_r = None
    text_ink = ink
    if ornaments:
        orn_l, orn_r, _ = find_ornaments(ink, (hint[0] - x0, hint[2] - x0))
        text_ink = ink.copy()
        for orn in (orn_l, orn_r):
            if orn:
                text_ink[:, orn[0]:orn[1] + 1] = False
        if art:
            # подложка-рисунок: за пределами орнаментов и рамки OCR рисунок не считается чернилами (иначе он попадает в заливку)
            lo = min([hint[0] - x0 - 3] + [o[0] for o in (orn_l, orn_r) if o and o[1] < hint[0] - x0])
            hi = max([hint[2] - x0 + 3] + [o[1] + 1 for o in (orn_l, orn_r) if o and o[0] > hint[2] - x0])
            ink = ink.copy()
            ink[:, :max(0, lo)] = False
            ink[:, max(0, hi):] = False
            text_ink = text_ink & ink
        if not text_ink.any():
            raise FooterBlocker('После отделения орнаментов текста не осталось')
    ys, xs = np.nonzero(text_ink)
    ink_box = (x0 + int(xs.min()), y0 + int(ys.min()), x0 + int(xs.max()) + 1, y0 + int(ys.max()) + 1)
    cn_h = ink_box[3] - ink_box[1]
    hole = flp.grow(ink, flp.HOLE_GROW)
    resid = (region[y0:y1, x0:x1, :3].astype(float) - bg)[~hole]
    std = resid.std(axis=0) if resid.size else np.zeros(3)
    autoart = bool(resid.size and std.max() > ART_STD)
    use_laplace = fill == 'laplace' or (fill == 'auto' and (art or autoart))
    if use_laplace:
        part = region[y0:y1, x0:x1, :3].astype(float)
        filled = column_fill(part, hole) if art and bg_model == 'cols' else laplace_fill(part, hole)
        filled = filled + np.random.default_rng(seed).normal(0.0, 1.0, filled.shape) * np.minimum(std, 4.0)[None, None, :] * 0.5
        base = region.copy()
        soft_hole = np.clip(ndimage.gaussian_filter(hole.astype(float), 0.7), 0, 1)
        soft_hole[hole] = 1.0
        base[y0:y1, x0:x1, :3] = np.clip(np.round(part * (1 - soft_hole[..., None]) + filled * soft_hole[..., None]), 0, 255).astype(np.uint8)
    else:
        base = flp.erase_old(region, win, bg, ink, seed)  # bg — выбранная модель (строки или колонки), erase_old берёт её как есть
    rgb = flp.ink_color(region[y0:y1, x0:x1, :3].astype(float), bg, diff, text_ink)
    # рельефная подпись (золотые карты: светлая грань и тёмная кайма): заливка — ярчайшие пиксели старой подписи, контур — тёмные
    win_rgb = region[y0:y1, x0:x1, :3].astype(float)
    signed = (win_rgb - bg) @ np.array([0.299, 0.587, 0.114])
    s_ink = signed[text_ink]
    pos = float((s_ink > 6).mean())
    neg = float((s_ink < -6).mean())
    bevel = outline != 'off' and pos >= 0.2 and neg >= 0.2
    outline_rgb = None
    if bevel:
        order = np.argsort(s_ink)
        k = max(3, len(order) // 5)
        px = win_rgb[text_ink]
        rgb = px[order[-k:]].mean(axis=0)
        outline_rgb = px[order[:k]].mean(axis=0)
    cn_em = cn_h / 0.93
    mask, metrics, size = fit_text(font_bytes, text, cn_em, max_w, em_cap, em_floor)
    mpad = metrics['pad']
    ink_w = metrics['ink_w'] / SS
    cx = (ink_box[0] + ink_box[2]) / 2
    left_px = cx - ink_w / 2 if align == 'center' else (ink_box[2] - ink_w if align == 'right' else ink_box[0])
    cy = (ink_box[1] + ink_box[3]) / 2
    baseline_y = cy + metrics['cap_h'] / SS / 2
    origin_x = int(round(left_px * SS)) - mpad
    origin_y = int(round(baseline_y * SS)) - metrics['baseline']
    gx0, gy0 = origin_x // SS, origin_y // SS
    shift_x, shift_y = origin_x - gx0 * SS, origin_y - gy0 * SS
    gw = (mask.shape[1] + shift_x + SS - 1) // SS
    gh = (mask.shape[0] + shift_y + SS - 1) // SS
    canvas = np.zeros((gh * SS, gw * SS))
    canvas[shift_y:shift_y + mask.shape[0], shift_x:shift_x + mask.shape[1]] = mask
    cov = flp.downsample(canvas)
    ocov = None
    if bevel:
        radius = 1.0 * SS
        yy_, xx_ = np.mgrid[-int(radius):int(radius) + 1, -int(radius):int(radius) + 1]
        ocanvas = ndimage.binary_dilation(canvas > 0.5, structure=(xx_ ** 2 + yy_ ** 2) <= radius ** 2).astype(float)
        ocov = flp.downsample(ocanvas)
    if soft > 0:
        cov = np.asarray(Image.fromarray((np.clip(cov, 0, 1) * 255).round().astype(np.uint8), 'L').filter(ImageFilter.GaussianBlur(soft)), dtype=float) / 255.0
        if ocov is not None:
            ocov = np.asarray(Image.fromarray((np.clip(ocov, 0, 1) * 255).round().astype(np.uint8), 'L').filter(ImageFilter.GaussianBlur(soft)), dtype=float) / 255.0
    full_cov = ocov if ocov is not None else cov
    yy, xx = np.nonzero(full_cov > 0.02)
    visible = [int(xx.min()) + gx0, int(yy.min()) + gy0, int(xx.max()) + 1 + gx0, int(yy.max()) + 1 + gy0]
    if visible[0] < 3 or visible[2] > width - 3 or visible[1] < 1 or visible[3] > height - 1 or gy0 < -4 or gy0 + gh > height + 4:
        raise FooterBlocker(f'Подпись выходит за область правки: {visible} в {width}x{height}')
    # холст маски может выходить за область пустыми краями: обрезаем
    cx0, cy0 = max(0, -gx0), max(0, -gy0)
    cx1, cy1 = min(gw, width - gx0), min(gh, height - gy0)
    cov = cov[cy0:cy1, cx0:cx1]
    if ocov is not None:
        ocov = ocov[cy0:cy1, cx0:cx1]
    gx0, gy0, gw, gh = gx0 + cx0, gy0 + cy0, cx1 - cx0, cy1 - cy0
    out = base.copy()
    sub = out[gy0:gy0 + gh, gx0:gx0 + gw, :3].astype(float)
    if ocov is not None:
        sub = sub * (1 - ocov[..., None]) + outline_rgb[None, None, :] * ocov[..., None]
    out[gy0:gy0 + gh, gx0:gx0 + gw, :3] = np.clip(np.round(sub * (1 - cov[..., None]) + rgb[None, None, :] * cov[..., None]), 0, 255).astype(np.uint8)
    text_l, text_r = visible[0], visible[2]
    orn_info = []
    if ornaments and align == 'center' and (orn_l or orn_r):
        alpha_full = np.clip(diff / (0.6 * peak), 0.0, 1.0)
        old_text_l, old_text_r = ink_box[0] - x0, ink_box[2] - x0
        for side, orn in (('l', orn_l), ('r', orn_r)):
            mirrored = False
            if not orn:
                orn = orn_r if side == 'l' else orn_l
                mirrored = True
            first, last = orn
            gap_old = (old_text_l - last - 1) if side == 'l' else (first - old_text_r)
            if mirrored:
                gap_old = orn_gap[0]
            gap = int(min(max(gap_old, orn_gap[0]), orn_gap[1]))
            new_first = text_l - gap - (last - first + 1) if side == 'l' else text_r + gap
            ox0, ox1 = max(0, first - 1), min(ink.shape[1], last + 2)
            dx = new_first - (x0 + first)
            tx0 = x0 + ox0 + dx
            if mirrored:
                # недостающий орнамент: зеркальная копия найденного
                if tx0 < 4 or tx0 + (ox1 - ox0) > width - 4:
                    continue
                alpha = alpha_full[:, ox0:ox1, None][:, ::-1]
                src = region[y0:y1, x0 + ox0:x0 + ox1, :3].astype(float)[:, ::-1]
                dst = out[y0:y1, tx0:tx0 + (ox1 - ox0), :3].astype(float)
                out[y0:y1, tx0:tx0 + (ox1 - ox0), :3] = np.clip(np.round(dst * (1 - alpha) + src * alpha), 0, 255).astype(np.uint8)
                orn_info.append({'side': side, 'dx': int(dx), 'gap': gap, 'w': last - first + 1, 'mirrored': True})
                continue
            if tx0 < 4 or tx0 + (ox1 - ox0) > width - 4:
                raise FooterBlocker('Орнамент выходит за область правки')
            alpha = alpha_full[:, ox0:ox1, None]
            src = region[y0:y1, x0 + ox0:x0 + ox1, :3].astype(float)
            dst = out[y0:y1, tx0:tx0 + (ox1 - ox0), :3].astype(float)
            out[y0:y1, tx0:tx0 + (ox1 - ox0), :3] = np.clip(np.round(dst * (1 - alpha) + src * alpha), 0, 255).astype(np.uint8)
            orn_info.append({'side': side, 'dx': int(dx), 'gap': gap, 'w': last - first + 1})
    changed = np.any(out != region, axis=2)
    if not changed.any():
        raise FooterBlocker('Подпись не изменила ни одного пикселя')
    cys, cxs = np.nonzero(changed)
    return out, {'font_px': round(size, 2), 'cn_ink_height': cn_h, 'em_ratio': round(size / cn_em, 3), 'condense': 1.0, 'ink_box': visible,
                 'old_ink_box': list(ink_box), 'color': [int(round(v)) for v in rgb], 'peak_diff': round(peak, 1), 'ink_width': round(ink_w, 1),
                 'fill': 'laplace' if use_laplace else bg_model, 'art': art, 'bevel': bevel, 'ornaments': orn_info, 'window': list(win),
                 'changed_box': [int(cxs.min()), int(cys.min()), int(cxs.max()) + 1, int(cys.max()) + 1],
                 'old_ornaments': [list(o) if o else None for o in (orn_l, orn_r)]}
