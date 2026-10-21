"""Перенос старого текста в synthetic будущий патч; настоящая игра не запускается.

    python -X utf8 -B installer/tests/test_future_patch_reuse.py --packer-only
    python -X utf8 -B installer/tests/test_future_patch_reuse.py
    python -X utf8 -B installer/tests/test_future_patch_reuse.py --case storage-gap

Штатный SDK только читается и копируется в tempfile. Все пакеты, исполняемые
стенды, реестр данных русификатора и результаты принадлежат песочнице.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / 'installer'))
sys.path.insert(0, str(PROJECT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_installer as bi  # noqa: E402
from game_inspect import QtsContainer  # noqa: E402
from scripts.game_lang_codec import LangDocument, validate_rewrite, varint  # noqa: E402
from localization.global_map_codec import GlobalMapDocument, prove_text_only  # noqa: E402
from test_cache_reinstall import header_hash, header_of, qts_container, replace_header, snapshot  # noqa: E402

SDK = Path(os.environ.get('WXQ_TEST_SDK', r'C:\Program Files\WXQLauncher\WXQ(1)\WXQ_Data\Plugins\x86_64\QtsVFS.dll'))
HEADER_ID = 0xe12bade797f29f55
NATIVE_HARNESS = r'''
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using Egoist.WXQRussian;
class FutureHarness {
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr LoadLibraryEx(string path,IntPtr reserved,uint flags);
    [DllImport("kernel32.dll")] static extern bool FreeLibrary(IntPtr module);
    static int Main(string[] args) {
        var json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
        IntPtr database = IntPtr.Zero;
        try {
            var input = (Dictionary<string, object>)json.DeserializeObject(File.ReadAllText(args[0], Encoding.UTF8));
            var payloads = new Dictionary<ulong, byte[]>();
            foreach (var item in (Dictionary<string, object>)input["payloads"])
                payloads.Add(Convert.ToUInt64(item.Key, 16), Convert.FromBase64String((string)item.Value));
            Console.Error.WriteLine("database-load-enter");
            database=LoadLibraryEx(Path.Combine(Path.GetDirectoryName((string)input["dll"]),"QtskDB.dll"),IntPtr.Zero,0x1100);
            if(database==IntPtr.Zero)throw new IOException("Database dependency load failed "+Marshal.GetLastWin32Error());
            Console.Error.WriteLine("database-load-done");
            var result = CompatPacker.Pack((string)input["dll"], (string)input["directory"], "1",
                (string)input["version"], Convert.ToInt32(input["revision"]), payloads);
            Console.WriteLine(json.Serialize(new { ok = true, resources = result.ToDictionary(
                p => p.Key.ToString("x16"), p => new { meta_bytes = p.Value.Meta.Length,
                    chunks = p.Value.ChunkData.Count, sha256 = p.Value.Sha,
                    payload_b64 = Convert.ToBase64String(p.Value.Payload) }) }));
        } catch (Exception error) {
            Console.WriteLine(json.Serialize(new { ok = false, type = error.GetType().Name, error = error.Message }));
        } finally {
            if(database!=IntPtr.Zero)FreeLibrary(database);
        }
        return 0;
    }
}
'''


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def compile_native_harness(work: Path) -> Path:
    source, executable = work / 'FutureHarness.cs', work / 'FutureHarness.exe'
    source.write_text(NATIVE_HARNESS, 'utf-8')
    # Packer проверяется отдельно от ещё меняющегося CLI. Reader не подменяется:
    # берём действующие Catalog/Lz4/OverridePackage из production побайтно.
    tool = (PROJECT / 'installer/WXQTool.cs').read_text('utf-8-sig')
    section = tool[tool.index('    class Catalog : IDisposable\n'):tool.index('    // QtskDB.dll игры')]
    reader = work / 'ProductionReader.cs'
    reader.write_text('''using System; using System.Collections.Generic; using System.IO;
using System.Linq; using System.Security.Cryptography; using System.Text;
namespace Egoist.WXQRussian {
class StopException : Exception { public StopException(string code,string message):base(message){} }
static class Util {
    public static string Sha(string path) { using(var f=File.OpenRead(path))using(var h=SHA256.Create())return Hex(h.ComputeHash(f)); }
    public static void FlushToDisk(string path) { using(var f = new FileStream(path,FileMode.Open,FileAccess.Write,FileShare.ReadWrite))f.Flush(true); }
    public static string Hex(byte[] bytes) { return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant(); }
}
''' + section + '\n}', 'utf-8')
    # Только диагностическая копия: те же аргументы native calls плюс фиксированные
    # маркеры фаз. Production-файл не меняется и вызовы SDK не подменяются.
    pack = (PROJECT / 'installer/WXQCompatPack.cs').read_text('utf-8-sig')
    pack = pack.replace('        static uint VersionNumber(', '''        static bool Trace(string name, Func<bool> call) { Console.Error.WriteLine(name+"-enter"); bool result=call(); Console.Error.WriteLine(name+"-done:"+result); return result; }
        static int TraceInt(string name, Func<int> call) { Console.Error.WriteLine(name+"-enter"); int result=call(); Console.Error.WriteLine(name+"-done:"+result); return result; }
        static uint VersionNumber(''', 1)
    calls = {
        'initialize(SilentLog, SilentPrint, IntPtr.Zero)': 'Trace("initialize", delegate { return initialize(SilentLog, SilentPrint, IntPtr.Zero); })',
        'create(Z(config), Z(packed))': 'TraceInt("create", delegate { return create(Z(config), Z(packed)); })',
        'createCache(Z(Path.Combine(directory, "packer-cache")))': 'TraceInt("cache", delegate { return createCache(Z(Path.Combine(directory, "packer-cache"))); })',
        'bindCache(packer, cache)': 'Trace("bind", delegate { return bindCache(packer, cache); })',
        'compress(packer, 1)': 'Trace("compress", delegate { return compress(packer, 1); })',
        'add(packer, Z(package), true, Z(raw), Z(files))': 'Trace("add", delegate { return add(packer, Z(package), true, Z(raw), Z(files)); })',
        'save(packer)': 'Trace("save", delegate { return save(packer); })',
    }
    for old, new in calls.items():
        assert old in pack, old
        pack = pack.replace(old, new, 1)
    traced = work / 'TracePacker.cs'
    traced.write_text(pack, 'utf-8')
    process = subprocess.run([str(bi.CSC), '/nologo', '/utf8output', '/warnaserror+', '/optimize+',
                              '/platform:x64', '/target:exe', '/codepage:65001', '/r:System.Web.Extensions.dll',
                              '/main:FutureHarness', '/out:' + str(executable),
                              str(reader), str(traced), str(source)],
                             capture_output=True, text=True, encoding='utf-8')
    if process.returncode:
        raise RuntimeError(process.stdout + process.stderr)
    return executable


def packer_probe(work: Path) -> dict:
    executable = compile_native_harness(work)
    copy = work / 'sdk/QtsVFS.dll'
    copy.parent.mkdir()
    shutil.copyfile(SDK, copy)
    shutil.copyfile(SDK.with_name('QtskDB.dll'), copy.with_name('QtskDB.dll'))
    assert digest(copy.read_bytes()) == digest(SDK.read_bytes())
    payloads = {'12345678abcdef01': b'new future text payload\x00\xff' * 300,
                '12345678abcdef02': 'Новая строка'.encode('utf-8')}
    request = {'dll': str(copy), 'directory': str(work / 'native-pack'), 'version': '1.7.0.1', 'revision': 7,
               'payloads': {key: base64.b64encode(value).decode('ascii') for key, value in payloads.items()}}
    (work / 'probe.json').write_text(json.dumps(request), 'utf-8')
    try:
        process = subprocess.run([str(executable), str(work / 'probe.json')], capture_output=True, timeout=60, cwd=work)
    except subprocess.TimeoutExpired as error:
        result = {'ok': False, 'type': 'TimeoutExpired', 'error': 'native pack exceeded 60 seconds',
                  'phases': (error.stderr or b'').decode('utf-8', errors='replace').splitlines()}
        (work / 'packer-probe.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return result
    (work / 'native-stdout.bin').write_bytes(process.stdout)
    (work / 'native-stderr.bin').write_bytes(process.stderr)
    lines = process.stdout.decode('utf-8', errors='replace').splitlines()
    result = json.loads(next(line for line in reversed(lines) if line.startswith('{'))) if lines else {}
    result['exit'] = process.returncode
    result['phases'] = process.stderr.decode('utf-8', errors='replace').splitlines()
    metadata = work / 'native-pack/packed/1/1.db'
    storage = work / 'native-pack/packed/1/1_0.db'
    if metadata.is_file():
        with QtsContainer(metadata, raw_values=True) as table:
            result['python_metadata_value_lengths'] = {
                f'{key:016x}': [len(raw) for raw in table.raw_record_values(key)] for key in table.entries}
            result['python_metadata_invalid'] = table.invalid_record_count
    if storage.is_file():
        with QtsContainer(storage) as table:
            result['python_payload_match'] = {key: table.decode_entry(int(key, 16)) == content for key, content in payloads.items()}
            result['python_storage_invalid'] = table.invalid_record_count
    (work / 'packer-probe.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    summary = {key: value for key, value in result.items() if key != 'resources'}
    summary['resources'] = {key: {k: v for k, v in row.items() if k != 'payload_b64'}
                            for key, row in result.get('resources', {}).items()}
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return result


LANG, GLOBAL, RAW, SCHEMA, NEW = (f'12345678abcdef0{i}' for i in range(1, 6))
LANG_KEYS = [f'#{i:016x}#' for i in range(1, 6)]
GLOBAL_KEYS = ['Keep', 'Changed', 'Removed', 'Fresh', 'Next']
SOURCE = ['保留文本', '先前文本', '删除文本', '新鲜文本', '追加文本']
TARGET = ['Сохранённый перевод', 'Старый перевод', 'Удалённый перевод']
OLD_INDEX = b'synthetic original GlobalIndexPrime.data 1.6.0.1'
failures: list[str] = []
checks = 0


def check(name: str, condition: bool, detail: object = '') -> None:
    global checks
    checks += 1
    print(('  ok   ' if condition else '  FAIL ') + name +
          (f' [{detail}]' if not condition and detail else ''), flush=True)
    if not condition:
        failures.append(name)


def write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def frame(number: int, content: bytes, *, noncanonical: bool = False) -> bytes:
    length = varint(len(content))
    if noncanonical:
        length = length[:-1] + bytes([length[-1] | 128, 0])
    return varint(number * 8 + 2) + length + content


def map_bytes(keys: list[str], rows: list[tuple[int, str]], generation: int) -> bytes:
    # Неизвестные числовые поля и непрозрачные blob не имеют отношения к тексту.
    parts = [varint(7 * 8) + varint(generation), b'\x41' + bytes([generation]) * 8]
    for position, (identity, text) in enumerate(rows):
        key = frame(1, keys[identity].encode('utf-8'))
        value = frame(2, text.encode('utf-8'), noncanonical=position == 0)
        unknown = b'\x1d' + bytes([generation, position, 255, 0]) + b'\x20' + varint(900 + generation)
        opaque = frame(5, b'\xff\x00\x08\x01opaque-number-like')
        inner = unknown + value + opaque + key if position % 2 else key + unknown + value + opaque
        parts.append(frame(1, inner, noncanonical=position == 1))
    parts.append(frame(9, b'\xff\x00\x0a\x04\x08\x01\x10\x01'))
    return b''.join(parts)


def text_rewrite(identifier: str, original: bytes, selected: dict[str, str]) -> bytes:
    if identifier == GLOBAL:
        rewritten = GlobalMapDocument.parse(original).replace_text(selected)
        prove_text_only(original, rewritten, selected)
        return rewritten
    document = LangDocument.parse(original)
    rewritten = document.replace(selected)
    validate_rewrite(document, LangDocument.parse(rewritten), selected)
    return rewritten


class Fixture:
    def __init__(self, work: Path):
        self.work = work
        self.executable = work / 'FutureHarness.exe'
        self.sdk = work / 'sdk/QtsVFS.dll'
        self.original = {
            LANG: map_bytes(LANG_KEYS, list(enumerate(SOURCE[:3])), 1),
            GLOBAL: map_bytes(GLOBAL_KEYS, list(enumerate(SOURCE[:3])), 1),
            RAW: b'return { label = "original unchanged Lua-like resource" }\n',
            SCHEMA: map_bytes(LANG_KEYS, [(0, SOURCE[0])], 1),
        }
        self.targets = {
            LANG: dict(zip(LANG_KEYS[:3], TARGET)),
            GLOBAL: dict(zip(GLOBAL_KEYS[:3], TARGET)),
            SCHEMA: {LANG_KEYS[0]: TARGET[0]},
        }
        self.translated = {identifier: text_rewrite(identifier, content, self.targets[identifier])
                           for identifier, content in self.original.items() if identifier != RAW}
        self.translated[RAW] = b'return { label = "translated unchanged Lua-like resource" }\n'
        self.old = self.pack('original', self.original, '1.6.0.1', 1)
        self.overlay = self.pack('translated', self.translated, '1.6.0.1', 1)
        self.variants: dict[str, dict] = {}
        for name, version, revision, generation, rows in (
            ('future', '1.7.0.1', 7, 2, [(1, '更新文本'), (3, SOURCE[3]), (0, SOURCE[0])]),
            ('next', '1.7.1.1', 8, 3, [(4, SOURCE[4]), (0, SOURCE[0]), (1, '再次更新文本'), (3, SOURCE[3])]),
            ('hotfix', '1.6.0.1', 2, 4, [(3, SOURCE[3]), (1, '修订文本'), (0, SOURCE[0])]),
        ):
            resources = {
                LANG: map_bytes(LANG_KEYS, rows, generation),
                GLOBAL: map_bytes(GLOBAL_KEYS, rows, generation), RAW: self.original[RAW],
                # Новая неизвестная схема: прежний text-ресурс больше не является map.
                SCHEMA: b'\x08\x01\x3d\x01\x02\x03\x04' + frame(8, b'new official schema'),
                NEW: b'new official resource absent from old translation ' + name.encode('ascii'),
            }
            packed = self.pack(name, resources, version, revision)
            self.variants[name] = dict(packed, resources=resources, version=version, revision=revision,
                                       index=('synthetic updated index ' + name).encode('ascii'))
        self.payload, self.install = self.make_payload()
        tool_dir = work / 'tool'
        tool_dir.mkdir()
        bi.build_tool(tool_dir, test_build=True)
        self.tool = tool_dir / 'WXQTool.exe'
        sources = {name: digest((PROJECT / 'installer' / name).read_bytes())
                   for name in ('WXQTool.cs', 'WXQCompatText.cs', 'WXQCompatPack.cs')}
        (work / 'compiled-sources.json').write_bytes(json_bytes(dict(sources, tool_sha256=digest(self.tool.read_bytes()))))

    def pack(self, name: str, resources: dict[str, bytes], version: str, revision: int) -> dict:
        directory = self.work / ('pack-' + name)
        request_path = self.work / ('request-' + name + '.json')
        request = {'dll': str(self.sdk), 'directory': str(directory), 'version': version, 'revision': revision,
                   'payloads': {key: base64.b64encode(value).decode('ascii') for key, value in resources.items()}}
        request_path.write_bytes(json_bytes(request))
        process = subprocess.run([str(self.executable), str(request_path)], capture_output=True,
                                 timeout=60, cwd=self.work)
        (self.work / ('native-' + name + '.log')).write_bytes(process.stderr)
        result = json.loads(next(line for line in reversed(process.stdout.decode('utf-8', errors='replace').splitlines())
                                 if line.startswith('{')))
        if process.returncode or not result.get('ok'):
            raise RuntimeError({'pack': name, 'exit': process.returncode, 'result': result})
        folder = directory / 'packed/1'
        with QtsContainer(folder / '1_0.db') as storage:
            assert storage.invalid_record_count == 0
            assert set(storage.entries) == {int(identifier, 16) for identifier in resources}
            assert all(storage.decode_entry(int(identifier, 16)) == value for identifier, value in resources.items())
        header = bytearray(header_of(folder / '1.db'))
        header[88:96] = header[80:88]
        return {'folder': folder, 'header': bytes(header), 'resources_meta': result['resources']}

    def make_payload(self) -> tuple[Path, dict]:
        payload = self.work / 'payload'
        payload.mkdir()
        for name in ('1.db', '1_0.db'):
            shutil.copyfile(self.overlay['folder'] / name, payload / ('p1_' + name))
        replace_header(payload / 'p1_1.db', self.old['header'])
        old_info = json_bytes({'version': '1.6.0.1', 'revision': '1'})
        (payload / 'p1_1.info').write_bytes(old_info)
        files = [{'name': name, 'bytes': (payload / ('p1_' + name)).stat().st_size,
                  'sha256': digest((payload / ('p1_' + name)).read_bytes()), 'payload': 'p1_' + name}
                 for name in ('1.db', '1_0.db', '1.info')]
        resources = []
        for identifier, translated in self.translated.items():
            codec = 'global-map-v1' if identifier == GLOBAL else 'lang-map-v1'
            row = {'id_hex': identifier, 'kind': 'global-localize' if identifier == GLOBAL else 'lua-script' if identifier == RAW else 'lang',
                   'original_sha256': digest(self.original[identifier]), 'sha256': digest(translated),
                   'bytes': len(translated), 'chunks': self.overlay['resources_meta'][identifier]['chunks']}
            if identifier != RAW:
                document = GlobalMapDocument.parse(self.original[identifier]) if identifier == GLOBAL else LangDocument.parse(self.original[identifier])
                entries = [{'key': item.key, 'source_sha256': digest(item.text.encode('utf-8')),
                            'target': self.targets[identifier][item.key]} for item in document.rows]
                content = json_bytes({'schema': 1, 'codec': codec, 'rows': entries})
                filename = 'p1_text_' + identifier + '.json'
                (payload / filename).write_bytes(content)
                row['text_reuse'] = {'schema': 1, 'codec': codec, 'payload': filename, 'sha256': digest(content),
                                     'bytes': len(content), 'rows': len(entries)}
            resources.append(row)
        install = {'product': 'Future patch reuse synthetic regression', 'version': 'test-future',
                   'built': '2026-10-21T00:00:00Z', 'game_version': '1.6.0.1', 'index_sha256': digest(OLD_INDEX),
                   'sdk_hashes': {name: digest((self.sdk.parent / name).read_bytes()) for name in ('QtskDB.dll', 'QtsVFS.dll')},
                   'sdk_known': [digest(self.sdk.with_name('QtskDB.dll').read_bytes())], 'candidates': ['synthetic-native-future'],
                   'packages': [{'id': '1', 'prime_pkg_hash': header_hash(self.old['header']), 'header_hex': self.old['header'].hex(),
                                 'info_sha256': digest(old_info), 'info_version': '1.6.0.1', 'files': files, 'resources': resources}],
                   'prime': [{'relative': '1/' + name, 'bytes': (self.old['folder'] / name).stat().st_size,
                              'sha256': digest((self.old['folder'] / name).read_bytes())} for name in ('1.db', '1_0.db')], 'known': {}}
        (payload / 'install.json').write_bytes(json_bytes(install))
        return payload, install


class Sandbox:
    def __init__(self, fixture: Fixture, name: str, variant: str = 'future', payload: Path | None = None):
        self.fixture, self.name = fixture, name
        self.root = fixture.work / name
        self.root.mkdir()
        self.game = self.root / 'Лаунчер с пробелом/Игра WXQ(1)'
        self.cache = self.game / 'QtsDataFolder/QtsVFSCache/packages'
        self.prime = self.game / 'WXQ_Data/Package/builtin'
        self.data = self.root / 'data'
        self.payload = payload or fixture.payload
        write(self.game / 'WXQ.exe', b'MZ synthetic game marker; never executed')
        for name in ('QtskDB.dll', 'QtsVFS.dll'):
            write(self.game / 'WXQ_Data/Plugins/x86_64' / name, (fixture.sdk.parent / name).read_bytes())
        write(self.cache / '1/readme.txt', b'keep unrelated official notes')
        self.apply_official(variant)
        self.command_number = 0

    def apply_official(self, variant: str) -> None:
        self.current = self.fixture.variants[variant]
        for name in ('1.db', '1_0.db'):
            write(self.prime / '1' / name, (self.current['folder'] / name).read_bytes())
        folder = self.cache / '1'
        if folder.exists():
            for path in folder.glob('1_*.db'):
                path.unlink()
        write(folder / '1.db', qts_container([(HEADER_ID, self.current['header'])]))
        write(folder / '1.info', json_bytes({'version': self.current['version'], 'revision': str(self.current['revision'])}))
        write(self.cache / 'builtin/Globalindex/GlobalIndexPrime.data', self.current['index'])
        current_hash = header_hash(self.current['header'])
        write(self.cache.parent / 'QtsVFS.log',
              f'MountPackage 1, prime, 7 => succeed! test pkgHash:{current_hash} basePkgHash:{current_hash}\n'.encode('ascii'))
        os.utime(self.cache.parent / 'QtsVFS.log', (time.time() + 2, time.time() + 2))

    def run(self, command: str, **extra: str) -> dict[str, str]:
        self.command_number += 1
        prefix = f'{self.command_number:02d}-{command}'
        output = self.root / (prefix + '.txt')
        arguments = [str(self.fixture.tool), command, '--payload', str(self.payload), '--data', str(self.data),
                     '--out', str(output), '--game', str(self.game)]
        for key, value in extra.items():
            arguments.extend(['--' + key, value])
        env = dict(os.environ, WXQ_RU_USERS_ROOT=str(self.root / 'no-users'), WXQ_RU_TEST_CLOSE_ROOT=str(self.root),
                   WXQ_RU_TEST_NOKILL='1', APPDATA=str(self.root / 'profile'))
        try:
            done = subprocess.run(arguments, capture_output=True, env=env, timeout=300, cwd=self.root)
        except subprocess.TimeoutExpired as error:
            (self.root / (prefix + '-stdout.bin')).write_bytes(error.stdout or b'')
            (self.root / (prefix + '-stderr.bin')).write_bytes(error.stderr or b'')
            log = self.data / 'install.log'
            if log.is_file():
                shutil.copyfile(log, self.root / (prefix + '-timeout.log'))
            receipts = {path.parent.name: json.loads(path.read_text('utf-8-sig'))
                        for path in self.data.glob('backup/*/receipt.json')}
            (self.root / (prefix + '-timeout-state.json')).write_bytes(json_bytes({
                'command': command, 'timeout': error.timeout, 'receipts': receipts, 'game': snapshot(self.game)}))
            raise
        (self.root / (prefix + '-stdout.bin')).write_bytes(done.stdout)
        (self.root / (prefix + '-stderr.bin')).write_bytes(done.stderr)
        rows = {'_exit': str(done.returncode)}
        if output.is_file():
            rows.update(line.partition('=')[::2] for line in output.read_text('utf-8-sig').splitlines() if '=' in line)
        return rows

    def ok(self, command: str, **extra: str) -> dict[str, str]:
        result = self.run(command, **extra)
        passed = result.get('status') == 'ok' and result.get('_exit') == '0'
        check(self.name + ': ' + command, passed, result)
        if not passed:
            raise RuntimeError(command + ' failed: ' + repr(result))
        return result

    def receipt(self, label: str) -> dict:
        return json.loads((self.data / 'backup' / label / 'receipt.json').read_text('utf-8-sig'))

    def status(self, installed: str, reapply: str) -> None:
        result = self.ok('status')
        check(self.name + ': status installed=' + installed + ', needs_reapply=' + reapply,
              result.get('installed') == installed and result.get('needs_reapply') == reapply, result)

    def install(self, expected_ids: set[str] | None = None, *, thorough: bool = True) -> str:
        expected_ids = {LANG, GLOBAL, RAW} if expected_ids is None else expected_ids
        before, prime = snapshot(self.game), snapshot(self.prime)
        if thorough:
            self.ok('check')
            check(self.name + ': check не пишет в игру', snapshot(self.game) == before)
        result = self.ok('backup')
        label = result.get('label', '')
        if not label:
            raise RuntimeError('backup did not create receipt')
        check(self.name + ': backup не пишет в игру', snapshot(self.game) == before)
        receipt = self.receipt(label)
        check(self.name + ': receipt знает текущую и исходную версии',
              receipt.get('game_version') == self.current['version'] and receipt.get('build_game_version') == '1.6.0.1')
        check(self.name + ': receipt содержит только подошедшие ресурсы', set(receipt.get('applied_resources', {}).get('1', [])) == expected_ids)
        if LANG in expected_ids:
            for identifier in (LANG, GLOBAL):
                row = receipt.get('text_reuse', {}).get('1', {}).get(identifier, {})
                check(self.name + ': счётчики строк ' + identifier,
                      row.get('applied_rows') == 1 and row.get('changed_source_rows') == 1 and row.get('missing_rows') == 1
                      and row.get('preserved_rows') == len(
                          LangDocument.parse(self.current['resources'][identifier]).rows if identifier == LANG
                          else GlobalMapDocument.parse(self.current['resources'][identifier]).rows) - 1, row)
        check(self.name + ': новая схема пропущена отдельно',
              any(row.startswith(SCHEMA + ':') for row in receipt.get('skipped_resources', {}).get('1', [])))
        self.ok('unpack', label=label)
        self.ok('verify', label=label)
        check(self.name + ': prime побайтно сохранён', snapshot(self.prime) == prime)
        with QtsContainer(self.cache / '1/1_0.db') as storage:
            check(self.name + ': storage содержит только совместимые ресурсы',
                  set(storage.entries) == {int(identifier, 16) for identifier in expected_ids})
            for identifier in expected_ids:
                content = storage.decode_entry(int(identifier, 16))
                if identifier == RAW:
                    expected = self.fixture.translated[RAW]
                else:
                    key = LANG_KEYS[0] if identifier == LANG else GLOBAL_KEYS[0]
                    expected = text_rewrite(identifier, self.current['resources'][identifier], {key: TARGET[0]})
                check(self.name + ': новые/изменённые строки и неизвестные bytes сохранены ' + identifier, content == expected)
        check(self.name + ': header привязан к новому prime', header_of(self.cache / '1/1.db') == self.current['header'])
        state = json.loads((self.data / 'state.json').read_text('utf-8-sig'))
        check(self.name + ': state знает фактическую игру и сборку перевода',
              state.get('game_version') == self.current['version'] and state.get('build_game_version') == '1.6.0.1')
        self.status('yes', 'no')
        if thorough:
            installed, count = snapshot(self.game), len(list(self.data.glob('backup/*/receipt.json')))
            again = self.ok('backup')
            check(self.name + ': повторная установка идемпотентна', again.get('state') == 'same' and not again.get('label')
                  and snapshot(self.game) == installed and len(list(self.data.glob('backup/*/receipt.json'))) == count, again)
        return label


def install_restore(fixture: Fixture, name: str, variant: str = 'future') -> None:
    box = Sandbox(fixture, name, variant)
    before = snapshot(box.game)
    box.install()
    box.ok('restore')
    check(name + ': restore возвращает именно текущую официальную игру', snapshot(box.game) == before)


def official_update(fixture: Fixture, reapply: bool) -> None:
    box = Sandbox(fixture, 'official-reapply' if reapply else 'official-restore')
    box.install(thorough=False)
    box.apply_official('next')
    official = snapshot(box.game)
    box.status('no', 'yes')
    if reapply:
        box.install(thorough=False)
    box.ok('restore')
    check(box.name + ': старые receipt не возвращают предыдущие официальные файлы', snapshot(box.game) == official)


def invalid_sidecar(fixture: Fixture, mode: str) -> None:
    payload = fixture.work / ('payload-' + mode)
    shutil.copytree(fixture.payload, payload)
    install = json.loads((payload / 'install.json').read_text('utf-8'))
    reference = next(row for row in install['packages'][0]['resources'] if row['id_hex'] == LANG)['text_reuse']
    path = payload / reference['payload']
    if mode == 'missing':
        path.unlink()
    elif mode == 'tampered':
        content = bytearray(path.read_bytes())
        content[-2] ^= 1
        path.write_bytes(content)
    elif mode == 'wrongcount':
        reference['rows'] += 1
        (payload / 'install.json').write_bytes(json_bytes(install))
    box = Sandbox(fixture, 'bad-' + mode, payload=payload)
    before = snapshot(box.game)
    first = box.run('check')
    if first.get('status') == 'ok':
        first = box.run('backup')
    check(box.name + ': повреждённый sidecar останавливает payload до записи',
          first.get('status') == 'stop' and first.get('code') == 'payload' and first.get('_exit') == '2', first)
    check(box.name + ': игра побайтно не изменена', snapshot(box.game) == before)
    check(box.name + ': prepared receipt не оставлен', not list(box.data.glob('backup/*/receipt.json')))


def changed_after_backup(fixture: Fixture) -> None:
    box = Sandbox(fixture, 'changed-after-backup')
    box.ok('check')
    result = box.ok('backup')
    label = result['label']
    # Официальное обновление произошло после подготовки и до первой записи.
    box.apply_official('next')
    official = snapshot(box.game)
    result = box.run('unpack', label=label)
    check(box.name + ': изменение prime останавливает старую подготовку',
          result.get('status') == 'stop' and result.get('code') == 'changed' and result.get('_exit') == '2', result)
    check(box.name + ': новых официальных файлов не коснулись', snapshot(box.game) == official)


def storage_gap(fixture: Fixture) -> None:
    box = Sandbox(fixture, 'storage-gap')
    # Проверяем чтение валидных чанков SDK из _2 при отсутствии _1. Полный
    # GlobalIndex клиента не эмулируется: тест обращается к PrimeStoragePaths.
    write(box.prime / '1/1_2.db', (box.prime / '1/1_0.db').read_bytes())
    write(box.prime / '1/1_0.db', qts_container([(0xabcdef1122334455, b'new-only')], chunks=True))
    log = box.cache.parent / 'QtsVFS.log'
    os.utime(log, (time.time() + 2, time.time() + 2))
    before = snapshot(box.game)
    check(box.name + ': storage _1 отсутствует, _2 существует', not (box.prime / '1/1_1.db').exists() and (box.prime / '1/1_2.db').is_file())
    box.install(thorough=False)
    box.ok('restore')
    check(box.name + ': storage с разрывом и вся игра сохранены', snapshot(box.game) == before)


def added_storage_after_backup(fixture: Fixture) -> None:
    box = Sandbox(fixture, 'added-storage-after-backup')
    result = box.ok('backup')
    # Изменение только набора файлов, прежний _0 и его mtime остаются прежними.
    write(box.prime / '1/1_2.db', qts_container([(0xabcdef1122334455, b'new-only')], chunks=True))
    # Журнал намеренно актуален: проверяем именно сверку набора storage
    # с receipt, а не более ранний отказ из-за устаревшего mount-журнала.
    log = box.cache.parent / 'QtsVFS.log'
    os.utime(log, (time.time() + 2, time.time() + 2))
    before = snapshot(box.game)
    result = box.run('unpack', label=result['label'])
    check(box.name + ': добавленный storage останавливает подготовку',
          result.get('status') == 'stop' and result.get('code') == 'changed' and result.get('_exit') == '2', result)
    check(box.name + ': игра не изменена', snapshot(box.game) == before)


def status_added_storage(fixture: Fixture) -> None:
    box = Sandbox(fixture, 'status-added-storage')
    box.install(thorough=False)
    write(box.prime / '1/1_2.db', qts_container([(0xabcdef1122334455, b'new-only')], chunks=True))
    before = snapshot(box.game)
    box.status('no', 'yes')
    check(box.name + ': status не пишет в игру', snapshot(box.game) == before)
    # Обновлённый официальный prime остаётся на месте и при удалении перевода.
    prime = snapshot(box.prime)
    box.ok('restore')
    check(box.name + ': restore сохраняет новый storage', snapshot(box.prime) == prime)


def legacy_payload(fixture: Fixture) -> None:
    payload = fixture.work / 'payload-legacy'
    shutil.copytree(fixture.payload, payload)
    install = json.loads((payload / 'install.json').read_text('utf-8'))
    for row in install['packages'][0]['resources']:
        row.pop('text_reuse', None)
    (payload / 'install.json').write_bytes(json_bytes(install))
    box = Sandbox(fixture, 'legacy', payload=payload)
    before = snapshot(box.game)
    box.install({RAW})
    box.ok('restore')
    check(box.name + ': старый payload сохраняет whole-resource-SHA поведение', snapshot(box.game) == before)


def resume_existing(root: Path, tool: Path) -> int:
    # Этот режим предназначен только для уже созданной этим тестом песочницы.
    root, tool = root.resolve(), tool.resolve()
    temporary = Path(tempfile.gettempdir()).resolve()
    if temporary not in root.parents or temporary not in tool.parents or not root.parent.name.startswith('wxq-future-patch-'):
        raise ValueError('Resume разрешён только внутри tempfile wxq-future-patch-*')
    box = object.__new__(Sandbox)
    box.root, box.name = root, 'resume-existing'
    box.game = root / 'Лаунчер с пробелом/Игра WXQ(1)'
    box.cache = box.game / 'QtsDataFolder/QtsVFSCache/packages'
    box.prime = box.game / 'WXQ_Data/Package/builtin'
    box.data, box.payload = root / 'data', root.parent / 'payload'
    box.fixture = SimpleNamespace(tool=tool)
    box.command_number = 100
    receipts = [json.loads(path.read_text('utf-8-sig')) for path in box.data.glob('backup/*/receipt.json')]
    interrupted = [row for row in receipts if row.get('status') == 'restore-in-progress']
    if len(interrupted) != 1:
        raise ValueError('Ожидался ровно один контролируемо прерванный restore')
    latest = interrupted[0]
    before, prime = snapshot(box.game), snapshot(box.prime)
    expected = dict(before)
    for row in latest['operations']:
        relative = 'QtsDataFolder/QtsVFSCache/' + row['relative']
        if row['before_exists']:
            expected[relative] = row['before_sha256']
        else:
            expected.pop(relative, None)
    box.ok('restore')
    check(box.name + ': resume вернул чистый актуальный cache без старых официальных файлов', snapshot(box.game) == expected)
    check(box.name + ': prime неизменен после resume', snapshot(box.prime) == prime)
    statuses = {path.parent.name: json.loads(path.read_text('utf-8-sig')).get('status')
                for path in box.data.glob('backup/*/receipt.json')}
    check(box.name + ': все receipt завершены restored', bool(statuses) and set(statuses.values()) == {'restored'}, statuses)
    check(box.name + ': установленное state снято', not (box.data / 'state.json').exists())
    report = {'checks': checks, 'failures': failures, 'receipt_statuses': statuses, 'root': str(root)}
    (root / 'restore-resume-result.json').write_bytes(json_bytes(report))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--packer-only', action='store_true')
    parser.add_argument('--case', action='append', help='Запустить только названный сценарий; параметр можно повторять.')
    parser.add_argument('--resume-box', type=Path, help='Продолжить контролируемо прерванный restore в нашей tempfile-коробке.')
    parser.add_argument('--tool', type=Path, help='Новый TESTBUILD helper для --resume-box.')
    args = parser.parse_args()
    if args.resume_box:
        if args.tool is None:
            parser.error('--resume-box требует --tool')
        return resume_existing(args.resume_box, args.tool)
    work = Path(tempfile.mkdtemp(prefix='wxq-future-patch-'))
    print('Песочница: ' + str(work), flush=True)
    result = packer_probe(work)
    if not (result.get('ok') and result.get('python_payload_match') and all(result['python_payload_match'].values())):
        return 1
    if args.packer_only:
        return 0
    fixture = Fixture(work)
    cases = [('future', lambda: install_restore(fixture, 'future')),
             ('hotfix', lambda: install_restore(fixture, 'hotfix', 'hotfix')),
             ('official-reapply', lambda: official_update(fixture, True)),
             ('official-restore', lambda: official_update(fixture, False)),
             *[(mode, lambda mode=mode: invalid_sidecar(fixture, mode)) for mode in ('missing', 'tampered', 'wrongcount')],
             ('changed-after-backup', lambda: changed_after_backup(fixture)),
             ('storage-gap', lambda: storage_gap(fixture)),
             ('added-storage-after-backup', lambda: added_storage_after_backup(fixture)),
             ('status-added-storage', lambda: status_added_storage(fixture)),
             ('legacy', lambda: legacy_payload(fixture))]
    if args.case:
        unknown = set(args.case) - {name for name, _ in cases}
        if unknown:
            raise ValueError('Unknown cases: ' + ', '.join(sorted(unknown)))
        cases = [(name, run) for name, run in cases if name in args.case]
    for name, run in cases:
        print('\n' + name, flush=True)
        try:
            run()
        except Exception:
            failures.append(name + ': exception')
            detail = traceback.format_exc()
            (work / (name + '-failure.txt')).write_text(detail, 'utf-8')
            print(detail, flush=True)
    report = {'checks': checks, 'failures': failures, 'sandbox': str(work), 'cases': len(cases)}
    (work / 'regression-result.json').write_bytes(json_bytes(report))
    print('\n' + json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
