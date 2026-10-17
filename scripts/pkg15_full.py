"""Пакет 15, весь комплект подписей карточек (W6b): группы, перевод по glossary, содержимое pilot-content и листы «до/после».

Игру читает ТОЛЬКО на чтение. Команды:
  content - pilot-content (namespace=atlas, style=footer): для 技能/天赋 записи пилота без изменений, для остальных групп
            style footer с params.mode='v2'; rect каждой карточки — минимальный (по рамке реально изменённых пикселей), кратный блокам 4x4;
  sheets  - контактные листы «до/после» по группам (до BC7, для быстрой правки; итоговые листы по BC7 строит check_pkg15_full.py);
  table   - таблица групп (для REPORT.md).
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import re
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
import footer_sprites  # noqa: E402
import pkg15_footers as pf  # noqa: E402
import wxq_install  # noqa: E402

W, H = pf.W, pf.H
PILOT_CONTENT = PROJECT / 'pilot/pilot-content-texture-v5-pkg15.json'
PILOT_LABELS = ('技能', '天赋')  # принятый пилот: записи берутся из него без изменений
RIGHT_FROM = 330

# Каноническая подпись -> русская; src: glossary (форма из glossary.json), short (короткая форма от ru_canon по смыслу en_ref),
# translit (практика проекта: Хэло/Чжулу/Цзяньму в glossary), pending (в glossary нет, предложение — ждёт решения владельца).
GROUPS: dict[str, dict] = {
    '技能': {'ru': 'Умение', 'en': 'Skill', 'src': 'glossary', 'term': 't-99aea2f913', 'ornaments': False, 'max_w': 110},
    '天赋': {'ru': 'Талант', 'en': 'Talent', 'src': 'glossary', 'term': 't-866e2e6d67', 'ornaments': False, 'max_w': 110},
    '装备': {'ru': 'Снаряжение', 'en': 'Equipment', 'src': 'glossary', 'term': 't-144a327039', 'ornaments': False, 'max_w': 168},
    '效果': {'ru': 'Эффект', 'en': 'Effect', 'src': 'ru_short (approved, решение руководителя 2026-10-13, на проверку владельцу)', 'term': 't-86a00cf742', 'ornaments': False, 'max_w': 168},
    '棋手': {'ru': 'Шахматист', 'en': 'Chess Player', 'src': 'glossary (approved, ru_short)', 'term': 't-de258d3c18', 'ornaments': False, 'max_w': 168},
    '棋手·秘技': {'ru': 'Секр. техника', 'en': 'Signature Skill', 'src': 'glossary (approved, решение руководителя 2026-10-13, на проверку владельцу): 秘技=«Секретная техника», 棋手 отброшен', 'term': 't-e873dcc243',
               'ornaments': False, 'max_w': 150, 'pending': 'решение руководителя 2026-10-13, на проверку владельцу: «Секр. техника» (без «棋手·»)'},
    '卡包': {'ru': 'Набор карт', 'en': 'Card Pack', 'src': 'glossary (approved, решение руководителя 2026-10-13, на проверку владельцу)', 'term': 't-bd6207bbf0', 'ornaments': False, 'max_w': 168,
           'pending': 'решение руководителя 2026-10-13, на проверку владельцу: «Набор карт»'},
    '资源': {'ru': 'Ресурсы', 'en': 'Resource', 'src': 'glossary (ru_canon)', 'term': 't-daa6d02a55', 'ornaments': False, 'max_w': 168},
    '任务': {'ru': 'Задание', 'en': 'Task', 'src': 'ru_short (approved, решение руководителя 2026-10-13, на проверку владельцу); ru_canon «Задания»', 'term': 't-5253040db8', 'ornaments': False, 'max_w': 168},
    '钻石狂潮': {'ru': 'Лихорадка', 'en': 'Diamond Frenzy', 'src': 'ru_short «Лихорадка» (approved, решение руководителя 2026-10-13, на проверку владельцу); правые — ru_canon', 'term': 't-16113214b4',
             'ornaments': False, 'max_w': 168, 'ru_right': 'Алмазная лихорадка', 'max_w_right': 300,
             'pending': 'решение руководителя 2026-10-13, на проверку владельцу: центральные подписи «Лихорадка», правые — «Алмазная лихорадка»'},
    '特邀天赋': {'ru': 'Талант по приглашению', 'en': 'Special Invitation Talent', 'src': 'glossary (approved, решение руководителя 2026-10-13, на проверку владельцу)', 'term': 't-74c0160824',
             'ornaments': False, 'max_w': 300},
    '河洛': {'ru': 'Хэло', 'en': 'Central Plains', 'src': 'glossary / транслит (ru_canon «Хэло»)', 'term': 't-05a2a2d9f2', 'ornaments': True, 'max_w': 134},
    '逐鹿': {'ru': 'Чжулу', 'en': 'Zhulu Plateau', 'src': 'glossary / транслит (approved «Чжулу»)', 'term': 't-6240184571', 'ornaments': True, 'max_w': 134},
    '建木': {'ru': 'Цзяньму', 'en': 'The First Lands', 'src': 'glossary / транслит (ru_canon «Цзяньму»)', 'term': 't-bf08646f07', 'ornaments': True, 'max_w': 134},
    '大河流域': {'ru': 'Великая река', 'en': 'Riverlands', 'src': 'ru_short (approved, решение руководителя 2026-10-13, на проверку владельцу); ru_canon «Бассейн Великой реки» не влезает', 'term': 't-610a342f84',
             'ornaments': True, 'max_w': 134, 'pending': 'решение руководителя 2026-10-13, на проверку владельцу: «Великая река»'},
    '三分之地': {'ru': 'Троецарствие', 'en': 'Three Kingdoms', 'src': 'ru_short (approved, решение руководителя 2026-10-13, на проверку владельцу); ru_canon «Земли Троецарствия» не влезает', 'term': 't-6468f7fe25',
             'ornaments': True, 'max_w': 136, 'pending': 'решение руководителя 2026-10-13, на проверку владельцу: «Троецарствие»'},
    '日落海': {'ru': 'Закат', 'en': 'Sunset', 'src': 'ru_short (approved, решение руководителя 2026-10-13, на проверку владельцу); ru_canon «Закатное море» не влезает', 'term': 't-c24b587e35',
            'ornaments': True, 'max_w': 134, 'pending': 'решение руководителя 2026-10-13, на проверку владельцу: «Закат»'},
    '机甲': {'ru': 'Меха', 'en': 'Mecha', 'src': 'glossary (approved, решение руководителя 2026-10-13, на проверку владельцу)', 'term': 't-fd7ae93d80', 'ornaments': True, 'max_w': 134,
           'pending': 'решение руководителя 2026-10-13, на проверку владельцу: «Меха»'},
}
TYPOS = {'建术': '建木', '建水': '建木', '健大': '建木', '阳落海': '日落海', '广河洛': '河洛', '一哪三': '三分之地'}


def normalize(label: str) -> str | None:
    """Китайская подпись из OCR (с шумом: «·», «一», опечатки) -> каноническая группа."""
    if label in GROUPS:
        return label
    s = label.replace('流城', '流域')
    for canon in ('大河流域', '三分之地', '日落海', '建木', '河洛', '逐鹿', '机甲'):
        if canon in s:
            return canon
    for typo, canon in TYPOS.items():
        if typo in s:
            return canon
    return None


def cards() -> list[dict]:
    """Карточки пакета 15 с подписью: id, группа, рамка OCR (x0,y0,x1,y1)."""
    data = json.loads(pf.INVENTORY.read_text('utf-8'))
    out = []
    for e in data['textures_with_cjk']:
        if str(e.get('pkg')) != '15' or not e.get('footer'):
            continue
        raw = e['footer'][0]
        group = normalize(raw)
        if group is None:
            raise SystemExit(f'Подпись {raw!r} ({e["id"]}) не сопоставлена группе')
        line = [l for l in e['lines'] if l['t'] == raw][0]
        out.append({'id': e['id'], 'group': group, 'raw': raw, 'box': list(line['box']), 'crop': e['footer_crop_group']})
    return sorted(out, key=lambda c: c['id'])


def block_align(x0, y0, x1, y1):
    return flp.block_rect(int(x0), int(y0), int(x1), int(y1), W, H)


ART_LIMITS_ABS = (100, 292)  # подложка-рисунок: окно правки не заходит на английские надписи карточки слева (HERO NO., EFFECT...) и справа (HONOR OF KINGS: ACE)


def try_card(before, ru, font, params, box, grect, align, group) -> dict:
    """Минимальный rect для карточки: большой прогон -> рамка изменений -> проверка, что в минимальной области результат тот же."""
    x0, y0, x1, y1 = box

    def with_hint(rect):
        out = dict(params, hint=[x0 - rect[0], y0 - rect[1], x1 - rect[0], y1 - rect[1]])
        if params.get('art'):
            out['origin_x'] = rect[0]
        if params.get('sprite'):
            out['origin_y'] = rect[1]
        return out
    gparams = with_hint(grect)
    gout, gm = flp.render_footer(before[grect[1]:grect[3], grect[0]:grect[2]], ru, font, **gparams)
    wx0, wy0, wx1, wy1 = gm['window']
    cb = gm['changed_box']
    rect = block_align(min(wx0 - 4, cb[0] - 6) + grect[0], min(wy0 - 1, cb[1] - 2) + grect[1], max(wx1 + 4, cb[2] + 6) + grect[0], max(wy1 + 1, cb[3] + 2) + grect[1])
    for _ in range(4):
        fparams = with_hint(rect)
        fout, fm = flp.render_footer(before[rect[1]:rect[3], rect[0]:rect[2]], ru, font, **fparams)
        ref = gout[rect[1] - grect[1]:rect[3] - grect[1], rect[0] - grect[0]:rect[2] - grect[0]]
        if np.array_equal(fout, ref):
            return {'rect': rect, 'params': fparams, 'metrics': {k: fm[k] for k in ('font_px', 'em_ratio', 'ink_width', 'ink_box', 'fill', 'bevel', 'ornaments', 'cn_ink_height', 'art')}}
        rect = block_align(rect[0] - 8, rect[1] - 4, rect[2] + 8, rect[3] + 4)
        if rect[0] < 4 or rect[2] > W - 4:
            break
    raise flp.FooterBlocker('Результат в минимальной области не совпал с результатом в большой')


def render_card(task: tuple) -> dict:
    """Один кадр: генерируем rect (минимальный) и проверяем рендер в нём. Работает в дочернем процессе."""
    ident, group, box, stream, font = task
    spec = GROUPS[group]
    x0, y0, x1, y1 = box
    if x1 - x0 > 140:
        x1 = x0 + 115  # шум OCR справа (соседний текст в рамке)
    align = 'right' if (x0 >= RIGHT_FROM or x1 >= 380) else 'center'
    before = atlas_text_patch.decode_upright(stream, W, H)
    ru, max_w = spec['ru'], spec['max_w']
    if align == 'right' and 'ru_right' in spec:
        ru, max_w = spec['ru_right'], spec['max_w_right']
    params = {'mode': 'v2', 'align': align, 'max_w': max_w, 'ornaments': spec['ornaments']}
    if align == 'right':
        gx0, gx1 = max(40, x1 - 330), 400
    else:
        gx0, gx1 = 80, 324
    grect = block_align(gx0, y0 - 16, gx1, y1 + 16)
    res = {'id': ident, 'group': group, 'ok': False, 'ru': ru}
    error_first = None
    attempts = []
    slug = footer_sprites.slug(spec['en']) if spec['ornaments'] and align == 'center' and footer_sprites.load(footer_sprites.slug(spec['en'])) and footer_sprites.is_gold(before) else None
    if slug:
        # золотая карточка группы с листиками: старая подпись стирается по спрайту (footer_sprite_render), подпись между листиками
        attempts.append({'art': True, 'sprite': slug})
    attempts += [{}, {'art': True, 'limits_abs': list(ART_LIMITS_ABS), 'outline': 'off'}]
    for extra in attempts:
        try:
            res.update(try_card(before, ru, font, params | extra, box=(x0, y0, x1, y1), grect=grect, align=align, group=group))
            res['ok'] = True
            res.pop('error', None)
            break
        except (flp.FooterBlocker, atlas_text_patch.AtlasBlocker) as error:
            res['error'] = str(error)
        except Exception as error:  # noqa: BLE001 - любая ошибка карточки попадает в отчёт отказов, а не роняет весь прогон
            import traceback
            res['error'] = f'{type(error).__name__}: {error} @ {traceback.format_exc().splitlines()[-3].strip()}'
        if error_first is None:
            error_first = res['error']
    if not res['ok']:
        res['error'] = f'{error_first} | последняя попытка: {res["error"]}'
    return res


def item_from(card: dict, res: dict) -> dict:
    spec = GROUPS[card['group']]
    return {'id': f'pkg15-{card["id"]}', 'namespace': 'atlas', 'resource': f'15/{card["id"]}', 'key': 'footer', 'cn_source': card['raw'],
            'en_ref': spec['en'], 'ru': res.get('ru', spec['ru']), 'rect': res['rect'], 'style': 'footer', 'params': res['params'],
            'where': f'пакет 15, группа {card["group"]}, подложка {card["crop"]}'}


def cmd_content(args) -> int:
    allc = cards()
    game = wxq_install.require_install_structure(wxq_install.detect_active_install(), full=True)
    font = bpc.title_font(game)
    pilot = {i['id']: i for i in json.loads(PILOT_CONTENT.read_text('utf-8'))['items']}
    todo = [c for c in allc if c['group'] not in PILOT_LABELS]
    streams = pf.read_streams(game, [c['id'] for c in todo])
    tasks = [(c['id'], c['group'], c['box'], streams[c['id']], font) for c in todo]
    results = {}
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        for n, res in enumerate(pool.map(render_card, tasks, chunksize=4), 1):
            results[res['id']] = res
            if n % 100 == 0:
                print(n, '/', len(tasks), flush=True)
    items, failed = [], []
    for c in allc:
        if c['group'] in PILOT_LABELS:
            items.append(pilot[f'pkg15-{c["id"]}'])
            continue
        res = results[c['id']]
        if res['ok']:
            items.append(item_from(c, res))
        else:
            failed.append({'id': c['id'], 'group': c['group'], 'raw': c['raw'], 'error': res['error']})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({'items': items}, ensure_ascii=False, indent=1) + '\n', 'utf-8')
    (args.out.with_suffix('.failed.json')).write_text(json.dumps(failed, ensure_ascii=False, indent=1) + '\n', 'utf-8')
    (args.out.with_suffix('.metrics.json')).write_text(json.dumps({r['id']: r.get('metrics') for r in results.values() if r['ok']}, ensure_ascii=False) + '\n', 'utf-8')
    print(f'записей {len(items)}, отказов {len(failed)}', args.out)
    by = collections.Counter(f['group'] for f in failed)
    print(dict(by))
    for f in failed[:12]:
        print(f['id'], f['raw'], f['error'])
    return 1 if failed else 0


def cmd_sheets(args) -> int:
    items = [i for i in json.loads(args.content.read_text('utf-8'))['items']]
    game = wxq_install.require_install_structure(wxq_install.detect_active_install(), full=True)
    font = bpc.title_font(game)
    by = collections.defaultdict(list)
    for item in items:
        by[normalize(item['cn_source'])].append(item)
    if args.art:
        by = collections.defaultdict(list, {'art': [i for g in by.values() for i in g if i['params'].get('art')]})
    groups = ['art'] if args.art else (args.groups or list(by))
    streams = pf.read_streams(game, [i['resource'].split('/')[1] for g in groups for i in by[g]])
    args.out.mkdir(parents=True, exist_ok=True)
    for group in groups:
        rows = []
        for item in by[group]:
            ident = item['resource'].split('/')[1]
            before = atlas_text_patch.decode_upright(streams[ident], W, H)
            x0, y0, x1, y1 = item['rect']
            drawn, _ = atlas_text_patch.render_item(before[y0:y1, x0:x1], item['ru'], font, 'footer', item['params'])
            after = before.copy()
            after[y0:y1, x0:x1] = drawn
            right = item['params']['align'] == 'right'
            rows.append({'id': ident, 'crop': item['where'].split()[-1], 'item': item, 'stats': pf.plate_stats(before, item['rect']), 'before': before, 'after': after,
                         'view': [70, 500, 404, 564] if right else [96, 500, 308, 564], 'label': f'{ident} {item["cn_source"]}'})
        sample = pf.pick_sample(rows, args.sample)
        per = args.per
        name = 'art' if group == 'art' else GROUPS[group]['en'].lower().replace(' ', '-')
        for n in range(0, len(sample), per):
            pf.sheet(sample[n:n + per], args.out / f'sheet-{name}-{n // per + 1}.png', scale=2, columns=2)
        print(group, len(by[group]), 'в листе', len(sample), flush=True)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('content')
    c.add_argument('--out', type=Path, required=True)
    c.add_argument('--workers', type=int, default=12)
    s = sub.add_parser('sheets')
    s.add_argument('--content', type=Path, required=True)
    s.add_argument('--out', type=Path, required=True)
    s.add_argument('--groups', nargs='*')
    s.add_argument('--sample', type=int, default=36)
    s.add_argument('--per', type=int, default=12)
    s.add_argument('--art', action='store_true', help='Только карточки с подложкой-рисунком (params.art)')
    args = parser.parse_args(argv)
    return cmd_content(args) if args.cmd == 'content' else cmd_sheets(args)


if __name__ == '__main__':
    sys.exit(main())
