"""Неоконный тест пути «пересадка записей» в WXQTool.exe: перевод для 1.6.0.1 ставится в песочницу игры 1.4.0.1.

    python -X utf8 -B installer/tests/test_transplant.py [--keep]

Песочница (во временной папке): prime пакетов 1 и 10 версии 1.4.0.1 (source/game-native-carrier/.../native-vendor/prime, жёсткие ссылки),
скелеты override 1.4.0.1 пакетов 0, 1, 10 (source/game-build-rebase/.../official-cache-v2-parent-observation), QtskDB.dll из того же vendor,
QtsVFS.log с pkgHash из заголовков скелетов. install.json собран build_installer.combine для кандидатов build/carrier-2026-10-17-v8-1.6.0.1 (пакет 1) и
build/lua-wrap-2026-10-17-v10-1.6.0.1 (пакет 10) под 1.6.0.1; пересборка Lua из настоящей игры в этом тесте отключена (игра новее, её не читаем).
Настоящая игра и её кэш не читаются и не меняются; окон нет. Если настоящая WXQ.exe запущена, помощник собирается без остановки по
запущенной игре (это единственное отличие копии; песочница не пересекается с настоящей игрой).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / 'installer'))
sys.path.insert(0, str(PROJECT / 'scripts'))
import build_installer as bi  # noqa: E402
import pilot_contract  # noqa: E402
from game_inspect import QtsContainer  # noqa: E402

VENDOR = PROJECT / 'source/game-native-carrier/1.4.0.1-331-v1/archives/native-vendor'
SKELETON = PROJECT / 'source/game-build-rebase/1.4.0.1/official-cache-v2-parent-observation/packages'
CANDIDATES = (PROJECT / 'build/carrier-2026-10-17-v8-1.6.0.1', PROJECT / 'build/lua-wrap-2026-10-17-v10-1.6.0.1')
HEADER_ID = 0xe12bade797f29f55
SKELETON_PACKAGES = ('0', '1', '10')
failures: list[str] = []


def check(name: str, condition: bool, detail: str = '') -> bool:
    print(('  ok   ' if condition else '  FAIL ') + name + ('' if condition else f'  [{detail}]'), flush=True)
    if not condition:
        failures.append(name)
    return bool(condition)


def sha(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def game_is_running() -> bool:
    done = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True)
    return bool(re.search(rb'"(WXQ|HonorofKingsACE|WXQLauncher[^"]*)\.exe"', done.stdout, re.IGNORECASE))


def build_tool(work: Path) -> Path:
    """Помощник из installer/WXQTool.cs (TESTBUILD). При запущенной настоящей игре — копия без остановки по процессам."""
    folder = work / 'tool'
    folder.mkdir()
    if not game_is_running():
        bi.build_tool(folder, test_build=True)
        return folder / 'WXQTool.exe'
    print('       WXQ.exe запущен: помощник собран без остановки по запущенной игре (только для песочницы)', flush=True)
    source = (PROJECT / 'installer/WXQTool.cs').read_text('utf-8-sig')
    needle = 'if (active.Count > 0)'
    assert source.count(needle) == 1
    (folder / 'WXQTool.cs').write_text(source.replace(needle, 'if (false && active.Count > 0)'), 'utf-8-sig')
    done = subprocess.run([str(bi.CSC), '/nologo', '/target:exe', '/platform:x64', '/optimize+', '/codepage:65001', '/define:TESTBUILD',
                           '/r:System.Web.Extensions.dll', f'/out:{folder / "WXQTool.exe"}', str(folder / 'WXQTool.cs')], capture_output=True)
    if done.returncode:
        raise SystemExit('помощник не собрался: ' + (done.stdout + done.stderr).decode('cp866', errors='replace'))
    return folder / 'WXQTool.exe'


def make_payload(work: Path) -> tuple[Path, dict]:
    bi.verify_lua_regeneration = lambda directory, manifest: None  # Lua-кандидат собран под 1.5.0.1; настоящую игру (новее) не читаем
    install, files = bi.combine(list(CANDIDATES), 'test', '2026-01-01')
    payload = work / 'payload'
    payload.mkdir()
    for source, flat in files:
        shutil.copyfile(source, payload / flat)
    (payload / 'install.json').write_text(json.dumps(install, ensure_ascii=False, indent=1), 'utf-8')
    return payload, install


def link_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def header_of(path: Path) -> bytes:
    with QtsContainer(path, raw_values=True) as meta:
        return meta.raw_record_values(HEADER_ID)[0]


def build_box(root: Path, log_hash_override: dict[str, str] | None = None) -> dict:
    """Песочница игры 1.4.0.1. Возвращает пути и снимок исходных файлов."""
    game = root / 'game'
    (game / 'WXQ_Data/Package/builtin').mkdir(parents=True)
    (game / 'WXQ_Data/Plugins/x86_64').mkdir(parents=True)
    (game / 'WXQ.exe').write_bytes(b'MZ')
    shutil.copyfile(VENDOR / 'QtskDB.dll', game / 'WXQ_Data/Plugins/x86_64/QtskDB.dll')
    for package in ('1', '10'):
        for name in (f'{package}.db', f'{package}_0.db'):
            link_or_copy(VENDOR / 'prime' / package / name, game / 'WXQ_Data/Package/builtin' / package / name)
    cache = game / 'QtsDataFolder/QtsVFSCache'
    lines = []
    for package in SKELETON_PACKAGES:
        for name in (f'{package}.db', f'{package}.info'):
            target = cache / 'packages' / package / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SKELETON / package / name, target)
        value = pilot_contract.header_hashes(header_of(cache / 'packages' / package / f'{package}.db').hex())
        assert value['pkgHash'] == value['basePkgHash']
        shown = (log_hash_override or {}).get(package, value['pkgHash'])
        lines.append(f'MountPackage {package}, prime, 7 => succeed! test pkgHash:{shown} basePkgHash:{shown}')
    (cache / 'QtsVFS.log').write_text('\n'.join(lines) + '\n', 'utf-8')
    return {'root': root, 'game': game, 'cache': cache / 'packages', 'data': root / 'data'}


def snapshot(path: Path) -> dict[str, str]:
    return {p.relative_to(path).as_posix(): sha(p) for p in sorted(path.rglob('*')) if p.is_file()}


def run(tool: Path, box: dict, payload: Path, command: str, _env: dict | None = None, **extra: str) -> dict[str, str]:
    out = box['root'] / 'out.txt'
    out.unlink(missing_ok=True)
    args = [str(tool), command, '--payload', str(payload), '--data', str(box['data']), '--out', str(out), '--game', str(box['game'])]
    for name, value in extra.items():
        args += ['--' + name, value]
    done = subprocess.run(args, capture_output=True, timeout=1800, env=dict(os.environ, WXQ_RU_USERS_ROOT=str(box['root'] / 'no-users'), **(_env or {})))
    rows = {'_exit': str(done.returncode)}
    if out.is_file():
        for line in out.read_text('utf-8-sig').splitlines():
            name, _, value = line.partition('=')
            rows[name] = value
    return rows


def receipt_of(box: dict, label: str) -> dict:
    return json.loads((box['data'] / 'backup' / label / 'receipt.json').read_text('utf-8-sig'))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--keep', action='store_true')
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix='wxq-transplant-test-'))
    try:
        tool = build_tool(work)
        payload, install = make_payload(work)
        check('T0 install.json: игра 1.6.0.1, sdk_known непуст, ресурсы пакетов на месте',
              install['game_version'] == '1.6.0.1' and install['sdk_known'] and all(p['resources'] for p in install['packages']))
        box = build_box(work / 'box')
        prime_before = snapshot(box['game'] / 'WXQ_Data/Package')
        cache_before = snapshot(box['cache'])
        skeleton_headers = {p: header_of(box['cache'] / p / f'{p}.db') for p in SKELETON_PACKAGES}

        # Ожидание независимо от помощника: Python-разбор prime 1.4.0.1 и оригиналов из install.json.
        expected_applied: dict[str, set[str]] = {}
        expected_skipped: dict[str, set[str]] = {}
        for entry in install['packages']:
            package = entry['id']
            expected_applied[package], expected_skipped[package] = set(), set()
            with QtsContainer(box['game'] / f'WXQ_Data/Package/builtin/{package}/{package}_0.db') as prime:
                for row in entry['resources']:
                    ident = int(row['id_hex'], 16)
                    same = ident in prime.entries and hashlib.sha256(prime.decode_entry(ident)).hexdigest() == row['original_sha256']
                    (expected_applied if same else expected_skipped)[package].add(row['id_hex'])
        print('       ожидается перенести/пропустить: ' + ', '.join(f'пакет {p}: {len(expected_applied[p])}/{len(expected_skipped[p])}' for p in expected_applied), flush=True)

        rows = run(tool, box, payload, 'check')
        check('T1 check в пути пересадки: status=ok, state=fresh', rows.get('status') == 'ok' and rows.get('state') == 'fresh', str(rows))
        check('T1a check ничего не записал', snapshot(box['cache']) == cache_before)

        rows = run(tool, box, payload, 'backup')
        check('T2 backup: status=ok, есть метка, transplant=1', rows.get('status') == 'ok' and rows.get('label') and rows.get('transplant') == '1', str(rows))
        label = rows.get('label', '')
        receipt = receipt_of(box, label) if label else {}
        applied = {k: set(v) for k, v in receipt.get('applied_resources', {}).items()}
        skipped = {k: {x.split(':')[0] for x in v} for k, v in receipt.get('skipped_resources', {}).items()}
        check('T3 receipt: transplant, applied_resources = ожидаемому набору (Python-разбор prime)',
              receipt.get('transplant') is True and applied == {k: v for k, v in expected_applied.items() if v}, f'{applied} vs {expected_applied}')
        check('T4 receipt: skipped_resources перечисляют пропущенные id', all(skipped.get(k, set()) == v for k, v in expected_skipped.items()), str(skipped))
        check('T4a backup ничего не записал в кэш игры', snapshot(box['cache']) == cache_before)

        rows = run(tool, box, payload, 'unpack', label=label)
        check('T5 unpack: status=ok', rows.get('status') == 'ok', str(rows))
        rows = run(tool, box, payload, 'verify', label=label)
        check('T6 verify: status=ok', rows.get('status') == 'ok', str(rows))
        state_path = box['data'] / 'state.json'
        state_file = json.loads(state_path.read_text('utf-8-sig')) if state_path.is_file() else {}

        by_id = {r['id_hex']: r for e in install['packages'] for r in e['resources']}
        for package, ids in applied.items():
            meta_path, data_path = box['cache'] / package / f'{package}.db', box['cache'] / package / f'{package}_0.db'
            check(f'T7 пакет {package}: файл данных создан', data_path.is_file())
            with QtsContainer(meta_path, raw_values=True) as meta, QtsContainer(data_path) as data:
                wanted = {int(i, 16) for i in ids}
                check(f'T8 пакет {package}: записи метаданных = заголовок + подошедшие id', set(meta.entries) == wanted | {HEADER_ID}, f'{len(meta.entries)} vs {len(wanted)}')
                check(f'T8a пакет {package}: записи данных = подошедшие id, битых нет', set(data.entries) == wanted and meta.invalid_record_count + data.invalid_record_count == 0)
                check(f'T9 пакет {package}: заголовок байт-в-байт = скелет (pkgHash)', meta.raw_record_values(HEADER_ID)[0] == skeleton_headers[package])
                bad = [i for i in ids if hashlib.sha256(data.decode_entry(int(i, 16))).hexdigest() != by_id[i]['sha256']]
                check(f'T10 пакет {package}: SHA декодированных нагрузок = install.json у всех {len(ids)}', not bad, str(bad[:3]))
            check(f'T10a пакет {package}: .info не менялся', sha(box['cache'] / package / f'{package}.info') == cache_before[f'{package}/{package}.info'])
        check('T11 prime не менялся (SHA всех файлов)', snapshot(box['game'] / 'WXQ_Data/Package') == prime_before)
        check('T11a пакет 0 (не в установщике) не тронут', all(cache_before[k] == sha(box['cache'] / k) for k in cache_before if k.startswith('0/')))

        rows = run(tool, box, payload, 'check')
        check('T12 повторный check: state=same (уже установлено)', rows.get('status') == 'ok' and rows.get('state') == 'same', str(rows))
        rows = run(tool, box, payload, 'backup')
        check('T12a повторный backup: метка пуста, ничего не делается', rows.get('status') == 'ok' and rows.get('label') == '', str(rows))

        rows = run(tool, box, payload, 'restore')
        check('T13 restore: status=ok', rows.get('status') == 'ok', str(rows))
        check('T14 restore вернул кэш к скелетам 1.4.0.1 байт-в-байт (файлы данных удалены)', snapshot(box['cache']) == cache_before)
        check('T14a prime по-прежнему не менялся', snapshot(box['game'] / 'WXQ_Data/Package') == prime_before)

        # Отказы: ничего не записано, понятный код.
        def fresh_box(name: str, **options) -> dict:
            return build_box(work / name, **options)

        wrong = fresh_box('neg-log', log_hash_override={'1': '123', '10': '456'})
        rows = run(tool, wrong, payload, 'check')
        check('T15 pkgHash в журнале не совпал с заголовком: stop cache_missing', rows.get('code') == 'cache_missing', str(rows))
        nolog = fresh_box('neg-nolog')
        (nolog['cache'].parent / 'QtsVFS.log').unlink()
        rows = run(tool, nolog, payload, 'check')
        check('T16 нет журнала QtsVFS.log: stop cache_missing', rows.get('code') == 'cache_missing', str(rows))
        foreign = fresh_box('neg-foreign')
        built = box['data'] / 'backup' / label / 'built' / '1' / '1.db'
        check('T16a собранный файл пакета 1 сохранён в папке копии', built.is_file())
        if built.is_file():
            shutil.copyfile(built, foreign['cache'] / '1' / '1.db')
        rows = run(tool, foreign, payload, 'check')
        check('T17 скелет уже содержит записи: stop cache_missing', rows.get('code') == 'cache_missing', str(rows))
        unknown = fresh_box('neg-sdk')
        bad_payload = work / 'payload-bad-sdk'
        shutil.copytree(payload, bad_payload)
        broken = json.loads((payload / 'install.json').read_text('utf-8'))
        broken['sdk_known'] = ['0' * 64]
        (bad_payload / 'install.json').write_text(json.dumps(broken, ensure_ascii=False), 'utf-8')
        rows = run(tool, unknown, bad_payload, 'backup')
        install_log = (unknown['data'] / 'install.log').read_text('utf-8-sig', errors='replace') if (unknown['data'] / 'install.log').is_file() else ''
        check('T18 QtskDB.dll не в sdk_known: больше не стоп, установка проходит, в журнале «неизвестная»',
              rows.get('status') == 'ok' and 'неизвестная' in install_log, str(rows))

        def sdk_box(name: str, dll_source: Path | None = None, content: bytes | None = None) -> dict:
            made = fresh_box(name)
            target = made['game'] / 'WXQ_Data/Plugins/x86_64/QtskDB.dll'
            if dll_source is not None:
                shutil.copyfile(dll_source, target)
            if content is not None:
                target.write_bytes(content)
            return made

        system_dll = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'version.dll'
        noexp = sdk_box('neg-noexports', dll_source=system_dll)
        before = snapshot(noexp['cache'])
        rows = run(tool, noexp, payload, 'backup')
        check('T18a DLL без нужных экспортов (системная DLL под именем QtskDB.dll): stop sdk, кэш не тронут',
              rows.get('code') == 'sdk' and snapshot(noexp['cache']) == before and not list((noexp['data'] / 'backup').glob('*/receipt.json')), str(rows))
        notpe = sdk_box('neg-notpe', content=b'this is not a portable executable' * 100)
        before = snapshot(notpe['cache'])
        rows = run(tool, notpe, payload, 'check')
        check('T18b не PE (текст под именем QtskDB.dll): stop sdk уже на check, кэш не тронут', rows.get('code') == 'sdk' and snapshot(notpe['cache']) == before, str(rows))
        tamper = fresh_box('neg-tamper')
        before = snapshot(tamper['cache'])
        rows = run(tool, tamper, payload, 'backup', _env={'WXQ_TEST_TAMPER': '1'})
        check('T18d подмена собранного <N>_0.db до проверки: stop transplant, кэш не тронут, receipt не создан',
              rows.get('code') == 'transplant' and snapshot(tamper['cache']) == before and not list((tamper['data'] / 'backup').glob('*/receipt.json')), str(rows))
        old_format = work / 'payload-old'
        shutil.copytree(payload, old_format)
        legacy = json.loads((payload / 'install.json').read_text('utf-8'))
        for entry in legacy['packages']:
            entry.pop('resources', None)
        (old_format / 'install.json').write_text(json.dumps(legacy, ensure_ascii=False), 'utf-8')
        oldbox = fresh_box('neg-old')
        rows = run(tool, oldbox, old_format, 'backup')
        check('T19 install.json без resources: stop payload, кэш не тронут', rows.get('code') == 'payload' and snapshot(oldbox['cache']) == cache_before, str(rows))
        busy = fresh_box('neg-pending')
        rows = run(tool, busy, payload, 'backup')
        label2 = rows.get('label', '')
        rows = run(tool, busy, payload, 'unpack', label=label2)
        check('T20 вторая независимая установка проходит так же (повторяемость)', rows.get('status') == 'ok', str(rows))
        receipt2 = receipt_of(busy, label2)
        # Файл, переписанный игрой после установки, при откате не затирается.
        victim = busy['cache'] / '1' / '1_0.db'
        victim.write_bytes(b'game wrote this')
        rows = run(tool, busy, payload, 'restore')
        check('T21 restore пропускает файл, переписанный игрой (skipped), а не затирает', rows.get('status') == 'ok' and '1_0.db' in rows.get('skipped', '') and victim.read_bytes() == b'game wrote this', str(rows))
        check('T21a остальные файлы вернулись к скелету', sha(busy['cache'] / '1' / '1.db') == cache_before['1/1.db'] and receipt2.get('transplant') is True)

        # --- ревью: частичное владение, устаревший заголовок, устаревший журнал, остаток быстрого пути, прерванный unpack ---
        def installed_box(name: str) -> tuple[dict, str]:
            made = build_box(work / name)
            got = run(tool, made, payload, 'backup')
            lab = got.get('label', '')
            run(tool, made, payload, 'unpack', label=lab)
            done = run(tool, made, payload, 'verify', label=lab)
            assert done.get('status') == 'ok', done
            return made, lab

        def touch_log(made: dict, offset: float = 0.0) -> None:
            stamp = time.time() + offset
            os.utime(made['cache'].parent / 'QtsVFS.log', (stamp, stamp))

        check('T22 state.json пересадки: game_version = фактическая версия из .info (1.4.0.1), build_game_version = 1.6.0.1',
              state_file.get('game_version') == '1.4.0.1' and state_file.get('build_game_version') == '1.6.0.1', str(state_file))

        part, _ = installed_box('p1-partial')
        shutil.copyfile(SKELETON / '10/10.db', part['cache'] / '10' / '10.db')
        (part['cache'] / '10' / '10_0.db').unlink()
        touch_log(part, 30)
        before_part = snapshot(part['cache'])
        rows = run(tool, part, payload, 'check')
        check('T23 частичное владение (пакет 1 наш, пакет 10 снова скелет): stop reinstall, кэш не тронут',
              rows.get('code') == 'reinstall' and snapshot(part['cache']) == before_part, str(rows))

        stale, _ = installed_box('p2-stale-header')
        log_path = stale['cache'].parent / 'QtsVFS.log'
        log_path.write_text(re.sub(r'pkgHash:\d+ basePkgHash:\d+', 'pkgHash:5 basePkgHash:5', log_path.read_text('utf-8')), 'utf-8')
        before_stale = snapshot(stale['cache'])
        rows = run(tool, stale, payload, 'check')
        check('T24 SHA наши, но заголовок не равен pkgHash журнала: stop reinstall, кэш не тронут',
              rows.get('code') == 'reinstall' and snapshot(stale['cache']) == before_stale, str(rows))

        oldlog = build_box(work / 'p3-oldlog')
        touch_log(oldlog, -10 * 365 * 86400)
        rows = run(tool, oldlog, payload, 'check')
        check('T25 журнал старше файлов prime (хеш при этом совпадает): stop cache_missing', rows.get('code') == 'cache_missing', str(rows))
        newskel = build_box(work / 'p3-newskel')
        future = time.time() + 3600
        os.utime(newskel['cache'] / '1' / '1.db', (future, future))
        rows = run(tool, newskel, payload, 'check')
        check('T25a журнал старше скелета (хеш совпадает): stop cache_missing', rows.get('code') == 'cache_missing' and snapshot(newskel['cache']) != {}, str(rows))

        remnant = build_box(work / 'p6-remnant')
        entry = next(e for e in install['packages'] if e['id'] == '1')
        flat = next(f['payload'] for f in entry['files'] if f['name'] == '1.db')
        shutil.copyfile(payload / flat, remnant['cache'] / '1' / '1.db')
        before_remnant = snapshot(remnant['cache'])
        rows = run(tool, remnant, payload, 'check')
        check('T26 остаток быстрого пути (известный SHA 1.db) при другой версии: stop reinstall, кэш не тронут',
              rows.get('code') == 'reinstall' and snapshot(remnant['cache']) == before_remnant, str(rows))

        cut = build_box(work / 'p7-interrupted')
        cut_before = snapshot(cut['cache'])
        rows = run(tool, cut, payload, 'backup')
        cut_label = rows.get('label', '')
        cut_receipt_path = cut['data'] / 'backup' / cut_label / 'receipt.json'
        cut_receipt = json.loads(cut_receipt_path.read_text('utf-8-sig'))
        cut_receipt['status'] = 'applying'
        cut_receipt_path.write_text(json.dumps(cut_receipt, ensure_ascii=False, indent=1), 'utf-8')
        shutil.copyfile(cut['data'] / 'backup' / cut_label / 'built' / '1' / '1_0.db', cut['cache'] / '1' / '1_0.db')   # записан только файл данных
        rows = run(tool, cut, payload, 'restore')
        check('T27 прерванный unpack (status=applying, записан один файл) -> restore в режиме пересадки: status=ok', rows.get('status') == 'ok', str(rows))
        check('T27a кэш вернулся к скелетам байт-в-байт', snapshot(cut['cache']) == cut_before)
    finally:
        if args.keep:
            print('рабочая папка оставлена:', work)
        else:
            shutil.rmtree(work, ignore_errors=True)
    print('ИТОГО: ' + ('все проверки пройдены' if not failures else f'не прошли: {failures}'))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
