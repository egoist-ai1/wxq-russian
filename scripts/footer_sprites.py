"""Спрайты старой подписи и орнаментов золотых карточек пакета 15 (региональные подписи 建木, 河洛, 逐鹿, ...).

Зачем. Старая подпись и листики по бокам нарисованы игрой одним и тем же спрайтом на каждой золотой карточке группы, только подложка разная.
На ровной подложке спрайт выделяется надёжно (отличие от фона), на сложной (рисунок, блики, тёмные края) — нет: отличие от фона захватывает
рисунок, и закраска получается пятном. Поэтому форму и цвет старой подписи берём не с самой карточки, а из спрайта группы, собранного по
всем золотым карточкам группы: у большинства подложка в зоне подписи ровная, рисунок у каждой свой, поэтому поэлементная МЕДИАНА по карточкам
даёт чистый спрайт на ровной подложке, а дальше он выделяется обычным способом (отличие от фона).

Что строит `build`: для каждой группы с орнаментами — медианную «карточку» золотых карточек в окне (X0..X1, Y0..Y1; координаты карточки),
выделяет на ней левый листик, текст и правый листик. Каждый — PNG RGBA: A = коэффициент покрытия (0..1), RGB = цвет спрайта;
meta.json хранит положение (x, y) на карточке и число карточек.

Что делает `match` при рендере: на сложной подложке ищет сдвиг спрайта (dx, dy) по минимуму отклонения цвета в «ядре» спрайта (A >= CORE).
Стороннего кода нет: Pillow, numpy, scipy.

Команда: python footer_sprites.py build --out scripts/footer_sprites
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
for _path in (str(PROJECT), str(PROJECT / 'scripts')):
    if _path not in sys.path:
        sys.path.insert(0, _path)

SPRITE_DIR = Path(__file__).resolve().parent / 'footer_sprites'
PAD = 2                 # поля вокруг чернил спрайта: кайма сглаживания
CORE = 0.6              # ядро спрайта для поиска сдвига: A не меньше
SEARCH_DX, SEARCH_DY = 3, 4
COMPONENTS = ('text', 'left', 'right')
WINDOW = (140, 514, 262, 562)   # X0, Y0, X1, Y1: окно спрайтов в координатах карточки
_cache: dict = {}


def slug(en: str) -> str:
    return en.lower().replace(' ', '-')


def load(group_slug: str) -> dict | None:
    """Спрайты группы: {'text': {'a': HxW, 'rgb': HxWx3, 'x': .., 'y': ..}, 'left': ..., 'right': ...} либо None."""
    if group_slug in _cache:
        return _cache[group_slug]
    meta_path = SPRITE_DIR / 'meta.json'
    result = None
    if meta_path.exists():
        meta = json.loads(meta_path.read_text('utf-8')).get(group_slug)
        if meta:
            from PIL import Image
            result = {}
            for name in COMPONENTS:
                png = np.asarray(Image.open(SPRITE_DIR / meta[name]['file']).convert('RGBA'))
                result[name] = {'a': png[..., 3].astype(float) / 255.0, 'rgb': png[..., :3].astype(float), 'x': meta[name]['x'], 'y': meta[name]['y']}
    _cache[group_slug] = result
    return result


def match(img: np.ndarray, sprite: dict, origin: tuple[int, int], dx_range=SEARCH_DX, dy_range=SEARCH_DY) -> tuple[int, int, float, float]:
    """Лучший сдвиг спрайта на картинке img (origin=(x, y) области на карточке): (x, y, cost, margin).

    x, y — левый верхний угол спрайта в координатах области; cost — среднее |RGB - спрайт| по ядру; margin — запас до лучшего несоседнего
    сдвига (для отсева неоднозначных совпадений)."""
    a, rgb = sprite['a'], sprite['rgb']
    core = a >= CORE
    if core.sum() < 6:
        core = a >= 0.3
    h, w = a.shape
    best = []
    for dy in range(-dy_range, dy_range + 1):
        for dx in range(-dx_range, dx_range + 1):
            x, y = sprite['x'] - origin[0] + dx, sprite['y'] - origin[1] + dy
            if x < 0 or y < 0 or y + h > img.shape[0] or x + w > img.shape[1]:
                continue
            patch = img[y:y + h, x:x + w, :3].astype(float)
            cost = float(np.abs(patch - rgb)[core].mean())
            best.append((cost, x, y, dx, dy))
    if not best:
        return 0, 0, 1e9, 0.0
    best.sort()
    cost, x, y, dx, dy = best[0]
    far = [c for c, _, _, ddx, ddy in best if abs(ddx - dx) > 1 or abs(ddy - dy) > 1]
    return x, y, cost, (far[0] - cost) if far else 99.0


def is_gold(img: np.ndarray) -> bool:
    """Золотая карточка: полоса рамки внизу (y 556..562) золотого тона и золотая подложка слева от подписи (не тёмная одноцветная).

    Спрайт старой подписи зависит от цвета карточки: на тёмных и цветных карточках он другой."""
    import colorsys
    border = img[556:562, 100:300, :3].astype(float).reshape(-1, 3).mean(axis=0) / 255
    hue, sat, _ = colorsys.rgb_to_hsv(*border)
    if not (30 <= hue * 360 <= 50 and sat > 0.3):
        return False
    strip = img[526:552:2, 105:150:3, :3].astype(float).reshape(-1, 3) / 255
    hsv = np.array([colorsys.rgb_to_hsv(*p) for p in strip])
    gold = (hsv[:, 0] * 360 > 28) & (hsv[:, 0] * 360 < 52) & (hsv[:, 1] > 0.4)
    return float(gold.mean()) >= 0.3


def split_columns(mask: np.ndarray, min_gap: int = 3, min_width: int = 4):
    """Группы колонок маски (щели меньше min_gap склеиваются): [(c0, c1)]."""
    cols = mask.any(axis=0)
    groups, start, last = [], None, None
    for x, value in enumerate(cols):
        if not value:
            continue
        if start is None:
            start = x
        elif x - last - 1 >= min_gap:
            groups.append((start, last + 1))
            start = x
        last = x
    if start is not None:
        groups.append((start, last + 1))
    return [g for g in groups if g[1] - g[0] >= min_width]


LEAF_MAX_W, LEAF_MAX_H = 16, 12


def split_components(strong: np.ndarray):
    """[(левый листик), (текст), (правый листик)] как пары колонок [c0, c1) либо None.

    По «сильной» маске: листик — узкая невысокая крайняя группа колонок; границы с текстом — середины щелей между группами."""
    groups = split_columns(strong, 2, 3)
    if len(groups) < 3:
        return None

    def leaf(group):
        rows = np.nonzero(strong[:, group[0]:group[1]].any(axis=1))[0]
        return group[1] - group[0] <= LEAF_MAX_W and rows.max() - rows.min() + 1 <= LEAF_MAX_H
    if not (leaf(groups[0]) and leaf(groups[-1])):
        return None
    left, right = groups[0], groups[-1]
    text = (groups[1][0], groups[-2][1])
    mid_l = (left[1] + text[0]) // 2
    mid_r = (text[1] + right[0] + 1) // 2
    return (left[0], mid_l), (mid_l, mid_r), (mid_r, right[1])


def _hull(mask: np.ndarray) -> np.ndarray:
    """Выпуклая оболочка маски (листик — капля: контур открыт, внутри цвет подложки, fill_holes не замыкает)."""
    from PIL import Image, ImageDraw
    from scipy.spatial import ConvexHull
    ys, xs = np.nonzero(mask)
    pts = np.stack([xs, ys], axis=1).astype(float)
    # углы пикселей, чтобы оболочка включала пиксели целиком
    pts = np.concatenate([pts + d for d in ((-0.5, -0.5), (0.5, -0.5), (-0.5, 0.5), (0.5, 0.5))])
    hull = ConvexHull(pts)
    poly = [tuple(pts[i] + 0.5) for i in hull.vertices]
    img = Image.new('L', (mask.shape[1], mask.shape[0]), 0)
    ImageDraw.Draw(img).polygon(poly, fill=255)
    return np.asarray(img) > 127


def median_card(images: list[np.ndarray]) -> np.ndarray:
    x0, y0, x1, y1 = WINDOW
    return np.median(np.stack([im[y0:y1, x0:x1, :3].astype(float) for im in images]), axis=0)


def extract(med: np.ndarray):
    """Спрайты на медианной карточке: (A HxW, сильная маска чернил) либо ValueError. Фон — построчная интерполяция между краями окна."""
    from scipy import ndimage
    ring = 3
    left = med[:, :ring].mean(axis=1)
    right = med[:, -ring:].mean(axis=1)
    t = ((np.arange(med.shape[1]) + 0.5) / med.shape[1])[None, :, None]
    bg = left[:, None, :] * (1 - t) + right[:, None, :] * t
    diff = np.abs(med - bg).max(axis=2)
    peak = float(diff.max())
    if peak < 40:
        raise ValueError(f'контраст спрайта мал: {peak:.0f}')
    mask = diff > max(12.0, 0.2 * peak)
    # главная полоса по строкам: подпись, а не рисунок в углах
    rows = mask.sum(axis=1)
    ys = np.nonzero(rows > 0.25 * rows.max())[0]
    keep = np.zeros_like(mask)
    keep[max(0, ys.min() - 3):ys.max() + 4] = True
    strong = (diff > max(20.0, 0.4 * peak)) & keep
    labels, count = ndimage.label(strong, structure=np.ones((3, 3), bool))
    if count == 0:
        raise ValueError('нет чернил')
    sizes = ndimage.sum(strong, labels, range(1, count + 1))
    strong &= np.isin(labels, [k + 1 for k, s in enumerate(sizes) if s >= 6])
    alpha = np.clip(diff / (0.6 * peak), 0.0, 1.0) * keep
    return alpha, strong


def build(out_dir: Path) -> int:
    import atlas_text_patch
    import pkg15_footers as pf
    import pkg15_full as full
    import wxq_install
    from PIL import Image
    allc = full.cards()
    by = {}
    for c in allc:
        if full.GROUPS[c['group']]['ornaments']:
            by.setdefault(c['group'], []).append(c['id'])
    game = wxq_install.require_install_structure(wxq_install.detect_active_install(), full=True)
    streams = pf.read_streams(game, [i for ids in by.values() for i in ids])
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {}
    order = ('left', 'text', 'right')
    X0, Y0, X1, Y1 = WINDOW
    for group, ids in sorted(by.items()):
        name = slug(full.GROUPS[group]['en'])
        images = []
        for i in ids:
            img = atlas_text_patch.decode_upright(streams[i], pf.W, pf.H)
            if is_gold(img):
                images.append(img)
        if len(images) < 5:
            print(group, 'мало золотых карточек:', len(images))
            continue
        med = median_card(images)
        try:
            alpha, strong = extract(med)
        except ValueError as error:
            print(group, 'спрайт не выделен:', error)
            continue
        cols = split_components(strong)
        if cols is None:
            print(group, 'спрайт не разделился на левый листик, текст и правый листик:', split_columns(strong, 2, 3))
            continue
        meta[name] = {}
        for comp, (c0, c1) in zip(order, cols):
            rows = np.nonzero(strong[:, c0:c1].any(axis=1))[0]
            r0, r1 = max(0, rows.min() - PAD - 1), min(Y1 - Y0, rows.max() + 1 + PAD + 1)
            c0p, c1p = max(0, c0 - PAD), min(X1 - X0, c1 + PAD)
            from scipy import ndimage
            a = alpha[r0:r1, c0p:c1p].copy()
            # только у чернил: дальше от сильной маски покрытие — шум градиента подложки, не часть спрайта
            near = ndimage.binary_dilation(strong[r0:r1, c0p:c1p], structure=np.ones((3, 3), bool), iterations=3)
            a = a * near
            if comp != 'text':
                # листик залит цветом подложки: внутри контраст с фоном почти нулевой, поэтому по контуру восстанавливаем сплошную заливку
                a = np.maximum(a, _hull(a > 0.25).astype(float))
            png = np.dstack([np.clip(np.round(med[r0:r1, c0p:c1p]), 0, 255), np.clip(np.round(a * 255), 0, 255)]).astype(np.uint8)
            fname = f'{name}-{comp}.png'
            Image.fromarray(png, 'RGBA').save(out_dir / fname)
            meta[name][comp] = {'file': fname, 'x': int(X0 + c0p), 'y': int(Y0 + r0), 'size': [int(c1p - c0p), int(r1 - r0)], 'cards': len(images)}
        print(group, name, {k: v['size'] for k, v in meta[name].items()}, 'золотых карточек', len(images))
        Image.fromarray(np.clip(med, 0, 255).astype(np.uint8)).resize((med.shape[1] * 4, med.shape[0] * 4), Image.Resampling.NEAREST).save(out_dir / f'{name}-median.png')
    (out_dir / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1) + '\n', 'utf-8')
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('--out', type=Path, default=SPRITE_DIR)
    args = parser.parse_args(argv)
    return build(args.out)


if __name__ == '__main__':
    sys.exit(main())
