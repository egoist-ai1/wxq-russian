"""Пакет 15: конвейер «группа китайских подписей -> одна русская подпись -> перерисовка по всем текстурам группы».

Читает инвентарь (reports/2026-10-13 texture-inventory/INVENTORY.json) и игру ТОЛЬКО на чтение. Команды:
  render  - отрисовать группы (по умолчанию все из GROUPS), метрики в JSON, листы «до/после» (>= N карточек на группу);
  content - записать pilot-content (namespace=atlas, style=footer) для build_pilot_carrier.py.
Перевод подписи группы — из glossary (GROUPS: источник и форма). Рисование — footer_label_patch (стиль footer).
"""
from __future__ import annotations

import argparse
import collections
import colorsys
import json
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
for _path in (str(PROJECT), str(PROJECT / 'scripts')):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import atlas_text_patch  # noqa: E402
import build_pilot_carrier as bpc  # noqa: E402
import footer_label_patch as flp  # noqa: E402
import wxq_install  # noqa: E402
from game_inspect import QtsContainer  # noqa: E402

INVENTORY = PROJECT / 'reports/2026-10-13 texture-inventory/INVENTORY.json'
W, H = 404, 564
# Группа = китайская подпись (после склейки шума OCR) -> русская подпись по glossary/glossary.json (ru_canon, status approved).
GROUPS = {
    '技能': {'ru': 'Умение', 'en_ref': 'Skill', 'glossary': 't-99aea2f913'},
    '天赋': {'ru': 'Талант', 'en_ref': 'Talent', 'glossary': 't-866e2e6d67'},
}
MAX_W = 110          # наибольшая ширина русской подписи, px (по свободному месту между английскими подписями карточки)
RIGHT_FROM = 330     # китайская подпись правее этого x — выравнивание по правому краю


def inventory_group(label: str) -> list[dict]:
    data = json.loads(INVENTORY.read_text('utf-8'))
    return [x for x in data['textures_with_cjk'] if str(x.get('pkg')) == '15' and x.get('footer') == [label]]


def read_streams(game: Path, idents: list[str]) -> dict[str, bytes]:
    """Потоки текстур пакета 15, каждый контейнер открывается один раз."""
    want = {int(i, 16): i for i in idents}
    found: dict[str, bytes] = {}
    for path in bpc.data_files(game, '15'):
        with QtsContainer(path) as container:
            for number in [n for n in want if n in container.entries]:
                found[want[number]] = container.decode_entry(number)
    missing = set(idents) - set(found)
    if missing:
        raise SystemExit(f'Нет в prime: {sorted(missing)[:5]}')
    return found


def item_for(entry: dict, label: str, ru: str) -> dict:
    """Запись pilot-content: rect по границам блоков вокруг подписи; hint — рамка OCR в координатах rect."""
    line = [l for l in entry['lines'] if l['t'] == label][0]
    x0, y0, x1, y1 = line['box']
    align = 'right' if x0 >= RIGHT_FROM else 'center'
    if align == 'right':
        rx0, rx1 = x1 - MAX_W - 14, 400
    else:
        cx = (x0 + x1) // 2
        rx0, rx1 = cx - MAX_W // 2 - 14, cx + MAX_W // 2 + 14
    rect = flp.block_rect(rx0, y0 - 12, rx1, y1 + 12, W, H)
    hint = [x0 - rect[0], y0 - rect[1], x1 - rect[0], y1 - rect[1]]
    return {'id': f'pkg15-{entry["id"]}', 'namespace': 'atlas', 'resource': f'15/{entry["id"]}', 'key': 'footer', 'cn_source': label,
            'en_ref': GROUPS[label]['en_ref'], 'ru': ru, 'rect': rect, 'style': 'footer',
            'params': {'hint': hint, 'align': align, 'max_w': MAX_W}, 'where': f'пакет 15, группа {label}, подложка {entry["footer_crop_group"]}'}


def plate_stats(img: np.ndarray, rect: list[int]) -> dict:
    crop = img[rect[1]:rect[3], rect[0]:rect[2], :3].astype(float) / 255
    mean = crop.reshape(-1, 3).mean(axis=0)
    h, s, v = colorsys.rgb_to_hsv(*mean)
    return {'lum': float(v), 'sat': float(s), 'spread': float(crop.reshape(-1, 3).std(axis=0).mean())}


def pick_sample(rows: list[dict], count: int) -> list[dict]:
    """Выборка для листа: самые светлые, самые пёстрые/насыщенные, самые тёмные, по одной на подложку, остальное — равномерно."""
    chosen: dict[str, dict] = {}

    def take(seq, n):
        for row in seq:
            if len([1 for _ in chosen]) >= count or n <= 0:
                break
            if row['id'] not in chosen:
                chosen[row['id']] = row
                n -= 1
    take(sorted(rows, key=lambda r: -r['stats']['lum']), 8)
    take(sorted(rows, key=lambda r: -r['stats']['spread']), 8)
    take(sorted(rows, key=lambda r: -r['stats']['sat']), 6)
    take(sorted(rows, key=lambda r: r['stats']['lum']), 4)
    seen = set()
    for row in rows:
        if row['crop'] not in seen:
            seen.add(row['crop'])
            take([row], 1)
    step = max(1, len(rows) // max(1, count - len(chosen)))
    take(rows[::step], count)
    return list(chosen.values())[:count]


def sheet(rows: list[dict], path: Path, scale: int = 3, columns: int = 3) -> None:
    """Лист «до/после»: клетка — подпись и две полосы (оригинал сверху, результат снизу) на реальной подложке."""
    from PIL import Image, ImageDraw
    cells = []
    for row in rows:
        r = row['view']
        tile = []
        for key in ('before', 'after'):
            tile.append(Image.fromarray(row[key][r[1]:r[3], r[0]:r[2]], 'RGBA').resize(((r[2] - r[0]) * scale, (r[3] - r[1]) * scale), Image.Resampling.NEAREST))
        cells.append((row['label'], tile))
    cw, ch = cells[0][1][0].size
    gap, lab = 6, 13
    rows_n = -(-len(cells) // columns)
    out = Image.new('RGB', (columns * (cw + gap) + gap, rows_n * (2 * ch + lab + 2 * gap) + gap), (24, 8, 32))
    draw = ImageDraw.Draw(out)
    for index, (label, tile) in enumerate(cells):
        cx, cy = gap + (index % columns) * (cw + gap), gap + (index // columns) * (2 * ch + lab + 2 * gap)
        draw.text((cx, cy), label, fill=(230, 230, 240))
        for k, image in enumerate(tile):
            out.paste(image.convert('RGB'), (cx, cy + lab + k * (ch + 2)))
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path)


def render_group(game: Path, label: str, font: bytes) -> list[dict]:
    ru = GROUPS[label]['ru']
    entries = inventory_group(label)
    streams = read_streams(game, [e['id'] for e in entries])
    results = []
    for entry in entries:
        item = item_for(entry, label, ru)
        stream = streams[entry['id']]
        row = {'id': entry['id'], 'crop': entry['footer_crop_group'], 'item': item, 'ok': True}
        try:
            before = atlas_text_patch.decode_upright(stream, W, H)
            x0, y0, x1, y1 = item['rect']
            drawn, metrics = flp.render_footer(before[y0:y1, x0:x1], ru, font, **item['params'])
            after = before.copy()
            after[y0:y1, x0:x1] = drawn
            row.update({'metrics': metrics, 'stats': plate_stats(before, item['rect']), 'before': before, 'after': after})
            vx = (135, 270) if item['params']['align'] == 'center' else (250, 404)
            row['view'] = [vx[0], 500, vx[1], 564]
            row['label'] = f'{GROUPS[label]["en_ref"]} {entry["id"]} plate {entry["footer_crop_group"]} {item["params"]["align"]}'
        except atlas_text_patch.AtlasBlocker as error:
            row.update({'ok': False, 'error': str(error)})
        results.append(row)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('render')
    r.add_argument('--out', type=Path, required=True)
    r.add_argument('--groups', nargs='*', default=list(GROUPS))
    r.add_argument('--sample', type=int, default=48)
    c = sub.add_parser('content')
    c.add_argument('--out', type=Path, required=True)
    c.add_argument('--groups', nargs='*', default=list(GROUPS))
    c.add_argument('--limit', type=int, default=0, help='Только первые N записей каждой группы (проверка времени)')
    args = parser.parse_args(argv)
    if args.cmd == 'content':
        items = []
        for label in args.groups:
            entries = sorted(inventory_group(label), key=lambda e: e['id'])
            items += [item_for(e, label, GROUPS[label]['ru']) for e in (entries[:args.limit] if args.limit else entries)]
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({'items': items}, ensure_ascii=False, indent=1) + '\n', 'utf-8')
        print(len(items), args.out)
        return 0
    game = wxq_install.require_install_structure(wxq_install.detect_active_install(), full=True)
    font = bpc.title_font(game)
    summary = {}
    for label in args.groups:
        results = render_group(game, label, font)
        good = [x for x in results if x['ok']]
        bad = [x for x in results if not x['ok']]
        sample = pick_sample(good, args.sample)
        per = 18
        for n in range(0, len(sample), per):
            sheet(sample[n:n + per], args.out / f'sheet-{GROUPS[label]["en_ref"].lower()}-{n // per + 1}.png')
        summary[label] = {'total': len(results), 'rendered': len(good), 'failed': [{'id': x['id'], 'error': x['error']} for x in bad],
                          'sample': len(sample), 'em_ratio': sorted({x['metrics']['em_ratio'] for x in good})[:3],
                          'ink_width': [min(x['metrics']['ink_width'] for x in good), max(x['metrics']['ink_width'] for x in good)]}
        print(label, json.dumps(summary[label], ensure_ascii=False), flush=True)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'render-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1) + '\n', 'utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
