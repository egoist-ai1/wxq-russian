"""Неоконный тест разбора override-пакетов в WXQTool.exe (команда parse-packages) и полей resources в install.json.

    python -X utf8 -B installer/tests/test_override_parse.py

Собирает WXQTool.exe во временную папку, читает пары 1.db/1_0.db (carrier-2026-10-14-v7) и 10.db/10_0.db (lua-wrap-2026-10-14-v9)
кодом C#, сверяет SHA декодированных нагрузок СО ВСЕМИ ресурсами манифеста, а 20 случайных id на пакет - с Python-разбором
(game_inspect.QtsContainer: SHA, размер, число чанков, 56 байт метаданных). Ни игры, ни окон, ни записи вне временной папки.
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'installer'))
sys.path.insert(0, str(PROJECT / 'scripts'))
import build_installer as bi  # noqa: E402
from game_inspect import QtsContainer  # noqa: E402

CASES = (('1', PROJECT / 'build/carrier-2026-10-14-v7'), ('10', PROJECT / 'build/lua-wrap-2026-10-14-v9'))
failures: list[str] = []


def check(name: str, condition: bool, detail: str = '') -> None:
    print(('  ok   ' if condition else '  FAIL ') + name + ('' if condition else f'  [{detail}]'))
    if not condition:
        failures.append(name)


def run_tool(exe: Path, out: Path, pairs: list[tuple[Path, Path]]) -> dict[str, str]:
    out.unlink(missing_ok=True)
    value = ';'.join(f'{a}|{b}' for a, b in pairs)
    subprocess.run([str(exe), 'parse-packages', '--pairs', value, '--out', str(out)], capture_output=True, timeout=600)
    rows = {}
    if out.is_file():
        for line in out.read_text('utf-8-sig').splitlines():
            name, _, text = line.partition('=')
            rows[name] = text
    return rows


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix='wxq-parse-test-'))
    try:
        bi.build_tool(work, test_build=True)
        exe = work / 'WXQTool.exe'
        pairs, manifests = [], []
        for package, directory in CASES:
            manifests.append(json.loads((directory / 'manifest.json').read_text('utf-8-sig')))
            pairs.append((directory / f'packages/{package}/{package}.db', directory / f'packages/{package}/{package}_0.db'))
        rows = run_tool(exe, work / 'out.txt', pairs)
        check('T0 команда завершилась со status=ok', rows.get('status') == 'ok', str(rows.get('message')))
        rng = random.Random(20261017)
        for index, ((package, directory), manifest) in enumerate(zip(CASES, manifests)):
            resources = [r for r in manifest['resources'] if r['package'] == package]
            head = rows.get(f'pkg.{index}', '').split('|')
            check(f'T1 пакет {package}: разобран (ok), число ресурсов = манифесту ({len(resources)})', head[0] == 'ok' and head[1:2] == [str(len(resources))], str(head))
            header_sha = hashlib.sha256(bytes.fromhex(manifest['override_package_headers_hex'][package])).hexdigest()
            check(f'T2 пакет {package}: SHA заголовка = манифесту', len(head) == 3 and head[2] == header_sha)
            parsed = {key.split('.', 2)[2]: value.split('|') for key, value in rows.items() if key.startswith(f'res.{index}.')}
            check(f'T3 пакет {package}: набор id = манифесту', set(parsed) == {r['id_hex'] for r in resources})
            wrong = [r['id_hex'] for r in resources if parsed.get(r['id_hex'], [''])[0] != r['sha256']]
            check(f'T4 пакет {package}: SHA декодированной нагрузки = sha256 манифеста у ВСЕХ {len(resources)} ресурсов', not wrong, str(wrong[:5]))
            meta_path, data_path = pairs[index]
            with QtsContainer(meta_path, raw_values=True) as meta, QtsContainer(data_path) as data:
                sample = rng.sample(sorted(parsed), min(20, len(parsed)))
                bad = []
                for ident in sample:
                    number = int(ident, 16)
                    decoded = data.decode_entry(number)
                    expect = [hashlib.sha256(decoded).hexdigest(), str(len(decoded)), str(len(data.entries[number])), meta.raw_record_values(number)[0].hex()]
                    if parsed[ident] != expect:
                        bad.append(ident)
                check(f'T5 пакет {package}: {len(sample)} случайных id совпали с Python-разбором (SHA, размер, чанки, метаданные)', not bad, str(bad))

        # Нестандартный формат: усечённое хранилище не должно ронять помощника, пакет пропускается (skip), соседний читается.
        package, directory = CASES[0]
        broken = work / 'broken_0.db'
        data = (directory / f'packages/{package}/{package}_0.db').read_bytes()
        broken.write_bytes(data[: len(data) // 2])
        rows = run_tool(exe, work / 'out2.txt', [(pairs[0][0], broken), pairs[1]])
        check('T6 усечённый <N>_0.db: пакет пропущен (skip), помощник не упал, второй пакет разобран',
              rows.get('status') == 'ok' and rows.get('pkg.0', '').startswith('skip|') and rows.get('pkg.1', '').startswith('ok|'), str({k: v for k, v in rows.items() if k.startswith(('pkg', 'status', 'code', 'message'))}))
        swapped = run_tool(exe, work / 'out3.txt', [(pairs[0][1], pairs[0][0])])
        check('T7 файлы перепутаны местами: skip, не крах', swapped.get('status') == 'ok' and swapped.get('pkg.0', '').startswith('skip|'), str({k: v for k, v in swapped.items() if not k.startswith('res.')}))

        # install.json: новые поля resources из combine (только carrier: кандидат Lua требует игру для повторной сборки).
        install, _files = bi.combine([CASES[0][1]], 'test', '2026-01-01')
        entry = install['packages'][0]
        manifest = manifests[0]
        expected = {r['id_hex']: (r['kind'], r['original_sha256'], r['sha256']) for r in manifest['resources'] if r['package'] == '1'}
        got = {r['id_hex']: (r['kind'], r['original_sha256'], r['sha256']) for r in entry['resources']}
        check('T8 install.json: resources каждого пакета = манифест (id_hex, kind, original_sha256, sha256)', got == expected)
        with QtsContainer(pairs[0][1]) as container:
            sizes_ok = all(r['bytes'] == sum(c.uncompressed_size for c in container.entries[int(r['id_hex'], 16)]) and r['chunks'] == len(container.entries[int(r['id_hex'], 16)]) for r in entry['resources'])
        check('T9 install.json: bytes и chunks совпадают с хранилищем', sizes_ok)
        check('T10 старые поля install.json на месте (game_version, files, header_hex)', install['game_version'] == manifest['game_version'] and entry['files'] and entry['header_hex'])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('ИТОГО: ' + ('все проверки пройдены' if not failures else f'не прошли: {failures}'))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
