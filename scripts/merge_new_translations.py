"""Слияние ответов переводчиков (tr/out-N.json) в носитель пакета 1 после обновления игры.

Вход: каталог tr/ с batch-N.json (задания: id, namespace, resource, key, zh_new, flags, max_len) и out-N.json
({id: русский текст}). Каждая строка проходит ту же проверку, что и при сборке (`build_carrier.row_issues`:
плейсхолдеры, теги, числа, шрифт, перенос слов, CJK) плюс явные проверки плейсхолдеров/тегов/переносов,
лимита длины и запрещённых вариантов канона. Исходное дерево (носитель-основа, tr/) не меняется.

Выход (новые файлы, существующие не перезаписываются):
  --out-delta    дельта в формате строк carrier: {"schema": "wxq-carrier-delta/v1", "carrier": [...]}
  --out-carrier  новый carrier = основа без удалённых и заменённых строк + дельта (его берёт `build_carrier.py --carrier`)
  <каталог --out-delta>/merge-report.json  принятые/отклонённые/непереведённые

Строки с замечаниями не попадают в выход и перечислены в отчёте. Без --allow-partial при любом замечании или
непереведённой строке выход не пишется (код 1). --check только проверяет.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
for _path in (str(PROJECT), str(PROJECT / 'scripts')):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import build_carrier as bc  # noqa: E402
from localization import core, fit_rules, latin_allow, ru_cli  # noqa: E402

DEFAULT_BASE = PROJECT / 'build/coverage-2026-10-14/after-layout-v7/carrier-layout.json'
CJK = re.compile(r'[㐀-鿿]')
BATCH = re.compile(r'batch-(\d+)\.json$')

# Явные исключения: оригинал остаётся как есть, перевода в носителе нет, сборку это не ломает.
# Строки без иероглифов (форматы '{0} {1}', даты '10.15-10.25', тестовый код) оставляются автоматически:
# переводить в них нечего, поэтому 'equal-to-source' для них не применяется.
KEEP_SOURCE = {
    'da235e4f728242bd/#FAE6155EE87BC6D4#': 'битая кодировка (mojibake)',
    'da235e4f728242bd/#FAE64AEF81D8F327#': 'битая кодировка (mojibake)',
    'dd5b5edfbb69feed/PvpSettleTips19': 'тестовая строка',
    'dd5b5edfbb69feed/PvpSettleTips31': 'одиночная скобка в шаблоне, смысл не восстановить',
}


def load_json(path: Path):
    return json.loads(path.read_text('utf-8-sig'))


def forbidden_terms() -> list[tuple[str, str]]:
    """(китайский термин, запрещённый русский вариант) из glossary/canon_decisions.json."""
    path = PROJECT / 'glossary/canon_decisions.json'
    if not path.is_file():
        return []
    return [(t['cn'], f) for t in load_json(path)['terms'] for f in t.get('forbidden', [])]


def own_issues(rec: dict, ru: str, banned: list[tuple[str, str]]) -> list[str]:
    zh = rec['zh_new']
    issues = []
    if not isinstance(ru, str) or not ru.strip():
        return ['empty']
    if ru.strip() == zh.strip():
        issues.append('equal-to-source')
    if CJK.search(ru) and not CJK.search(zh):
        issues.append('cjk-in-translation')
    if collections.Counter(core._PLACEHOLDER.findall(ru)) != collections.Counter(core._PLACEHOLDER.findall(zh)):
        issues.append('placeholders-differ')
    if collections.Counter(m.group(0) for m in core._TAG.finditer(ru)) != collections.Counter(m.group(0) for m in core._TAG.finditer(zh)):
        issues.append('tags-differ')
    if ru.count('\n') != zh.count('\n'):
        issues.append('newline-count-differs')
    if '\\n' in ru:
        issues.append('literal-backslash-n')
    limit = rec.get('max_len')
    # max_len в batch считается по видимому тексту (без тегов и аргументов), так же меряем и перевод
    shown = fit_rules.visible_length(ru.strip())
    if limit and shown > limit:
        issues.append(f'too-long:{shown}>{limit}')
    for cn, bad in banned:
        if cn in zh and re.search(r'(?<!\w)' + re.escape(bad) + r'(?!\w)', ru, re.I):
            issues.append(f'forbidden-variant:{bad}')
    return issues


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tr-dir', type=Path, required=True, help='Каталог с batch-N.json и out-N.json')
    parser.add_argument('--base-carrier', type=Path, default=DEFAULT_BASE)
    parser.add_argument('--untranslated', type=Path, default=None, help='UNTRANSLATED-*.json: removed_keys убираются из носителя')
    parser.add_argument('--out-delta', type=Path, default=None)
    parser.add_argument('--out-carrier', type=Path, default=None)
    parser.add_argument('--game-version', default='1.6.0.1')
    parser.add_argument('--allow-partial', action='store_true', help='Писать выход из принятых строк, даже если есть замечания или пропуски')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')

    batches = sorted(args.tr_dir.glob('batch-*.json'), key=lambda p: int(BATCH.search(p.name).group(1)))
    if not batches:
        print('batch-N.json не найдены', file=sys.stderr)
        return 2
    banned = forbidden_terms()
    glyphs = bc.load_glyphs()
    latin = latin_allow.default_allowlist(ru_cli.GLOSSARY)
    edge = bc.edge_space_idents()
    accepted, rejected, untranslated, unknown, kept_source = [], [], [], [], []
    seen_ids = set()
    for batch_path in batches:
        number = BATCH.search(batch_path.name).group(1)
        recs = load_json(batch_path)
        by_id = {r['id']: r for r in recs}
        out_path = args.tr_dir / f'out-{number}.json'
        answers = load_json(out_path) if out_path.is_file() else {}
        if not isinstance(answers, dict):
            print(f'{out_path.name}: ожидается объект {{id: ru}}', file=sys.stderr)
            return 2
        unknown += [f'out-{number}:{i}' for i in answers if i not in by_id]
        for rec in recs:
            ident = (rec['namespace'], rec['resource'], rec['key'])
            if rec['id'] in seen_ids:
                rejected.append({'id': rec['id'], 'reasons': ['duplicate-id-in-batches']})
                continue
            seen_ids.add(rec['id'])
            if rec['id'] not in answers:
                reason = KEEP_SOURCE.get(rec['id']) or ('нет иероглифов, переводить нечего' if not CJK.search(rec['zh_new']) else None)
                if reason:
                    kept_source.append({'id': rec['id'], 'reason': reason, 'zh': rec['zh_new']})
                else:
                    untranslated.append(rec['id'])
                continue
            ru = answers[rec['id']]
            issues = own_issues(rec, ru, banned)
            if not issues:
                ns = 'lang' if rec['namespace'] == 'pb-lang' else 'global'
                keep = ident in edge or rec['zh_new'] != rec['zh_new'].strip()
                _game, found = bc.row_issues(ns, rec['zh_new'], ru, None, glyphs, latin, keep)
                issues = found
            if issues:
                rejected.append({'id': rec['id'], 'reasons': issues, 'ru': ru})
                continue
            text = ru if (ident in edge or rec['zh_new'] != rec['zh_new'].strip()) else ru.strip()
            accepted.append({'consumer': '*', 'key': rec['key'], 'namespace': rec['namespace'], 'resource': rec['resource'],
                             'source_sha256': core.digest(rec['zh_new']), 'text': text, 'variant': '*'})
    summary = {'accepted': len(accepted), 'rejected': len(rejected), 'untranslated': len(untranslated),
               'kept_source': len(kept_source), 'unknown_ids': unknown}
    report = {**summary, 'rejected_rows': rejected, 'untranslated_ids': untranslated, 'kept_source_rows': kept_source}
    print(json.dumps(summary, ensure_ascii=False))
    for item in rejected[:20]:
        print('  ', item['id'], item['reasons'])
    if args.check:
        return 0 if not (rejected or untranslated or unknown) else 1
    if args.out_delta is None or args.out_carrier is None:
        print('Нужны --out-delta и --out-carrier (или --check)', file=sys.stderr)
        return 2
    for target in (args.out_delta, args.out_carrier):
        if target.exists():
            print(f'Файл уже существует: {target}', file=sys.stderr)
            return 2
    args.out_delta.parent.mkdir(parents=True, exist_ok=True)
    report_path = args.out_delta.with_name('merge-report.json')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n', 'utf-8')
    if (rejected or untranslated or unknown) and not args.allow_partial:
        print(f'Есть замечания/пропуски: выход не записан; отчёт {report_path}', file=sys.stderr)
        return 1
    if len({(r['namespace'], r['resource'], r['key']) for r in accepted}) != len(accepted):
        print('Повтор ключей в принятых строках', file=sys.stderr)
        return 2

    base_value = load_json(args.base_carrier)
    base_rows = base_value['carrier'] if isinstance(base_value, dict) else base_value
    meta = {k: v for k, v in base_value.items() if k != 'carrier'} if isinstance(base_value, dict) else {}
    drop = {(r['namespace'], r['resource'], r['key']) for r in accepted}
    for item in kept_source:  # прежний перевод к изменившемуся оригиналу не оставляем
        resource, key = item['id'].split('/', 1)
        drop.add(('global' if resource == bc.GLOBAL_ID else 'pb-lang', resource, key))
    if args.untranslated:
        for r in load_json(args.untranslated).get('removed_keys', []):
            drop.add(('global' if r['resource'] == bc.GLOBAL_ID else 'pb-lang', r['resource'], r['key']))
    kept = [r for r in base_rows if (r['namespace'], r['resource'], r['key']) not in drop]
    rows = sorted(kept + accepted, key=lambda r: (r['namespace'], r['resource'], r['key']))
    dump = {'schema': 'wxq-carrier-delta/v1', 'game_version': args.game_version, 'carrier': sorted(accepted, key=lambda r: (r['namespace'], r['resource'], r['key']))}
    args.out_delta.write_text(json.dumps(dump, ensure_ascii=False, indent=0, sort_keys=True) + '\n', 'utf-8')
    args.out_carrier.parent.mkdir(parents=True, exist_ok=True)
    args.out_carrier.write_text(json.dumps({**meta, 'carrier': rows}, ensure_ascii=False, indent=0, sort_keys=True) + '\n', 'utf-8')
    bc.load_carrier(args.out_carrier)  # тот же разбор, что у build_carrier
    print(json.dumps({'carrier_rows': len(rows), 'base_rows': len(base_rows), 'dropped_from_base': len(base_rows) - len(kept),
                      'delta': str(args.out_delta), 'carrier': str(args.out_carrier)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
