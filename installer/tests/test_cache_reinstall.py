"""Regression установки поверх неизвестного перевода и очистки соседних кэшей.

    python -X utf8 -B installer/tests/test_cache_reinstall.py

Самостоятельно собирает TESTBUILD помощника штатным csc и вызывает настоящий CLI:
check -> backup -> unpack -> verify -> restore. Вся игра, prime, SDK, индекс,
профили и receipt синтетические и лежат в tempfile. DLL не загружаются, EXE игры
не запускается, реестр и сеть не используются. Из build/ только читаются два
проверенных кандидата Lua 1.6.0.1: старый v13 и устанавливаемый v14.

--baseline-only запускает только точный случай ошибки «1914 запис(ей) кроме
заголовка». --tool-source позволяет проверить сохранённый исходник до правки,
не изменяя его и не отключая проверки процессов в C#.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'installer'))
sys.path.insert(0, str(PROJECT / 'scripts'))
import build_installer as bi  # noqa: E402
from game_inspect import QtsContainer  # noqa: E402

OLD = PROJECT / 'build/lua-wrap-2026-10-18-v13-1.6.0.1'
NEW = PROJECT / 'build/lua-wrap-2026-10-18-v14-1.6.0.1'
HEADER_ID = 0xe12bade797f29f55
RESOURCE_ID = 0x12345678abcdef01
PRIME = {'10/10.db': b'synthetic prime catalog 10', '10/10_0.db': b'synthetic prime storage 10'}
SDK = {'QtskDB.dll': b'synthetic SDK: never loaded', 'QtsVFS.dll': b'synthetic VFS: never loaded'}
INDEX = b'synthetic GlobalIndexPrime.data'
failures: list[str] = []


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def check(name: str, condition: bool, detail: object = '') -> bool:
    print(('  ok   ' if condition else '  FAIL ') + name + (f' [{detail}]' if not condition and detail else ''), flush=True)
    if not condition:
        failures.append(name)
    return bool(condition)


def write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def snapshot(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob('*')) if p.is_file()}


def header_of(path: Path) -> bytes:
    with QtsContainer(path, raw_values=True) as catalog:
        values = catalog.raw_record_values(HEADER_ID)
        assert len(values) == 1 and len(values[0]) == 120
        return values[0]


def header_hash(header: bytes, offset: int = 80) -> str:
    high, low = struct.unpack_from('<II', header, offset)
    return str((high << 32) | low)


def replace_header(path: Path, value: bytes) -> None:
    before = header_of(path)
    content = path.read_bytes()
    assert len(value) == len(before) and content.count(before) == 1
    path.write_bytes(content.replace(before, value))


def qts_container(records: list[tuple[int, bytes]], *, chunks: bool = False) -> bytes:
    """Маленькая валидная QTS база с одним индексным блоком; только тестовые данные."""
    assert 0 < len(records) <= 510
    result = bytearray(128 + 4096)
    for i in range(8):
        struct.pack_into('<ii', result, 16 + i * 8, -1, 0)
    struct.pack_into('<ii', result, 56, 80, 48)
    struct.pack_into('<ii', result, 80, 1, 1)
    struct.pack_into('<6i', result, 104, 128, -1, -1, 0, 0, 0)
    struct.pack_into('<iiiHH', result, 128 + 4080, 128, 0, -1, len(records), 1)
    for i, (identifier, value) in enumerate(records):
        offset = len(result)
        struct.pack_into('<i', result, 128 + 2040 + i * 4, offset)
        record = bytearray(32 if chunks else 28)
        struct.pack_into('<ii', record, 0, offset, -1)
        if chunks:
            # LZ4-блок, состоящий только из литерала; во всех фикстурах < 15 байт.
            assert len(value) < 15
            compressed = bytes([len(value) << 4]) + value
            struct.pack_into('<ii', record, 24, len(compressed) + 4, len(value))
            record.extend(compressed)
        else:
            struct.pack_into('<I', record, 24, len(value))
            record.extend(value)
        record.extend(b'\0' * ((-len(record)) % 4))
        record.extend(struct.pack('<Qii', identifier, 0, 0) if chunks else struct.pack('<Q', identifier))
        result.extend(record)
    struct.pack_into('<Qii', result, 0, 0x0403020102000001, len(result), len(result))
    return bytes(result)


def load_candidate(directory: Path) -> dict:
    manifest = json.loads((directory / 'manifest.json').read_text('utf-8-sig'))
    assert manifest['game_version'] == '1.6.0.1' and manifest['packages'] == ['10']
    for row in manifest['files']:
        path = directory / row['relative']
        assert path.stat().st_size == row['bytes'] and sha(path) == row['sha256'], path
    return manifest


def make_payload(work: Path) -> tuple[Path, dict]:
    """Сборка install.json без combine/load_candidate: они проверяют настоящую игру."""
    old, new = load_candidate(OLD), load_candidate(NEW)
    assert old['override_package_headers_hex'] == new['override_package_headers_hex']
    assert len(old['resources']) == len(new['resources']) == 1914
    assert sha(OLD / 'packages/10/10.db') != sha(NEW / 'packages/10/10.db')
    assert sha(OLD / 'packages/10/10_0.db') != sha(NEW / 'packages/10/10_0.db')
    missing: list[str] = []
    entry = {'id': '10', 'prime_pkg_hash': new['prime_pkg_hashes']['10'],
             'header_hex': new['override_package_headers_hex']['10'],
             'info_sha256': new['official_cache_infos']['10']['sha256'], 'info_version': new['game_version'],
             'resources': bi.package_resources(NEW, new, '10', missing), 'files': []}
    assert not missing
    payload = work / 'payload'
    payload.mkdir()
    for row in new['files']:
        name = Path(row['relative']).name
        flat = 'p10_' + name
        shutil.copyfile(NEW / row['relative'], payload / flat)
        entry['files'].append({'name': name, 'bytes': row['bytes'], 'sha256': row['sha256'], 'payload': flat})
    install = {'product': 'WXQ cache reinstall regression', 'version': 'test', 'built': '2026-10-20',
               'game_version': new['game_version'], 'index_sha256': digest(INDEX),
               'sdk_hashes': {name: digest(data) for name, data in SDK.items()}, 'sdk_known': [],
               'candidates': ['synthetic-prime-test'], 'packages': [entry],
               'prime': [{'relative': name, 'bytes': len(data), 'sha256': digest(data)} for name, data in PRIME.items()],
               'known': {}}
    (payload / 'install.json').write_text(json.dumps(install, ensure_ascii=False), 'utf-8')
    return payload, install


def build_tool(work: Path, source: Path | None) -> Path:
    folder = work / 'tool'
    folder.mkdir()
    if source is None:
        bi.build_tool(folder, test_build=True)
    else:
        done = subprocess.run([str(bi.CSC), '/nologo', '/target:exe', '/platform:x64', '/optimize+', '/codepage:65001',
                               '/define:TESTBUILD', '/r:System.Web.Extensions.dll', '/r:Microsoft.CSharp.dll',
                               f'/resource:{PROJECT / "installer/tests/fixtures/10.db.placeholder"},cache-template.db',
                               f'/out:{folder / "WXQTool.exe"}', str(source.resolve()),
                               str(PROJECT / 'installer/WXQCompatText.cs'), str(PROJECT / 'installer/WXQCompatPack.cs')], capture_output=True, timeout=120)
        assert done.returncode == 0, (done.stdout + done.stderr).decode('cp866', errors='replace')
    return folder / 'WXQTool.exe'


class Sandbox:
    def __init__(self, root: Path, install: dict):
        self.root, self.install = root, install
        self.game = root / 'Лаунчер с пробелом' / 'Игра WXQ(1)'
        self.data = root / 'data'
        self.cache = self.game / 'QtsDataFolder/QtsVFSCache/packages'
        self.mounts = {'10': install['packages'][0]['prime_pkg_hash']}
        write(self.game / 'WXQ.exe', b'synthetic game marker, never executed')
        for name, content in PRIME.items():
            write(self.game / 'WXQ_Data/Package/builtin' / name, content)
        for name, content in SDK.items():
            write(self.game / 'WXQ_Data/Plugins/x86_64' / name, content)
        write(self.cache / 'builtin/Globalindex/GlobalIndexPrime.data', INDEX)
        for row in load_candidate(OLD)['files']:
            write(self.cache / '10' / Path(row['relative']).name, (OLD / row['relative']).read_bytes())
        # Имена вне строгого шаблона пакета и хранилища не принадлежат операции.
        for name in ('10/readme.txt', '10/10_1.info', '10/10_2.db.txt', '10/unknown.db',
                     'notes/notes.db', '01/01.db', '01/01_0.db', 'other/unrelated.bin'):
            write(self.cache / name, ('keep ' + name).encode())
        self.refresh_log()

    def refresh_log(self) -> None:
        content = ''.join(f'MountPackage {p}, prime, 7 => succeed! test pkgHash:{h} basePkgHash:{h}\n' for p, h in self.mounts.items())
        write(self.cache.parent / 'QtsVFS.log', content.encode())
        # Точность времён файлов отличается между Windows FS; журнал заведомо свежее.
        os.utime(self.cache.parent / 'QtsVFS.log', (time.time() + 2, time.time() + 2))

    def add_storages(self, package: str) -> None:
        for suffix in ('1.db', '2.db', '003.db', '4.DB'):
            write(self.cache / package / f'{package}_{suffix}', qts_container([(RESOURCE_ID, b'old-cache')], chunks=True))

    def add_neighbor(self, package: str = '77') -> None:
        header = bytearray(bytes.fromhex(self.install['packages'][0]['header_hex']))
        struct.pack_into('<I', header, 16, int(package))
        for offset in (80, 88):
            struct.pack_into('<II', header, offset, 0x12345678, int(package) + 1)
        write(self.cache / package / f'{package}.db', qts_container([(HEADER_ID, bytes(header)), (RESOURCE_ID, bytes(56))]))
        write(self.cache / package / f'{package}_0.db', qts_container([(RESOURCE_ID, b'old-cache')], chunks=True))
        write(self.cache / package / f'{package}.info', json.dumps({'version': self.install['game_version'], 'revision': '0'}).encode())
        write(self.cache / package / f'{package}.txt', b'keep adjacent package notes')
        for name in (f'{package}.db', f'{package}_0.db'):
            write(self.game / 'WXQ_Data/Package/builtin' / package / name, b'synthetic adjacent prime')
        self.mounts[package] = header_hash(header)
        self.add_storages(package)
        self.refresh_log()

    def run(self, tool: Path, payload: Path, command: str, **extra: str) -> dict[str, str]:
        output = self.root / 'result.txt'
        output.unlink(missing_ok=True)
        args = [str(tool), command, '--payload', str(payload), '--data', str(self.data), '--out', str(output), '--game', str(self.game)]
        for name, value in extra.items():
            args.extend(['--' + name, value])
        env = dict(os.environ, WXQ_RU_USERS_ROOT=str(self.root / 'no-users'),
                   WXQ_RU_TEST_CLOSE_ROOT=str(self.root), WXQ_RU_TEST_NOKILL='1', APPDATA=str(self.root / 'profile'))
        done = subprocess.run(args, capture_output=True, env=env, timeout=120)
        rows = {'_exit': str(done.returncode)}
        if output.is_file():
            for line in output.read_text('utf-8-sig').splitlines():
                name, _, value = line.partition('=')
                rows[name] = value
        return rows

    def receipt(self, label: str) -> dict:
        return json.loads((self.data / 'backup' / label / 'receipt.json').read_text('utf-8-sig'))


def ok(box: Sandbox, tool: Path, payload: Path, command: str, name: str, **extra: str) -> dict[str, str] | None:
    rows = box.run(tool, payload, command, **extra)
    return rows if check(name, rows.get('status') == 'ok' and rows['_exit'] == '0', rows) else None


def install_restore(box: Sandbox, tool: Path, payload: Path, name: str, neighbors: tuple[str, ...] = ()) -> None:
    before = snapshot(box.game)
    rows = ok(box, tool, payload, 'check', name + ': check принимает старый перевод')
    check(name + ': check не пишет в игру', snapshot(box.game) == before)
    if rows is None:
        return
    rows = ok(box, tool, payload, 'backup', name + ': backup')
    if rows is None:
        return
    label = rows.get('label', '')
    if not check(name + ': есть receipt', bool(label)):
        return
    check(name + ': backup не пишет в игру', snapshot(box.game) == before)
    receipt = box.receipt(label)
    touched = {row['relative']: row for row in receipt['operations']}
    for package in ('10',) + neighbors:
        for suffix in ('1.db', '2.db', '003.db', '4.DB'):
            relative = f'packages/{package}/{package}_{suffix}'
            target = box.cache / package / f'{package}_{suffix}'
            if target.is_file():
                check(name + ': receipt хранит удаление ' + relative,
                      relative in touched and touched[relative]['before_sha256'] == sha(target)
                      and touched[relative]['candidate_sha256'] is None, touched.get(relative))
    if ok(box, tool, payload, 'unpack', name + ': unpack', label=label) is None:
        return
    installed = snapshot(box.game)
    expected = {f"QtsDataFolder/QtsVFSCache/{key}" for key in touched}
    changes = {key for key in before.keys() | installed.keys() if before.get(key) != installed.get(key)}
    check(name + ': изменились только файлы из receipt', changes <= expected, changes - expected)
    preserved = {key: value for key, value in before.items()
                 if key.startswith('QtsDataFolder/QtsVFSCache/packages/')
                 and not re.fullmatch(r'QtsDataFolder/QtsVFSCache/packages/(0|[1-9][0-9]*)/\1(?:_[0-9]+)?\.db', key, re.IGNORECASE)}
    check(name + ': .info, .txt, неизвестные файлы и папки 01/notes сохранены',
          all(installed.get(key) == value for key, value in preserved.items()))
    for package in ('10',) + neighbors:
        check(name + f': пакет {package} без старых numbered storages',
              not any((box.cache / package / f'{package}_{suffix}').exists() for suffix in ('1.db', '2.db', '003.db', '4.DB')))
    for package in neighbors:
        with QtsContainer(box.cache / package / f'{package}.db', raw_values=True) as catalog:
            check(name + f': сосед {package} содержит только прежний header',
                  set(catalog.entries) == {HEADER_ID} and catalog.raw_record_values(HEADER_ID) == [bytes.fromhex(before_header(receipt, box, package))])
        check(name + f': у соседа {package} удалён storage 0', not (box.cache / package / f'{package}_0.db').exists())
    ok(box, tool, payload, 'verify', name + ': verify', label=label)
    rows = ok(box, tool, payload, 'backup', name + ': повторная установка идемпотентна')
    check(name + ': для совпавшей установки не создаётся новая копия', rows is not None and rows.get('state') == 'same' and not rows.get('label'), rows)
    ok(box, tool, payload, 'restore', name + ': restore')
    check(name + ': весь исходный game byte-identical после restore', snapshot(box.game) == before)


def before_header(receipt: dict, box: Sandbox, package: str) -> str:
    return header_of(box.data / 'backup' / receipt['label'] / 'packages' / package / f'{package}.db').hex()


def reject(box: Sandbox, tool: Path, payload: Path, name: str, codes: set[str], reasons: tuple[str, ...]) -> None:
    before = snapshot(box.game)
    for command in ('check', 'backup'):
        rows = box.run(tool, payload, command)
        message = rows.get('message', '')
        check(name + ': ' + command + ' отказал по ожидаемой причине',
              rows.get('status') == 'stop' and rows['_exit'] == '2' and rows.get('code') in codes
              and all(reason in message for reason in reasons), rows)
        check(name + ': ' + command + ' не пишет в игру', snapshot(box.game) == before)
    check(name + ': резервная копия не создана', not list(box.data.glob('backup/*/receipt.json')))


@contextmanager
def deny_replace(path: Path):
    """Сбой записи средствами Windows: чтение разрешено, atomic replace запрещён."""
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                              ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    api.CreateFileW.restype = ctypes.c_void_p
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = api.CreateFileW(str(path), 0x80000000, 1, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        api.CloseHandle(handle)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline-only', action='store_true')
    parser.add_argument('--tool-source', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='wxq-cache-reinstall-') as temporary:
        work = Path(temporary)
        tool = build_tool(work, args.tool_source)
        payload, install = make_payload(work)
        box = Sandbox(work / 'unknown-1914', install)
        with QtsContainer(box.cache / '10/10.db', raw_values=True) as catalog:
            check('Фикстура: ровно 1914 неизвестных записи, known={}', len(catalog.entries) - 1 == 1914 and install['known'] == {})
        if args.baseline_only:
            before = snapshot(box.game)
            rows = box.run(tool, payload, 'check')
            print('BASELINE ' + json.dumps(rows, ensure_ascii=False), flush=True)
            if rows.get('status') != 'ok':
                check('1914: воспроизведён именно исходный отказ foreign1914',
                      rows.get('status') == 'stop' and rows.get('code') == 'foreign' and rows['_exit'] == '2'
                      and 'пакета 10' in rows.get('message', '') and '1914 запис(ей) кроме заголовка' in rows.get('message', ''), rows)
            check('1914: check должен принимать совпавший header/prime/info', rows.get('status') == 'ok', rows)
            check('1914: отказ или check не меняет игру', snapshot(box.game) == before)
        else:
            install_restore(box, tool, payload, '1914')
            box = Sandbox(work / 'numbered-and-neighbors', install)
            box.add_storages('10')
            for package in ('77', '0'):
                box.add_neighbor(package)
            install_restore(box, tool, payload, 'Нумерованные хранилища и соседи', ('77', '0'))

            box = Sandbox(work / 'wrong-target-header', install)
            header = bytearray(header_of(box.cache / '10/10.db'))
            header[80] ^= 1
            replace_header(box.cache / '10/10.db', bytes(header))
            reject(box, tool, payload, 'Другой header целевого пакета', {'foreign'}, ('пакета 10', 'официаль'))

            box = Sandbox(work / 'official-hotfix', install)
            box.add_neighbor()
            header = bytearray(header_of(box.cache / '77/77.db'))
            header[80] ^= 1
            replace_header(box.cache / '77/77.db', bytes(header))
            box.refresh_log()
            reject(box, tool, payload, 'Официальный hotfix: pkgHash != basePkgHash', {'foreign'}, ('пакета 77', 'официаль'))

            box = Sandbox(work / 'missing-prime-log', install)
            box.add_neighbor()
            del box.mounts['77']
            box.refresh_log()
            reject(box, tool, payload, 'Неподтверждённый сосед: нет prime в журнале',
                   {'foreign', 'cache_missing'}, ('MountPackage 77', 'нет строки'))

            box = Sandbox(work / 'stale-prime-log', install)
            box.add_neighbor()
            os.utime(box.cache.parent / 'QtsVFS.log', (time.time() - 60, time.time() - 60))
            reject(box, tool, payload, 'Неподтверждённый сосед: устаревший журнал',
                   {'foreign', 'cache_missing'}, ('пакета 77', 'старше'))

            box = Sandbox(work / 'official-update-before-restore', install)
            box.add_storages('10')
            rows = ok(box, tool, payload, 'backup', 'Обновление перед restore: backup грязного пакета')
            if rows and rows.get('label'):
                label = rows['label']
                unpacked = ok(box, tool, payload, 'unpack', 'Обновление перед restore: установка', label=label)
                if unpacked is not None:
                    ok(box, tool, payload, 'verify', 'Обновление перед restore: проверка установки', label=label)
                    check('Обновление перед restore: прежние _1/_2 удалены установщиком',
                          all(not (box.cache / '10' / f'10_{n}.db').exists() for n in (1, 2)))
                    # Официальный hotfix меняет каталог и данные, не возвращая старые _1/_2.
                    # Новый каталог имеет pkgHash != basePkgHash и единственный свой ресурс.
                    header = bytearray(header_of(box.cache / '10/10.db'))
                    header[80] ^= 1
                    write(box.cache / '10/10.db', qts_container([(HEADER_ID, bytes(header)), (RESOURCE_ID, bytes(56))]))
                    write(box.cache / '10/10_0.db', qts_container([(RESOURCE_ID, b'official-new')], chunks=True))
                    official = snapshot(box.game)
                    refused = box.run(tool, payload, 'check')
                    check('Обновление перед restore: check отказал именно из-за официального пакета 10',
                          refused.get('status') == 'stop' and refused['_exit'] == '2' and refused.get('code') == 'foreign'
                          and 'пакета 10' in refused.get('message', '') and 'официаль' in refused.get('message', ''), refused)
                    check('Обновление перед restore: отказ не изменил официальные файлы', snapshot(box.game) == official)
                    ok(box, tool, payload, 'restore', 'Обновление перед restore: restore завершён безопасно')
                    check('Обновление перед restore: удалённые старые _1/_2 не возвращаются',
                          all(not (box.cache / '10' / f'10_{n}.db').exists() for n in (1, 2)))
                    check('Обновление перед restore: весь официальный пакет сохранён byte-identical',
                          snapshot(box.game) == official)

            box = Sandbox(work / 'failed-write', install)
            box.add_storages('10')
            box.add_neighbor()
            before = snapshot(box.game)
            rows = ok(box, tool, payload, 'backup', 'Сбой записи: backup')
            if rows and rows.get('label'):
                with deny_replace(box.cache / '10/10.db'):
                    result = box.run(tool, payload, 'unpack', label=rows['label'])
                check('Сбой записи: автоматический откат', result.get('code') == 'apply_reverted', result)
                check('Сбой записи: все исходные файлы byte-identical', snapshot(box.game) == before)
                check('Сбой записи: receipt закрыт как restored', box.receipt(rows['label'])['status'] == 'restored')
    print('ИТОГО: ' + ('все проверки пройдены' if not failures else f'не прошли: {len(failures)}'), flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
