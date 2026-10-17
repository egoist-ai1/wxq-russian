"""Подпись золотой карточки пакета 15 по спрайтам (footer_sprites): стирание старой подписи восстановлением фона и русская подпись между листиками.

Вызывается из footer_label_v2.render при params.sprite (имя группы в footer_sprites). Шаги:
1. Спрайты старой подписи (текст, левый и правый листик) ищутся на карточке сдвигом по цвету (footer_sprites.match).
2. Дыра = форма спрайтов (A > HOLE_A), расширенная на HOLE_GROW px. Только штрихи иероглифов и листики, не прямоугольник.
3. Фон в дыре восстанавливается по текстуре подложки: inpaint по образцам (patch_inpaint), источник — окно вне дыры. Однотонной заливки нет.
4. Русская подпись рисуется по центру старой, кегль не меньше em_floor; листики спрайта ставятся по бокам с зазором от текста: подпись
   между ними, а не поверх. Не хватает ширины — меньший кегль до порога, иначе отказ.
Всё чтение и запись — внутри окна SPRITE_WINDOW (координаты карточки), поэтому результат не зависит от размера rect.
Без стороннего кода: numpy, scipy, Pillow.
"""
from __future__ import annotations

import numpy as np

import footer_label_patch as flp
import footer_sprites as fs

FooterBlocker = flp.FooterBlocker
SPRITE_WINDOW = (104, 509, 296, 564)   # x0, y0, x1, y1 в координатах карточки: окно чтения/записи
HOLE_A = 0.06          # пиксели спрайта с покрытием выше — старая подпись
HOLE_GROW = 2          # расширение дыры, px (кайма сглаживания и тень)
VIS_A = 0.3            # видимая часть спрайта: покрытие выше
MATCH_MAX_COST = 16.0  # средний |RGB - спрайт| по ядру, выше — спрайт не найден
FILL = 'harmonic'      # 'harmonic' | 'patch'
GROW_REACH = 5         # на сколько px дыра может дорасти до остатков подписи
STRONG_DIFF = 24.0     # сильные отклонения рядом с дырой берём без связности (остаток штриха отделён зазором)
STRONG_REACH = 5
GROW_PASSES = 4       # проходов дорастания дыры
GROW_PASS_MAX = 140   # больше пикселей за проход - это уже рисунок плиты, а не остаток штриха
GROW_DIFF = 20.0      # отличие картинки от заливки (RGB, максимум по каналам), с которого пиксель считается остатком подписи
PATCH = 7              # размер образца, px
LOCALITY = 0.03        # штраф за удалённость образца (на квадрат расстояния), чтобы брать фон поблизости
MARGIN = 4             # поле от края окна до листиков, px


def harmonic_fill(img: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """Гладкое восстановление дыры по её границе (уравнение Лапласа, точное решение разреженной системой). Дыра тонкая — шва нет."""
    from scipy import sparse
    from scipy.sparse.linalg import spsolve
    h, w = hole.shape
    idx = -np.ones((h, w), dtype=np.int64)
    ys, xs = np.nonzero(hole)
    idx[ys, xs] = np.arange(len(ys))
    n = len(ys)
    rows, cols, vals = [], [], []
    rhs = np.zeros((n, 3))
    for k, (y, x) in enumerate(zip(ys, xs)):
        cnt = 0
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            yy, xx = y + dy, x + dx
            if not (0 <= yy < h and 0 <= xx < w):
                continue
            cnt += 1
            j = idx[yy, xx]
            if j >= 0:
                rows.append(k); cols.append(j); vals.append(-1.0)
            else:
                rhs[k] += img[yy, xx, :3]
        rows.append(k); cols.append(k); vals.append(float(cnt))
    a = sparse.csr_matrix((vals, (rows, cols)), shape=(n, n))
    out = img.astype(float).copy()
    for c in range(3):
        out[ys, xs, c] = spsolve(a.tocsc(), rhs[:, c])
    return out


def grow_to_ink(img: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """Дорастание в несколько проходов: на карточке подпись шире медианного спрайта на пару px, остаток штриха может лежать дальше GROW_REACH.

    Первый проход обязателен; следующие (до GROW_PASSES) принимаются, пока каждый добавляет немного пикселей (остаток штриха, а не рисунок подложки)."""
    current = _grow_once(img, hole)
    for _ in range(GROW_PASSES - 1):
        try:
            nxt = _grow_once(img, current, strict=False)
        except FooterBlocker:
            break
        from scipy import ndimage
        added = int((nxt & ~ndimage.binary_dilation(current, structure=np.ones((3, 3), bool))).sum())   # новое за пределами обычной каймы в 1 px
        if added <= 0 or added > GROW_PASS_MAX:
            break
        current = nxt
    return current


def _grow_once(img: np.ndarray, hole: np.ndarray, strict: bool = True) -> np.ndarray:
    """Один проход. Дыра по спрайту — среднее по карточкам: на отдельной карточке штрихи старой подписи выходят за неё.

    Опорная заливка считается по более широкой дыре (границу берём за остатками подписи); пиксели вокруг дыры, заметно отличающиеся от
    опоры (светлая грань, тёмная кайма), добавляются к дыре. Дорастание ограничено GROW_REACH px: рисунок подложки дальше не трогаем."""
    from scipy import ndimage
    ring = np.ones((3, 3), bool)
    wide = ndimage.binary_dilation(hole, structure=ring, iterations=GROW_REACH + 2)
    if strict and (wide[:2].any() or wide[:, :2].any() or wide[:, -2:].any()):
        raise FooterBlocker('Дыра старой подписи упирается в край окна')
    ref = harmonic_fill(img, wide)
    diff = np.abs(img - ref).max(axis=2)
    limit = ndimage.binary_dilation(hole, structure=ring, iterations=GROW_REACH)
    ink = (diff > GROW_DIFF) & limit
    # только то, что связано с дырой: остатки штрихов, а не случайные пиксели рисунка
    labels, count = ndimage.label(ndimage.binary_dilation(ink | hole, structure=ring), structure=ring)   # со вздутием на 1 px: остаток штриха отделён зазором в 1 px
    keep = np.isin(labels, np.unique(labels[hole & (labels > 0)]))
    strong = (diff > STRONG_DIFF) & ndimage.binary_dilation(hole, structure=ring, iterations=STRONG_REACH)   # одиночный тёмный остаток штриха рядом с дырой
    return ndimage.binary_dilation(hole | (ink & keep) | strong, structure=ring)


def patch_inpaint(img: np.ndarray, hole: np.ndarray, src_ok: np.ndarray) -> np.ndarray:
    """Восстановление фона в дыре по образцам (Criminisi): край дыры заполняется образцом, найденным по известным пикселям вокруг.

    img — float HxWx3; hole — что восстанавливаем; src_ok — откуда можно брать образцы целиком (образец целиком в src_ok). Детерминировано."""
    from numpy.lib.stride_tricks import sliding_window_view
    from scipy import ndimage
    h, w = hole.shape
    p = PATCH
    r = p // 2
    img = img.astype(np.float32).copy()
    known = ~hole
    img[hole] = 0.0
    conf = known.astype(np.float32)
    windows = sliding_window_view(img.copy(), (p, p, 3))[:, :, 0]            # (h-p+1, w-p+1, p, p, 3), образцы из ИСХОДНОЙ картинки
    # образцы берём из исходных пикселей: дыра в них не попадает (src_ok), поэтому копия с нулями безопасна
    valid = sliding_window_view(src_ok, (p, p)).all(axis=(2, 3))
    corners = np.argwhere(valid)
    if len(corners) < 20:
        raise FooterBlocker('Мало фона вокруг подписи для восстановления текстуры')
    sources = windows[valid].astype(np.float32)                              # (N, p, p, 3)
    centers = corners.astype(np.float32) + r
    weights = np.array([0.299, 0.587, 0.114], dtype=np.float32)
    cross = ndimage.generate_binary_structure(2, 1)
    remaining = hole.copy()
    guard = 0
    while remaining.any():
        guard += 1
        if guard > 4000:
            raise FooterBlocker('Восстановление фона не сошлось')
        front = remaining & ndimage.binary_dilation(known, structure=cross)
        if not front.any():
            raise FooterBlocker('Восстановление фона: нет края дыры')
        gray = img @ weights
        gy, gx = np.gradient(gray)
        valid_g = ndimage.binary_erosion(known, structure=np.ones((3, 3), bool))
        gxk, gyk = gx * valid_g, gy * valid_g
        norm = ndimage.gaussian_filter(valid_g.astype(np.float32), 1.5) + 1e-6
        gxs, gys = ndimage.gaussian_filter(gxk, 1.5) / norm, ndimage.gaussian_filter(gyk, 1.5) / norm
        ky, kx = np.gradient(ndimage.gaussian_filter(known.astype(np.float32), 1.5))
        nlen = np.hypot(kx, ky) + 1e-6
        data = np.abs(-gys * kx / nlen + gxs * ky / nlen) / 255.0              # |изофота . нормаль|
        confidence = ndimage.uniform_filter(conf, size=p, mode='nearest')
        priority = np.where(front, confidence * (data + 0.01), -1.0)
        py, px = np.unravel_index(int(np.argmax(priority)), priority.shape)
        cy, cx = int(np.clip(py, r, h - 1 - r)), int(np.clip(px, r, w - 1 - r))
        ys, xs = slice(cy - r, cy + r + 1), slice(cx - r, cx + r + 1)
        target = img[ys, xs]
        kmask = known[ys, xs]
        ssd = (((sources - target[None]) ** 2) * kmask[None, :, :, None]).sum(axis=(1, 2, 3))
        ssd = ssd + LOCALITY * ((centers[:, 0] - cy) ** 2 + (centers[:, 1] - cx) ** 2) * max(1, int(kmask.sum()))
        best = int(np.argmin(ssd))
        fill = ~kmask
        patch = img[ys, xs]
        chosen = sources[best]
        if kmask.any():
            # образец подгоняется по средней яркости к известной части: нет ступенек на стыке
            chosen = chosen + (target[kmask].mean(axis=0) - chosen[kmask].mean(axis=0))[None, None, :]
        patch[fill] = np.clip(chosen[fill], 0, 255)
        img[ys, xs] = patch
        conf_patch = conf[ys, xs]
        conf_patch[fill] = float(confidence[py, px])
        conf[ys, xs] = conf_patch
        known[ys, xs] = True
        remaining[ys, xs] = False
    return img


def render(region: np.ndarray, text: str, font_bytes: bytes, sprite: str, origin_x: int, origin_y: int, align: str = 'center', max_w: int = 140,
           em_cap: float = 24.0, em_floor: float = 17.0, orn_gap=(6, 14), soft: float = flp.SOFT) -> tuple[np.ndarray, dict]:
    from PIL import Image, ImageFilter
    from scipy import ndimage
    SS = flp.SS
    text = text.strip()
    if not text or '\n' in text:
        raise FooterBlocker('Подпись — одна непустая строка')
    if align != 'center':
        raise FooterBlocker('Спрайтовая подпись — только по центру')
    sp = fs.load(sprite)
    if not sp:
        raise FooterBlocker(f'Нет спрайтов группы {sprite!r}')
    height, width = region.shape[:2]
    ax0, ay0, ax1, ay1 = SPRITE_WINDOW
    wx0, wy0, wx1, wy1 = ax0 - origin_x, ay0 - origin_y, ax1 - origin_x, ay1 - origin_y
    if wx0 < 0 or wy0 < 0 or wx1 > width or wy1 > height:
        raise FooterBlocker(f'Область правки {width}x{height} меньше окна подписи {SPRITE_WINDOW}')
    win = region[wy0:wy1, wx0:wx1, :3]
    ww, wh = wx1 - wx0, wy1 - wy0
    placed = {}
    for name in fs.COMPONENTS:
        x, y, cost, margin = fs.match(win, sp[name], (ax0, ay0))
        placed[name] = (x, y, cost, margin)
    if placed['text'][2] > MATCH_MAX_COST or placed['text'][3] < 3.0:
        raise FooterBlocker(f'Старая подпись не найдена по спрайту (отклонение {placed["text"][2]:.1f}, запас {placed["text"][3]:.1f})')
    for name in ('left', 'right'):
        if placed[name][2] > MATCH_MAX_COST or placed[name][3] < 2.0:
            # листик не найден: берём сдвиг текста
            dx, dy = placed['text'][0] - (sp['text']['x'] - ax0), placed['text'][1] - (sp['text']['y'] - ay0)
            placed[name] = (sp[name]['x'] - ax0 + dx, sp[name]['y'] - ay0 + dy, placed[name][2], -1.0)
    # дыра: форма спрайтов
    hole = np.zeros((wh, ww), dtype=bool)
    vis = {}
    for name in fs.COMPONENTS:
        x, y = placed[name][:2]
        a = sp[name]['a']
        h, w = a.shape
        if x < 0 or y < 0 or x + w > ww or y + h > wh:
            raise FooterBlocker('Спрайт старой подписи выходит за окно')
        # листик восстанавливаем целиком (внутри он залит); у иероглифов — только штрихи: просветы внутри знаков — целая подложка
        hole[y:y + h, x:x + w] |= ndimage.binary_closing(np.pad(a > HOLE_A, 4), structure=np.ones((3, 3), bool), iterations=3)[4:-4, 4:-4]
        ys, xs = np.nonzero(a > VIS_A)
        vis[name] = (x + int(xs.min()), y + int(ys.min()), x + int(xs.max()) + 1, y + int(ys.max()) + 1)
    hole = ndimage.binary_dilation(hole, structure=np.ones((3, 3), bool), iterations=HOLE_GROW)
    if hole[:3].any() or hole[:, :3].any() or hole[:, -3:].any():
        raise FooterBlocker('Дыра старой подписи упирается в край окна')
    hole = grow_to_ink(win.astype(float), hole)
    if hole[:3].any() or hole[:, :3].any() or hole[:, -3:].any():
        raise FooterBlocker('Дыра старой подписи упирается в край окна')
    if FILL == 'harmonic':
        filled = harmonic_fill(win.astype(float), hole)
    else:
        src_ok = ~ndimage.binary_dilation(hole, structure=np.ones((3, 3), bool), iterations=3)
        filled = patch_inpaint(win.astype(float), hole, src_ok)
    base = region.copy()
    inside = base[wy0:wy1, wx0:wx1, :3].astype(float)
    inside[hole] = filled[hole]
    base[wy0:wy1, wx0:wx1, :3] = np.clip(np.round(inside), 0, 255).astype(np.uint8)
    # цвет подписи: ярчайшая и тёмная часть спрайта (рельеф: светлая грань и тёмная кайма)
    ta, trgb = sp['text']['a'], sp['text']['rgb']
    core = ta > 0.8
    luma = trgb @ np.array([0.299, 0.587, 0.114])
    order = np.argsort(luma[core])
    k = max(3, len(order) // 5)
    px = trgb[core]
    mid = order[int(len(order) * 0.25):int(len(order) * 0.6)]      # грань: светлое золото без белых бликов
    rgb = px[mid].mean(axis=0)
    outline_rgb = px[order[:k]].mean(axis=0)
    # кегль и ширина: подпись между листиками
    tbox = vis['text']
    cn_h = tbox[3] - tbox[1]
    cn_em = cn_h / 0.93
    cx = (tbox[0] + tbox[2]) / 2
    lw = vis['left'][2] - vis['left'][0]
    rw = vis['right'][2] - vis['right'][0]
    half = min(cx - MARGIN - lw - orn_gap[0], ww - MARGIN - cx - rw - orn_gap[0]) - 1   # запас на каёмку и сглаживание
    if half < 20:
        raise FooterBlocker('Слишком мало места для подписи между листиками')
    import footer_label_v2 as v2
    mask, metrics, size = v2.fit_text(font_bytes, text, cn_em, min(max_w, 2 * half), em_cap, em_floor)
    mpad = metrics['pad']
    ink_w = metrics['ink_w'] / SS
    left_px = cx - ink_w / 2
    cy = (tbox[1] + tbox[3]) / 2
    baseline_y = cy + metrics['cap_h'] / SS / 2
    origin_gx = int(round(left_px * SS)) - mpad
    origin_gy = int(round(baseline_y * SS)) - metrics['baseline']
    gx0, gy0 = origin_gx // SS, origin_gy // SS
    shift_x, shift_y = origin_gx - gx0 * SS, origin_gy - gy0 * SS
    gw = (mask.shape[1] + shift_x + SS - 1) // SS
    gh = (mask.shape[0] + shift_y + SS - 1) // SS
    canvas = np.zeros((gh * SS, gw * SS))
    canvas[shift_y:shift_y + mask.shape[0], shift_x:shift_x + mask.shape[1]] = mask
    cov = flp.downsample(canvas)
    radius = 1.0 * SS
    yy_, xx_ = np.mgrid[-int(radius):int(radius) + 1, -int(radius):int(radius) + 1]
    ocov = flp.downsample(ndimage.binary_dilation(canvas > 0.5, structure=(xx_ ** 2 + yy_ ** 2) <= radius ** 2).astype(float))
    if soft > 0:
        cov = np.asarray(Image.fromarray((np.clip(cov, 0, 1) * 255).round().astype(np.uint8), 'L').filter(ImageFilter.GaussianBlur(soft)), dtype=float) / 255.0
        ocov = np.asarray(Image.fromarray((np.clip(ocov, 0, 1) * 255).round().astype(np.uint8), 'L').filter(ImageFilter.GaussianBlur(soft)), dtype=float) / 255.0
    yy, xx = np.nonzero(ocov > 0.02)
    visible = [int(xx.min()) + gx0, int(yy.min()) + gy0, int(xx.max()) + 1 + gx0, int(yy.max()) + 1 + gy0]
    # листики: новые места по бокам подписи
    gap_old_l = tbox[0] - vis['left'][2]
    gap_old_r = vis['right'][0] - tbox[2]
    # новые листики: левый из спрайта, правый — его зеркало (рисунок симметричен; правый спрайт на среднем кадре обрезан слабым контрастом)
    paste = {'left': {'a': sp['left']['a'], 'rgb': sp['left']['rgb']},
             'right': {'a': sp['left']['a'][:, ::-1], 'rgb': sp['left']['rgb'][:, ::-1]}}
    pbox = {}
    for name, spr in paste.items():
        ys_, xs_ = np.nonzero(spr['a'] > VIS_A)
        pbox[name] = (int(xs_.min()), int(ys_.min()), int(xs_.max()) + 1, int(ys_.max()) + 1)
    orn_new = {}
    for name, gap_old in (('left', gap_old_l), ('right', gap_old_r)):
        gap = int(min(max(gap_old, orn_gap[0]), orn_gap[1]))
        w_leaf = pbox[name][2] - pbox[name][0]
        first = visible[0] - gap - w_leaf if name == 'left' else visible[2] + gap
        if first < MARGIN or first + w_leaf > ww - MARGIN:
            gap = orn_gap[0]
            first = visible[0] - gap - w_leaf if name == 'left' else visible[2] + gap
        if first < MARGIN or first + w_leaf > ww - MARGIN:
            raise FooterBlocker('Листик не помещается в окно: подпись слишком широка')
        orn_new[name] = (first - pbox[name][0], placed['left'][1], first, gap)
    if visible[0] < MARGIN or visible[2] > ww - MARGIN or visible[1] < 1 or visible[3] > wh - 1:
        raise FooterBlocker(f'Подпись выходит за окно: {visible} в {ww}x{wh}')
    out_win = base[wy0:wy1, wx0:wx1, :3].astype(float)
    cut =(slice(max(0, -gy0), min(gh, wh - gy0)), slice(max(0, -gx0), min(gw, ww - gx0)))
    cov_c, ocov_c = cov[cut], ocov[cut]
    gy1, gx1 = max(gy0, 0), max(gx0, 0)
    sub = out_win[gy1:gy1 + cov_c.shape[0], gx1:gx1 + cov_c.shape[1]]
    sub = sub * (1 - ocov_c[..., None]) + outline_rgb[None, None, :] * ocov_c[..., None]
    sub = sub * (1 - cov_c[..., None]) + rgb[None, None, :] * cov_c[..., None]
    out_win[gy1:gy1 + cov_c.shape[0], gx1:gx1 + cov_c.shape[1]] = sub
    orn_info = {}
    for name, (nx, ny, first, gap) in orn_new.items():
        a = paste[name]['a'][..., None]
        h, w = a.shape[:2]
        dst = out_win[ny:ny + h, nx:nx + w]
        out_win[ny:ny + h, nx:nx + w] = dst * (1 - a) + paste[name]['rgb'] * a
        orn_info[name] = {'x0': int(first), 'x1': int(first + pbox[name][2] - pbox[name][0]), 'y0': int(ny + pbox[name][1]), 'y1': int(ny + pbox[name][3]), 'gap': gap}
    out = base.copy()
    out[wy0:wy1, wx0:wx1, :3] = np.clip(np.round(out_win), 0, 255).astype(np.uint8)
    changed = np.any(out != region, axis=2)
    if not changed.any():
        raise FooterBlocker('Подпись не изменила ни одного пикселя')
    cys, cxs = np.nonzero(changed)
    text_box = [visible[0] + wx0, visible[1] + wy0, visible[2] + wx0, visible[3] + wy0]
    old_text = [tbox[0] + wx0, tbox[1] + wy0, tbox[2] + wx0, tbox[3] + wy0]
    return out, {'font_px': round(size, 2), 'cn_ink_height': cn_h, 'em_ratio': round(size / cn_em, 3), 'condense': 1.0, 'ink_box': text_box,
                 'old_ink_box': old_text, 'color': [int(round(v)) for v in rgb], 'peak_diff': 0.0, 'ink_width': round(ink_w, 1),
                 'fill': 'patch', 'art': True, 'bevel': True, 'window': [wx0, wy0, wx1, wy1],
                 'ornaments': [{'side': side[0], 'box': [o['x0'] + wx0, o['y0'] + wy0, o['x1'] + wx0, o['y1'] + wy0], 'gap': o['gap']} for side, o in orn_info.items()],
                 'sprite_cost': {n: round(placed[n][2], 1) for n in fs.COMPONENTS}, 'hole_px': int(hole.sum()),
                 'changed_box': [int(cxs.min()), int(cys.min()), int(cxs.max()) + 1, int(cys.max()) + 1], 'old_ornaments': None}
