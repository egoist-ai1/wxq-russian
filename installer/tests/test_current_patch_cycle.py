"""Полный цикл текущего payload на замороженном снимке реального патча.

Запускать только через isolated_test_run.py: отдельный невидимый desktop и Job.
Тест не запускает игру, лаунчер, Inno Setup и не обращается к живой установке.
"""
from __future__ import annotations

import argparse
from collections import Counter
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[2]
for item in (PROJECT, PROJECT / 'installer', PROJECT / 'scripts'):
    sys.path.insert(0, str(item))
import build_installer as bi  # noqa: E402
from game_inspect import QtsContainer  # noqa: E402


def sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', 'utf-8')


def snapshot(folder: Path) -> dict[str, str]:
    return {path.relative_to(folder).as_posix(): sha(path)
            for path in sorted(folder.rglob('*')) if path.is_file()}


def isolation_proof() -> dict:
    """До компиляции/запуска EXE требуем окружение штатной обёртки."""
    if os.name != 'nt':
        raise RuntimeError('Этот тест предназначен только для Windows')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetCurrentThreadId.restype = wintypes.DWORD
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    user.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user.GetThreadDesktop.restype = wintypes.HANDLE
    user.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                              wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    user.GetUserObjectInformationW.restype = wintypes.BOOL
    in_job = wintypes.BOOL()
    if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(in_job)):
        raise ctypes.WinError(ctypes.get_last_error())
    desktop = user.GetThreadDesktop(kernel.GetCurrentThreadId())
    name = ctypes.create_unicode_buffer(256)
    needed = wintypes.DWORD()
    if not user.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not in_job.value or not re.fullmatch(r'WXQTest-[0-9a-f]{32}', name.value):
        raise RuntimeError('Запустите тест через installer/tests/isolated_test_run.py')
    return {'private_desktop': name.value, 'is_process_in_job': bool(in_job.value),
            'switch_desktop_called': False}


class Probe:
    def __init__(self, args):
        self.work = args.work.resolve()
        self.source = args.snapshot.resolve(strict=True)
        self.staging = args.staging.resolve(strict=True)
        self.report = args.report.resolve()
        temporary = Path(tempfile.gettempdir()).resolve()
        for name, path in (('work', self.work), ('snapshot', self.source), ('staging', self.staging)):
            if not path.is_relative_to(temporary) or path == temporary:
                raise ValueError(name + ' должен находиться внутри Temp')
        if self.work.exists() or self.work.is_relative_to(self.source) or self.source.is_relative_to(self.work):
            raise ValueError('Для work нужна новая отдельная папка Temp')
        if self.staging.is_relative_to(self.work) or self.work.is_relative_to(self.staging):
            raise ValueError('work и staging должны быть отдельными папками')
        self.isolation = isolation_proof()
        self.work.mkdir(parents=True, exist_ok=False)
        self.report.mkdir(parents=True, exist_ok=True)
        self.version = args.version
        self.game_version = args.game_version
        self.resource_count = args.resources
        self.checks = []
        self.commands = []
        self.game = self.work / 'box/Игра WXQ(1)'
        self.data = self.work / 'box/data'
        self.cache = self.game / 'QtsDataFolder/QtsVFSCache/packages'
        self.payload = self.work / 'payload'
        self.install = json.loads((self.staging / 'install.json').read_text('utf-8-sig'))
        self.counts = {}
        self.resource_rows = []
        self.baseline = None
        self.source_hashes = {}
        save(self.report / 'child-isolation.json', self.isolation)

    def check(self, name: str, passed: bool, detail: object = '') -> None:
        self.checks.append({'name': name, 'passed': bool(passed), 'detail': detail})
        print(('PASS ' if passed else 'FAIL ') + name, flush=True)
        if not passed:
            raise AssertionError(name + ': ' + str(detail))

    def prepare(self) -> None:
        mapping = json.loads((self.source.parent / 'mapping.json').read_text('utf-8'))
        self.check('Snapshot полностью снят с настоящего патча', mapping.get('complete') is True)
        expected = {row['target_relative'].replace('\\', '/'): row['copy_sha256'] for row in mapping['files']}
        wrong_time = [row['target_relative'] for row in mapping['files']
                      if (self.source / row['target_relative']).stat().st_mtime_ns != row['source_mtime_utc_after_ns']]
        self.check('Snapshot сохранил исходные mtime, включая порядок журналов', not wrong_time, wrong_time[:20])
        self.source_hashes = snapshot(self.source)
        self.check('Snapshot совпал с исходным SHA-256 manifest', self.source_hashes == expected, len(expected))
        self.check('Версия install.json соответствует выпуску и патчу',
                   self.install['version'] == self.version and self.install['game_version'] == self.game_version,
                   {'version': self.install['version'], 'game_version': self.install['game_version']})
        packages = self.install['packages']
        self.check('В payload ровно пять ожидаемых пакетов', {row['id'] for row in packages} == {'1', '4', '10', '15', '19'})
        self.check('Число ресурсов совпало с проверяемым выпуском',
                   sum(len(row['resources']) for row in packages) == self.resource_count, self.resource_count)
        for source in sorted(self.source.rglob('*')):
            if source.is_file():
                target = self.game / source.relative_to(self.source)
                target.parent.mkdir(parents=True, exist_ok=True)
                # Даже prime копируем отдельно: ошибка тестируемого кода не сможет
                # изменить исходный снимок через жёсткую ссылку.
                shutil.copy2(source, target)
        self.check('Копия игры побайтно равна snapshot', snapshot(self.game) == self.source_hashes)
        wrong_time = [str(path.relative_to(self.source)) for path in self.source.rglob('*') if path.is_file()
                      and path.stat().st_mtime_ns != (self.game / path.relative_to(self.source)).stat().st_mtime_ns]
        self.check('Копирование сохранило все mtime', not wrong_time, wrong_time[:20])
        self.payload.mkdir()
        shutil.copy2(self.staging / 'install.json', self.payload / 'install.json')
        references = {}
        for package in packages:
            for row in package['files']:
                references[row['payload']] = row
            for row in package['resources']:
                if 'text_reuse' in row:
                    reference = row['text_reuse']
                    references[reference['payload']] = reference
        sources = {}
        for line in (self.staging / 'payload-files.iss').read_text('utf-8-sig').splitlines():
            match = re.match(r'Source: "([^"]+)"; DestName: "([^"]+)";', line)
            if match and match[2] in references:
                if match[2] in sources:
                    raise ValueError('Повторная ссылка payload: ' + match[2])
                sources[match[2]] = Path(match[1]).resolve(strict=True)
        self.check('Все ссылки install.json присутствуют в Inno payload', set(sources) == set(references))
        for flat, source in sources.items():
            if Path(flat).name != flat:
                raise ValueError('Ожидалось плоское имя payload: ' + flat)
            target = self.payload / flat
            shutil.copy2(source, target)
            row = references[flat]
            if target.stat().st_size != row['bytes'] or sha(target) != row['sha256']:
                raise AssertionError('Повреждён payload: ' + flat)
        self.check('Все payload-файлы и text sidecar совпали по размеру и SHA', True, len(references))
        tool_dir = self.work / 'tool'
        tool_dir.mkdir()
        receipt = {name: sha(PROJECT / 'installer' / name)
                   for name in ('WXQTool.cs', 'WXQCompatText.cs', 'WXQCompatPack.cs')}
        receipt['staging_tool_sha256'] = sha(self.staging / 'WXQTool.exe')
        receipt['staging_manifest_sha256'] = sha(self.staging / 'install.json')
        # Тот же production-код, но test guard ограничивает сканирование процессов
        # нашей папкой. Живые процессы игры/лаунчера не выбираются и не закрываются.
        bi.build_tool(tool_dir, test_build=True)
        self.tool = tool_dir / 'WXQTool.exe'
        receipt['test_tool_sha256'] = sha(self.tool)
        receipt['test_build'] = True
        save(self.report / 'compiled-sources.json', receipt)
        self.prime_before = {key: value for key, value in self.source_hashes.items()
                             if key.startswith('WXQ_Data/Package/builtin/')}

    def run(self, command: str, **extra: str) -> dict:
        number = len(self.commands) + 1
        output = self.work / f'{number:02d}-{command}.txt'
        args = [str(self.tool), command, '--payload', str(self.payload), '--data', str(self.data),
                '--game', str(self.game), '--out', str(output)]
        for key, value in extra.items():
            args += ['--' + key, value]
        env = dict(os.environ, WXQ_RU_TEST_CLOSE_ROOT=str(self.work), WXQ_RU_USERS_ROOT=str(self.work / 'no-users'))
        started = time.monotonic()
        done = subprocess.run(args, capture_output=True, timeout=1800, env=env)
        rows = {'_exit': done.returncode, '_seconds': round(time.monotonic() - started, 3)}
        if output.exists():
            rows.update(line.partition('=')[::2] for line in output.read_text('utf-8-sig').splitlines() if '=' in line)
        (self.report / f'{number:02d}-{command}-stdout.bin').write_bytes(done.stdout)
        (self.report / f'{number:02d}-{command}-stderr.bin').write_bytes(done.stderr)
        save(self.report / f'{number:02d}-{command}.json', rows)
        self.commands.append({'command': command, 'result': rows})
        print(json.dumps({'command': command, 'result': rows}, ensure_ascii=False), flush=True)
        return rows

    def ok(self, command: str, **extra: str) -> dict:
        result = self.run(command, **extra)
        self.check(command + ' завершился успешно', result.get('status') == 'ok' and result['_exit'] == 0, result)
        return result

    def verify_expected(self) -> None:
        """Независимый Python parser читает каждый ресурс prime/payload/result."""
        counts = Counter()
        for package in self.install['packages']:
            package_id = package['id']
            wanted = {int(row['id_hex'], 16): row for row in package['resources']}
            self.check('Пакет ' + package_id + ': в manifest нет дубликатов', len(wanted) == len(package['resources']))
            original_hashes = {}
            prime = self.game / 'WXQ_Data/Package/builtin' / package_id
            for path in sorted(prime.glob(package_id + '_*.db')):
                with QtsContainer(path) as container:
                    for ident in set(container.entries) & set(wanted):
                        if ident in original_hashes:
                            raise AssertionError('Повторный исходный ресурс: ' + f'{ident:016x}')
                        original_hashes[ident] = digest(container.decode_entry(ident))
            self.check('Пакет ' + package_id + ': все оригиналы найдены в текущем prime', set(original_hashes) == set(wanted))
            self.check('Пакет ' + package_id + ': все исходные SHA относятся к реальному патчу',
                       all(original_hashes[ident] == row['original_sha256'] for ident, row in wanted.items()))
            storage = self.payload / next(row['payload'] for row in package['files'] if row['name'] == package_id + '_0.db')
            with QtsContainer(storage) as payload, QtsContainer(self.cache / package_id / (package_id + '_0.db')) as result:
                self.check('Пакет ' + package_id + ': payload содержит только ожидаемые ресурсы', set(payload.entries) == set(wanted))
                self.check('Пакет ' + package_id + ': результат содержит только ожидаемые ресурсы', set(result.entries) == set(wanted))
                for ident, row in wanted.items():
                    expected = payload.decode_entry(ident)
                    actual = result.decode_entry(ident)
                    if len(expected) != row['bytes'] or digest(expected) != row['sha256']:
                        raise AssertionError('Payload не совпал с manifest: ' + row['id_hex'])
                    if actual != expected or len(result.entries[ident]) != row['chunks']:
                        raise AssertionError('Ресурс в игре не совпал с payload: ' + row['id_hex'])
                    self.resource_rows.append({'package': package_id, 'id_hex': row['id_hex'], 'kind': row['kind'],
                                               'original_sha256': original_hashes[ident], 'sha256': digest(actual),
                                               'bytes': len(actual), 'chunks': len(result.entries[ident])})
                    counts['resources'] += 1
                    counts['bytes'] += len(actual)
                    counts['kind_' + row['kind']] += 1
                self.check('Пакет ' + package_id + ': каждый ресурс совпал побайтно/SHA/chunks', True, len(wanted))
            for row in package['files']:
                self.check('Пакет ' + package_id + ': точный установленный файл ' + row['name'],
                           sha(self.cache / package_id / row['name']) == row['sha256'])
            counts['packages'] += 1
        self.counts = dict(counts)
        self.check('Независимо прочитаны все ресурсы выпуска', counts['resources'] == self.resource_count, self.counts)
        save(self.report / 'independent-readback.json', {'counts': self.counts, 'resources': self.resource_rows})

    def execute(self) -> None:
        self.prepare()
        self.baseline = self.ok('check')
        save(self.report / 'baseline.json', self.baseline)
        self.check('Check сообщает фактическую и build версию нового патча',
                   self.baseline.get('game_version') == self.game_version and
                   self.baseline.get('build_game_version') == self.game_version, self.baseline)
        self.check('check не меняет игровые файлы', snapshot(self.game) == self.source_hashes)
        backup = self.ok('backup')
        label = backup.get('label', '')
        self.check('backup создал receipt', bool(label))
        self.check('backup не меняет игровые файлы', snapshot(self.game) == self.source_hashes)
        receipt_path = self.data / 'backup' / label / 'receipt.json'
        receipt = json.loads(receipt_path.read_text('utf-8-sig'))
        save(self.report / 'prepared-receipt.json', receipt)
        self.check('Новый payload использует штатную установку, а не перенос старой версии',
                   receipt.get('game_version') == self.game_version and not receipt.get('transplant'), receipt.get('game_version'))
        self.ok('unpack', label=label)
        self.ok('verify', label=label)
        self.verify_expected()
        after = snapshot(self.game)
        self.check('Prime файлы сохранились побайтно', {key: value for key, value in after.items()
                                                      if key.startswith('WXQ_Data/Package/builtin/')} == self.prime_before)
        changed = {key for key in set(self.source_hashes) | set(after) if self.source_hashes.get(key) != after.get(key)}
        self.check('Индекс/info/SDK/EXE/логи и соседние пакеты сохранены',
                   all(re.fullmatch(r'QtsDataFolder/QtsVFSCache/packages/(1|4|10|15|19)/(?:\1|\1_[0-9]+)\.db', key)
                       for key in changed), sorted(changed))
        state = json.loads((self.data / 'state.json').read_text('utf-8-sig'))
        save(self.report / 'installed-state.json', state)
        self.check('State содержит новый выпуск и реальный патч', state.get('installed') is True and
                   state.get('version') == self.version and state.get('game_version') == self.game_version, state)
        status = self.ok('status')
        self.check('Status видит установленный актуальный перевод',
                   status.get('installed') == 'yes' and status.get('needs_reapply') == 'no' and
                   status.get('version') == self.version and status.get('game_version') == self.game_version, status)
        self.ok('check')
        again = self.ok('backup')
        self.check('Повторный backup идемпотентен', again.get('state') == 'same' and not again.get('label'), again)
        self.check('Повторное применение не пишет в игру', snapshot(self.game) == after)
        restored = self.ok('restore')
        self.check('Restore применил одну резервную копию', restored.get('restored') == '1', restored)
        self.check('Restore вернул точную текущую официальную игру', snapshot(self.game) == self.source_hashes)
        self.ok('check')
        status = self.ok('status')
        self.check('После отката статус не установлен и без pending',
                   status.get('installed') == 'no' and status.get('pending') == 'no', status)
        second = self.ok('restore')
        self.check('Повторный restore идемпотентен', second.get('restored') == '0', second)
        self.check('Повторный restore не изменил игру', snapshot(self.game) == self.source_hashes)
        self.check('Исходный frozen snapshot сохранился', snapshot(self.source) == self.source_hashes)

    def finish(self, error: str = '') -> None:
        if (self.data / 'install.log').exists():
            shutil.copy2(self.data / 'install.log', self.report / 'install.log')
        save(self.report / 'result.json', {'work': str(self.work), 'source': str(self.source),
             'staging': str(self.staging), 'version': self.version, 'game_version': self.game_version,
             'isolation': self.isolation, 'checks': self.checks, 'commands': self.commands,
             'baseline': self.baseline, 'independent_counts': self.counts,
             'passed': not error and all(item['passed'] for item in self.checks), 'error': error})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--staging', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--version', default='0.6.8')
    parser.add_argument('--game-version', default='1.7.0.1')
    parser.add_argument('--resources', type=int, default=4199)
    args = parser.parse_args()
    probe = Probe(args)
    error = ''
    try:
        probe.execute()
    except Exception:
        error = traceback.format_exc()
        print(error, flush=True)
    finally:
        probe.finish(error)
    return 1 if error else 0


if __name__ == '__main__':
    raise SystemExit(main())
