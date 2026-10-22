"""Проверка старого payload на неизменённом снимке настоящего нового патча.

Игра не запускается. Источником служит только заранее снятая копия в Temp.
Запускать через isolated_test_run.py; исходники C#/ISS/UI не меняются.
"""
from __future__ import annotations

import argparse
from collections import Counter
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
from scripts.game_lang_codec import LangDocument  # noqa: E402
from localization.global_map_codec import GlobalMapDocument, prove_text_only  # noqa: E402


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


class Probe:
    def __init__(self, args):
        self.work = args.work.resolve()
        self.source = args.snapshot.resolve()
        self.staging = args.staging.resolve()
        self.report = args.report.resolve()
        temporary = Path(tempfile.gettempdir()).resolve()
        assert self.work.is_relative_to(temporary) and self.source.is_relative_to(temporary)
        assert (self.source / 'WXQ_Data/StreamingAssets/Config/Version.txt').is_file()
        self.work.mkdir(parents=True, exist_ok=False)
        self.report.mkdir(parents=True, exist_ok=True)
        self.checks = []
        self.commands = []
        self.game = self.work / 'box/Игра WXQ(1)'
        self.data = self.work / 'box/data'
        self.cache = self.game / 'QtsDataFolder/QtsVFSCache/packages'
        self.payload = self.work / 'payload'
        self.install = json.loads((self.staging / 'install.json').read_text('utf-8-sig'))
        self.expected = {}
        self.reuse_details = {}
        self.baseline = None

    def check(self, name: str, passed: bool, detail: object = '') -> None:
        self.checks.append({'name': name, 'passed': bool(passed), 'detail': detail})
        print(('PASS ' if passed else 'FAIL ') + name, flush=True)
        if not passed:
            raise AssertionError(name + ': ' + str(detail))

    def prepare(self) -> None:
        mapping = json.loads((self.source.parent / 'mapping.json').read_text('utf-8'))
        assert mapping.get('complete') is True
        wrong_time = [row['target_relative'] for row in mapping['files']
                      if (self.source / row['target_relative']).stat().st_mtime_ns != row['source_mtime_utc_after_ns']]
        self.check('Frozen snapshot сохранил исходные mtime, включая порядок журналов', not wrong_time, wrong_time[:20])
        before = snapshot(self.source)
        self.source_hashes = before
        for source in sorted(self.source.rglob('*')):
            if not source.is_file():
                continue
            relative = source.relative_to(self.source)
            target = self.game / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            # Жёсткая ссылка допустима только на нашу замороженную Temp-копию
            # prime. Ни один файл живой игры не связан с песочницей.
            if relative.as_posix().startswith('WXQ_Data/Package/builtin/'):
                os.link(source, target)
            else:
                shutil.copy2(source, target)
        self.check('Копия игры побайтно равна frozen snapshot', snapshot(self.game) == before)
        self.payload.mkdir()
        shutil.copy2(self.staging / 'install.json', self.payload / 'install.json')
        references = {}
        for package in self.install['packages']:
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
                sources[match[2]] = Path(match[1])
        self.check('В исходном Inno payload присутствуют все ссылки install.json', set(sources) == set(references))
        for flat, source in sources.items():
            target = self.payload / flat
            shutil.copy2(source, target)
            row = references[flat]
            assert target.stat().st_size == row['bytes'] and sha(target) == row['sha256'], flat
        self.check('Побайтный payload опубликованной0.6.7 и все sidecar проверены', True, len(references))
        tool_dir = self.work / 'tool'
        tool_dir.mkdir()
        sources = {name: sha(PROJECT / 'installer' / name)
                   for name in ('WXQTool.cs', 'WXQCompatText.cs', 'WXQCompatPack.cs')}
        bi.build_tool(tool_dir, test_build=True)
        self.tool = tool_dir / 'WXQTool.exe'
        self.source_receipt = dict(sources, tool_sha256=sha(self.tool))
        save(self.report / 'compiled-sources.json', self.source_receipt)
        self.cache_before = snapshot(self.cache)
        self.prime_before = {key: value for key, value in before.items()
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

    def verify_expected(self, receipt: dict) -> None:
        applied = {key: set(values) for key, values in receipt['applied_resources'].items()}
        counts = Counter()
        for package in self.install['packages']:
            package_id = package['id']
            originals = {}
            wanted = {int(row['id_hex'], 16): row for row in package['resources']}
            prime = self.game / 'WXQ_Data/Package/builtin' / package_id
            for path in sorted(prime.glob(package_id + '_*.db')):
                with QtsContainer(path) as container:
                    for ident in set(container.entries) & set(wanted):
                        if ident in originals:
                            raise ValueError('Duplicate actual prime resource ' + str(ident))
                        originals[ident] = container.decode_entry(ident)
            old_storage = self.payload / next(row['payload'] for row in package['files'] if row['name'] == package_id + '_0.db')
            expected = {}
            details = {}
            with QtsContainer(old_storage) as payload:
                for ident, row in wanted.items():
                    if ident not in originals:
                        counts['missing_resource'] += 1
                        continue
                    original = originals[ident]
                    if digest(original) == row['original_sha256']:
                        expected[ident] = payload.decode_entry(ident)
                        counts['whole_resource_reused'] += 1
                        continue
                    if 'text_reuse' not in row:
                        counts['changed_resource_skipped'] += 1
                        continue
                    metadata = row['text_reuse']
                    sidecar = json.loads((self.payload / metadata['payload']).read_text('utf-8'))
                    document = (GlobalMapDocument if metadata['codec'] == 'global-map-v1' else LangDocument).parse(original)
                    current = {item.key: item.text for item in document.rows}
                    matches = {item['key']: item['target'] for item in sidecar['rows']
                               if item['key'] in current and digest(current[item['key']].encode('utf-8')) == item['source_sha256']}
                    if not matches:
                        counts['no_matching_rows'] += 1
                        continue
                    # Sidecar взят из опубликованного payload с точным SHA:
                    # смысл перевода здесь не утверждается повторно. Проверяем
                    # только current sourceSHA и побайтный text-only перенос,
                    # включая утверждённые ранее числовые пересчёты/разметку.
                    wire_document = GlobalMapDocument.parse(original)
                    assert [(r.key, r.text) for r in wire_document.rows] == [(r.key, r.text) for r in document.rows]
                    rewritten = wire_document.replace_text(matches)
                    prove_text_only(original, rewritten, matches)
                    expected[ident] = rewritten
                    details[row['id_hex']] = {'applied_rows': len(matches), 'current_rows': len(current),
                                               'preserved_rows': len(current) - len(matches), 'expected_sha256': digest(rewritten)}
                    counts['text_resource_reused'] += 1
                    counts['text_rows_reused'] += len(matches)
                    counts['text_rows_preserved'] += len(current) - len(matches)
            self.check('Пакет' + package_id + ': applied ids совпали с независимым Python',
                       {f'{ident:016x}' for ident in expected} == applied.get(package_id, set()))
            if expected:
                with QtsContainer(self.cache / package_id / (package_id + '_0.db')) as result:
                    self.check('Пакет' + package_id + ': состав storage точен', set(result.entries) == set(expected))
                    self.check('Пакет' + package_id + ': все нагрузки совпали побайтно',
                               all(result.decode_entry(ident) == content for ident, content in expected.items()), len(expected))
            self.reuse_details[package_id] = details
        self.expected = dict(counts)
        save(self.report / 'independent-readback.json', {'counts': self.expected, 'text_resources': self.reuse_details})

    def execute(self) -> None:
        self.prepare()
        self.baseline = self.run('check')
        save(self.report / 'baseline.json', self.baseline)
        self.check('Старый0.6.7 payload проходит check на реальном патче1.7.0.1',
                   self.baseline.get('status') == 'ok' and self.baseline['_exit'] == 0, self.baseline)
        self.check('check не меняет игровые файлы', snapshot(self.game) == self.source_hashes)
        backup = self.ok('backup')
        label = backup.get('label', '')
        self.check('backup создал receipt', bool(label))
        self.check('backup не меняет игровые файлы', snapshot(self.game) == self.source_hashes)
        receipt_path = self.data / 'backup' / label / 'receipt.json'
        receipt = json.loads(receipt_path.read_text('utf-8-sig'))
        save(self.report / 'prepared-receipt.json', receipt)
        self.check('Receipt различает текущую1.7 и исходную1.6',
                   receipt.get('game_version') == '1.7.0.1' and receipt.get('build_game_version') == '1.6.0.1')
        self.ok('unpack', label=label)
        self.ok('verify', label=label)
        self.verify_expected(receipt)
        after = snapshot(self.game)
        self.check('Prime файлы сохранились', {key: value for key, value in after.items()
                                              if key.startswith('WXQ_Data/Package/builtin/')} == self.prime_before)
        changed = set(self.source_hashes) | set(after)
        changed = {key for key in changed if self.source_hashes.get(key) != after.get(key)}
        self.check('Индекс/info/SDK/EXE/логи и соседние пакеты сохранены',
                   all(re.fullmatch(r'QtsDataFolder/QtsVFSCache/packages/(1|4|10|15|19)/(?:\1|\1_[0-9]+)\.db', key)
                       for key in changed), sorted(changed))
        state = json.loads((self.data / 'state.json').read_text('utf-8-sig'))
        save(self.report / 'installed-state.json', state)
        status = self.ok('status')
        self.check('status установлен и не требует повторного применения',
                   status.get('installed') == 'yes' and status.get('needs_reapply') == 'no', status)
        self.ok('check')
        again = self.ok('backup')
        self.check('Повторный backup идемпотентен', again.get('state') == 'same' and not again.get('label'), again)
        self.check('Повторное применение не пишет в игру', snapshot(self.game) == after)
        self.ok('restore')
        self.check('restore вернул именно текущую официальную игру', snapshot(self.game) == self.source_hashes)
        self.check('Frozen snapshot источника сохранился', snapshot(self.source) == self.source_hashes)

    def finish(self, error: str = '') -> None:
        if (self.data / 'install.log').exists():
            shutil.copy2(self.data / 'install.log', self.report / 'install.log')
        save(self.report / 'result.json', {'work': str(self.work), 'source': str(self.source),
             'staging': str(self.staging), 'checks': self.checks, 'commands': self.commands,
             'baseline': self.baseline, 'independent_counts': self.expected,
             'passed': not error and all(item['passed'] for item in self.checks), 'error': error})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--staging', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
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
