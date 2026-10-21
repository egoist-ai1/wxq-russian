"""Память CLI при переносе всех 297 текстовых ресурсов carrier14 и sparse 1 ГиБ.

    python -X utf8 -B installer/tests/test_future_patch_memory.py --prepare-only
    python -X utf8 -B installer/tests/test_future_patch_memory.py --work <temp-work>

Оригиналы и SDK установленной игры только читаются. Все DLL, пакеты, helper,
игровые каталоги и receipt принадлежат tempfile; игра и UI не запускаются.
Замеряется дескриптор только собственного x64 child, без глобального поиска PID.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import traceback

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / 'installer'))
sys.path.insert(0, str(PROJECT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_installer as bi  # noqa: E402
from build_compat_text import make_reuse  # noqa: E402
from game_inspect import QtsContainer  # noqa: E402
import pilot_contract  # noqa: E402
from scripts.game_lang_codec import varint  # noqa: E402
from test_cache_reinstall import header_hash, header_of, qts_container  # noqa: E402

CARRIER = PROJECT / 'build/carrier-2026-10-21-v14-1.6.0.1'
GAME_SOURCES = Path(r'C:\Program Files\WXQLauncher\WXQ(1)')
HEADER_ID = 0xe12bade797f29f55
UNKNOWN = varint(536870900 << 3) + varint(0xd00dfeed)
GIB = 1024 ** 3
MIB = 1024 ** 2
checks: list[dict] = []

FIXTURE_HARNESS = r'''
using System; using System.Collections.Generic; using System.IO; using System.Linq;
using System.Runtime.InteropServices; using System.Text; using System.Web.Script.Serialization;
using Egoist.WXQRussian;
class MemoryFixturePacker {
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr LoadLibraryEx(string path,IntPtr reserved,uint flags);
 [DllImport("kernel32.dll")] static extern bool FreeLibrary(IntPtr module);
 static bool Writable(string path) {
  try { using(var stream=new FileStream(path,FileMode.Open,FileAccess.Write,FileShare.ReadWrite|FileShare.Delete)){} return true; }
  catch(IOException){return false;}
 }
 static object Locked(Dictionary<string,object> input) {
  string path=(string)input["path"],renamed=path+".lock-probe";
  bool controlWrite=Writable(path),controlRename=false,writeBlocked=false,renameBlocked=false,hashMatches=false,decodedMatches=false,released=false;
  File.Move(path,renamed);File.Move(renamed,path);controlRename=true;
  string fileSha=null;
  try {
   using(var catalog=Catalog.OpenChunks(path)) {
    fileSha=Util.Sha(path);
    hashMatches=fileSha==(string)input["file_sha256"];
    using(var hash=System.Security.Cryptography.SHA256.Create())
     decodedMatches=Util.Hex(hash.ComputeHash(catalog.DecodeEntry(Convert.ToUInt64((string)input["id_hex"],16))))==(string)input["entry_sha256"];
    writeBlocked=!Writable(path);
    try {File.Move(path,renamed);File.Move(renamed,path);}catch(IOException){renameBlocked=true;}
   }
  }finally{if(File.Exists(renamed)&&!File.Exists(path))File.Move(renamed,path);}
  if(Writable(path)){File.Move(path,renamed);File.Move(renamed,path);released=true;}
  return new {ok=true,control_write=controlWrite,control_rename=controlRename,write_blocked=writeBlocked,rename_blocked=renameBlocked,
   hash_matches_locked_file=hashMatches,decoded_matches=decodedMatches,released_after_dispose=released,file_sha256=fileSha};
 }
 static object Cleanup(Dictionary<string,object> input) {
  var ctx=new Context {Data=(string)input["data"]};
  try {
   object method; string name=input.TryGetValue("method",out method)?(string)method:"RemoveBuildScratch";
   typeof(Egoist.WXQRussian.Program).GetMethod(name,System.Reflection.BindingFlags.Static|System.Reflection.BindingFlags.NonPublic)
    .Invoke(null,new object[]{ctx,(string)input["directory"]});
   return new {ok=true,rejected=false};
  }catch(System.Reflection.TargetInvocationException error){
   return new {ok=true,rejected=true,error_type=error.InnerException.GetType().Name,error=error.InnerException.Message};
  }
 }
 static object CleanupLocked(Dictionary<string,object> input) {
  var ctx=new Context {Data=(string)input["data"]};string directory=(string)input["directory"];
  bool returned=false,retained=false;
  using(var held=new FileStream(Path.Combine(directory,"locked.tmp"),FileMode.Open,FileAccess.Read,FileShare.Read)) {
   typeof(Egoist.WXQRussian.Program).GetMethod("TryRemoveBuildScratch",System.Reflection.BindingFlags.Static|System.Reflection.BindingFlags.NonPublic)
    .Invoke(null,new object[]{ctx,directory});
   returned=true;retained=Directory.Exists(directory)&&File.Exists(Path.Combine(directory,"locked.tmp"));
  }
  typeof(Egoist.WXQRussian.Program).GetMethod("RemoveBuildScratch",System.Reflection.BindingFlags.Static|System.Reflection.BindingFlags.NonPublic)
   .Invoke(null,new object[]{ctx,directory});
  return new {ok=true,wrapper_returned=returned,retained_while_locked=retained,removed_after_close=!Directory.Exists(directory)};
 }
 static object ZeroApplied(Dictionary<string,object> input) {
  string game=(string)input["game"],payload=(string)input["payload"],data=(string)input["data"];
  var ctx=new Context {Game=game,Payload=payload,Data=data,Cache=Path.Combine(game,"QtsDataFolder","QtsVFSCache","packages"),M=Manifest.Load(payload)};
  var package=ctx.M.Pkgs.First(p=>p.Id=="1"); string catalog=Path.Combine(ctx.Cache,"1","1.db");
  var itemType=typeof(Egoist.WXQRussian.Program).GetNestedType("TransplantItem",System.Reflection.BindingFlags.NonPublic);
  var item=Activator.CreateInstance(itemType,true);
  itemType.GetField("Package").SetValue(item,package);itemType.GetField("Catalog").SetValue(item,catalog);
  itemType.GetField("Storage").SetValue(item,Path.Combine(ctx.Cache,"1","1_0.db"));
  itemType.GetField("Header").SetValue(item,Catalog.Parse(File.ReadAllBytes(catalog)).RawValues(OverridePackage.HeaderId)[0]);
  itemType.GetField("CatalogSha").SetValue(item,Util.Sha(catalog));
  var result=typeof(Egoist.WXQRussian.Program).GetMethod("BuildTransplant",System.Reflection.BindingFlags.Static|System.Reflection.BindingFlags.NonPublic)
   .Invoke(null,new object[]{ctx,item,(string)input["directory"],(string)input["dll"]});
  int applied=((System.Collections.ICollection)result.GetType().GetField("Applied").GetValue(result)).Count;
  int skipped=((System.Collections.ICollection)result.GetType().GetField("Skipped").GetValue(result)).Count;
  return new {ok=true,applied_count=applied,skipped_count=skipped,spool_removed=!Directory.Exists(Path.Combine((string)input["directory"],"text-spool"))};
 }
 static int Main(string[] args) {
  IntPtr db=IntPtr.Zero; var json=new JavaScriptSerializer { MaxJsonLength=32*1024*1024 };
  try {
   var input=(Dictionary<string,object>)json.DeserializeObject(File.ReadAllText(args[0],Encoding.UTF8));
   object mode;if(input.TryGetValue("mode",out mode)&&(string)mode=="lock"){Console.WriteLine(json.Serialize(Locked(input)));return 0;}
   if(input.TryGetValue("mode",out mode)&&(string)mode=="cleanup"){Console.WriteLine(json.Serialize(Cleanup(input)));return 0;}
   if(input.TryGetValue("mode",out mode)&&(string)mode=="cleanup-locked"){Console.WriteLine(json.Serialize(CleanupLocked(input)));return 0;}
   if(input.TryGetValue("mode",out mode)&&(string)mode=="zero"){Console.WriteLine(json.Serialize(ZeroApplied(input)));return 0;}
   string dll=(string)input["dll"];
   db=LoadLibraryEx(Path.Combine(Path.GetDirectoryName(dll),"QtskDB.dll"),IntPtr.Zero,0x1100);
   if(db==IntPtr.Zero)throw new IOException("SDK dependency "+Marshal.GetLastWin32Error());
   var files=new Dictionary<ulong,string>();
   foreach(var pair in (Dictionary<string,object>)input["paths"])files.Add(Convert.ToUInt64(pair.Key,16),(string)pair.Value);
   var result=CompatPacker.PackFiles(dll,(string)input["directory"],"1","1.7.0.1",7,files);
   Console.WriteLine(json.Serialize(new {ok=true,resources=result.ToDictionary(p=>p.Key.ToString("x16"),p=>new {sha256=p.Value.Sha,chunks=p.Value.ChunkData.Count,meta_bytes=p.Value.Meta.Length})}));
   return 0;
  }catch(Exception error){Console.WriteLine(json.Serialize(new {ok=false,type=error.GetType().Name,error=error.Message}));return 2;}
  finally{if(db!=IntPtr.Zero)FreeLibrary(db);}
 }
}
'''


def sha(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def check(name: str, condition: bool, detail: object = None) -> None:
    checks.append({'name': name, 'passed': bool(condition), 'detail': detail})
    print(('  ok   ' if condition else '  FAIL ') + name + (f' [{detail}]' if not condition else ''), flush=True)


def snapshot(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob('*')) if p.is_file()}


def temporary_work(path: Path | None) -> Path:
    result = Path(tempfile.mkdtemp(prefix='wxq-future-memory-')) if path is None else path.resolve()
    if Path(tempfile.gettempdir()).resolve() not in result.parents or not result.name.startswith('wxq-future-memory-'):
        raise ValueError('Все записи стенда разрешены только в tempfile wxq-future-memory-*')
    result.mkdir(parents=True, exist_ok=True)
    return result


def prepare(work: Path, carrier: Path, game: Path) -> dict:
    manifest = json.loads((carrier / 'manifest.json').read_text('utf-8-sig'))
    rows = [row for row in manifest['resources'] if row['kind'] in ('lang', 'global-localize')]
    assert len(rows) == 297 and {row['package'] for row in rows} == {'1'}
    payload = work / 'payload'
    payload.mkdir()
    for name in ('QtskDB.dll', 'QtsVFS.dll'):
        source = game / 'WXQ_Data/Plugins/x86_64' / name
        target = work / 'sdk' / name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
        assert sha(target) == sha(source)
    files = []
    for name in ('1.db', '1_0.db', '1.info'):
        source = carrier / 'packages/1' / name
        target = payload / ('p1_' + name)
        shutil.copyfile(source, target)
        files.append({'name': name, 'bytes': target.stat().st_size, 'sha256': sha(target), 'payload': target.name})
    descriptions = []
    resource_rows = []
    changed_rows = 0
    reader = pilot_contract.make_prime_reader(game, QtsContainer)
    try:
        with QtsContainer(carrier / 'packages/1/1_0.db') as translated_storage:
            for index, resource in enumerate(manifest['resources']):
                identifier = resource['id_hex']
                chunks = translated_storage.entries[int(identifier, 16)]
                row = {'id_hex': identifier, 'kind': resource['kind'], 'original_sha256': resource['original_sha256'],
                       'sha256': resource['sha256'], 'bytes': sum(c.uncompressed_size for c in chunks), 'chunks': len(chunks)}
                if resource['kind'] in ('lang', 'global-localize'):
                    original = reader('1', int(identifier, 16))
                    assert original is not None and digest(original) == resource['original_sha256'], identifier
                    translated = translated_storage.decode_entry(int(identifier, 16))
                    assert digest(translated) == resource['sha256'], identifier
                    reuse = make_reuse(resource['kind'], original, translated)
                    assert reuse is not None
                    content = encoded(reuse)
                    filename = 'p1_text_' + identifier + '.json'
                    (payload / filename).write_bytes(content)
                    row['text_reuse'] = {'schema': 1, 'codec': reuse['codec'], 'payload': filename,
                                         'sha256': digest(content), 'bytes': len(content), 'rows': len(reuse['rows'])}
                    current_path, expected_path = work / 'current' / (identifier + '.bin'), work / 'expected' / (identifier + '.bin')
                    write(current_path, original + UNKNOWN)
                    write(expected_path, translated + UNKNOWN)
                    assert digest(original + UNKNOWN) != row['original_sha256']
                    descriptions.append({'id_hex': identifier, 'kind': resource['kind'], 'rows': len(reuse['rows']),
                                         'current_path': str(current_path), 'current_sha256': sha(current_path),
                                         'expected_path': str(expected_path), 'expected_sha256': sha(expected_path)})
                    changed_rows += len(reuse['rows'])
                resource_rows.append(row)
                if (index + 1) % 25 == 0:
                    print(f'Подготовлено: {index + 1}/{len(manifest["resources"])} ресурсов', flush=True)
    finally:
        reader.close()
    install = {'product': 'WXQ real carrier14 memory stress', 'version': 'stress-memory', 'built': '2026-10-21',
               'game_version': manifest['game_version'], 'index_sha256': manifest['prime_global_index_sha256'],
               'sdk_hashes': {name: sha(work / 'sdk' / name) for name in ('QtskDB.dll', 'QtsVFS.dll')},
               'sdk_known': [sha(work / 'sdk/QtskDB.dll')], 'candidates': [carrier.name],
               'packages': [{'id': '1', 'prime_pkg_hash': str(manifest['prime_pkg_hashes']['1']),
                             'header_hex': manifest['override_package_headers_hex']['1'],
                             'info_sha256': manifest['official_cache_infos']['1']['sha256'],
                             'info_version': manifest['game_version'], 'files': files, 'resources': resource_rows}],
               'prime': [{'relative': '1/' + name, 'bytes': (carrier / 'packages/1' / name).stat().st_size,
                          'sha256': sha(carrier / 'packages/1' / name)} for name in ('1.db', '1_0.db')], 'known': {}}
    (payload / 'install.json').write_bytes(encoded(install))
    result = {'schema': 1, 'carrier': str(carrier), 'carrier_manifest_sha256': sha(carrier / 'manifest.json'),
              'maps': len(descriptions), 'changed_rows': changed_rows, 'unknown_wire_hex': UNKNOWN.hex(), 'resources': descriptions}
    (work / 'prepared.json').write_bytes(encoded(result))
    print(json.dumps({'maps': len(descriptions), 'changed_rows': changed_rows, 'prepared': str(work)}, ensure_ascii=False), flush=True)
    return result


def clone_prepared(work: Path, source: Path) -> dict:
    source = source.resolve()
    if source == work or Path(tempfile.gettempdir()).resolve() not in source.parents or not source.name.startswith('wxq-future-memory-'):
        raise ValueError('Подготовленные данные должны принадлежать другой нашей tempfile-коробке')
    prepared = json.loads((source / 'prepared.json').read_text('utf-8-sig'))
    for folder in ('current', 'expected', 'payload', 'sdk'):
        shutil.copytree(source / folder, work / folder)
    for row in prepared['resources']:
        for kind in ('current', 'expected'):
            path = work / kind / (row['id_hex'] + '.bin')
            assert sha(path) == row[kind + '_sha256']
            row[kind + '_path'] = str(path)
    prepared['prepared_from'] = str(source)
    (work / 'prepared.json').write_bytes(encoded(prepared))
    return prepared


class MemoryCounters(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                *[(name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                  'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage', 'PagefileUsage',
                  'PeakPagefileUsage', 'PrivateUsage')]]


def measured(arguments: list[str], cwd: Path, prefix: str, env: dict | None = None, timeout: int = 1800) -> dict:
    api = ctypes.WinDLL('psapi', use_last_error=True).GetProcessMemoryInfo
    api.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
    api.restype = wintypes.BOOL
    result = {'peak_working_set_bytes': 0, 'peak_pagefile_bytes': 0, 'sampled_private_bytes': 0, 'samples': 0}
    begin = time.monotonic()
    with (cwd / (prefix + '-stdout.bin')).open('wb') as stdout, (cwd / (prefix + '-stderr.bin')).open('wb') as stderr:
        process = subprocess.Popen(arguments, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        while True:
            counters = MemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if api(int(process._handle), ctypes.byref(counters), counters.cb):
                result['samples'] += 1
                result['peak_working_set_bytes'] = max(result['peak_working_set_bytes'], counters.PeakWorkingSetSize)
                result['peak_pagefile_bytes'] = max(result['peak_pagefile_bytes'], counters.PeakPagefileUsage)
                result['sampled_private_bytes'] = max(result['sampled_private_bytes'], counters.PrivateUsage)
            exit_code = process.poll()
            if exit_code is not None:
                result['exit_code'] = exit_code
                break
            if time.monotonic() - begin > timeout:
                process.kill()
                process.wait()
                result.update(exit_code=process.returncode, timeout_seconds=timeout)
                break
            time.sleep(.025)
    result['elapsed_seconds'] = round(time.monotonic() - begin, 3)
    (cwd / (prefix + '-memory.json')).write_bytes(encoded(result))
    assert result['samples'] > 0, 'GetProcessMemoryInfo did not yield own-child samples'
    return result


def compile_tools(work: Path) -> tuple[Path, Path]:
    stage = work / 'tools'
    stage.mkdir()
    bi.build_tool(stage, test_build=True)
    source, fixture_tool = stage / 'MemoryFixturePacker.cs', stage / 'MemoryFixturePacker.exe'
    source.write_text(FIXTURE_HARNESS, 'utf-8')
    template = PROJECT / 'installer/tests/fixtures/10.db.placeholder'
    arguments = [str(bi.CSC), '/nologo', '/utf8output', '/optimize+', '/platform:x64', '/target:exe', '/codepage:65001',
                 '/define:TESTBUILD', '/r:System.Web.Extensions.dll', '/r:Microsoft.CSharp.dll',
                 '/resource:' + str(template) + ',cache-template.db', '/main:MemoryFixturePacker', '/out:' + str(fixture_tool),
                 str(PROJECT / 'installer/WXQTool.cs'), str(PROJECT / 'installer/WXQCompatText.cs'),
                 str(PROJECT / 'installer/WXQCompatPack.cs'), str(source)]
    done = subprocess.run(arguments, capture_output=True, cwd=work, timeout=120)
    if done.returncode:
        raise RuntimeError((done.stdout + done.stderr).decode('utf-8', errors='replace'))
    tool = stage / 'WXQTool.exe'
    content = tool.read_bytes()
    offset = struct.unpack_from('<I', content, 0x3c)[0]
    assert content[offset:offset + 4] == b'PE\0\0' and struct.unpack_from('<H', content, offset + 4)[0] == 0x8664
    (work / 'compiled-sources.json').write_bytes(encoded({**{name: sha(PROJECT / 'installer' / name)
        for name in ('WXQTool.cs', 'WXQCompatText.cs', 'WXQCompatPack.cs')}, 'tool_sha256': sha(tool), 'machine': 'x64',
        'test_sha256': sha(Path(__file__)), 'fixture_harness_sha256': sha(source)}))
    return tool, fixture_tool


def fixture_pack(work: Path, fixture_tool: Path, name: str, descriptions: list[dict]) -> Path:
    folder = work / ('native-' + name)
    request = work / ('native-' + name + '.json')
    request.write_bytes(encoded({'dll': str(work / 'sdk/QtsVFS.dll'), 'directory': str(folder),
                                 'paths': {row['id_hex']: row['current_path'] for row in descriptions}}))
    metrics = measured([str(fixture_tool), str(request)], work, 'native-' + name)
    stdout = (work / ('native-' + name + '-stdout.bin')).read_text('utf-8', errors='replace')
    result = json.loads(next(line for line in reversed(stdout.splitlines()) if line.startswith('{')))
    if metrics['exit_code'] or not result.get('ok'):
        raise RuntimeError({'fixture': name, 'result': result, 'metrics': metrics})
    (work / ('native-' + name + '-result.json')).write_bytes(encoded({'result': result, 'metrics': metrics}))
    return folder / 'packed/1'


def read_lock_probe(work: Path, fixture_tool: Path, packed: Path, row: dict) -> dict:
    path = (packed / '1_0.db').resolve()
    assert work in path.parents
    request = work / 'read-lock-request.json'
    request.write_bytes(encoded({'mode': 'lock', 'path': str(path), 'file_sha256': sha(path),
                                 'id_hex': row['id_hex'], 'entry_sha256': row['current_sha256']}))
    metrics = measured([str(fixture_tool), str(request)], work, 'read-lock')
    stdout = (work / 'read-lock-stdout.bin').read_text('utf-8', errors='replace')
    result = json.loads(next(line for line in reversed(stdout.splitlines()) if line.startswith('{')))
    if metrics['exit_code'] or not result.get('ok'):
        raise RuntimeError({'read_lock': result, 'metrics': metrics})
    check('read-lock: обычная запись и переименование разрешены до открытия', result['control_write'] and result['control_rename'])
    check('read-lock: запись запрещена открытым Catalog', result['write_blocked'])
    check('read-lock: переименование запрещено открытым Catalog', result['rename_blocked'])
    check('read-lock: SHA относится к заблокированному файлу', result['hash_matches_locked_file'])
    check('read-lock: декодированы ожидаемые байты того же файла', result['decoded_matches'])
    check('read-lock: Dispose освобождает запись и переименование', result['released_after_dispose'])
    (work / 'read-lock-result.json').write_bytes(encoded({'result': result, 'metrics': metrics}))
    return result


def cleanup_probe(work: Path, fixture_tool: Path) -> dict:
    root = work / 'cleanup-guards'
    backup = root / 'data/backup'
    backup.mkdir(parents=True)
    target = root / 'data/backup-escape'
    target.mkdir()
    write(target / 'sentinel.bin', b'own tempfile sentinel; cleanup must not follow links')
    before = snapshot(target)
    results = {}

    def invoke(name: str, directory: Path, *, mode: str = 'cleanup', method: str = 'RemoveBuildScratch') -> dict:
        request = root / (name + '.json')
        request.write_bytes(encoded({'mode': mode, 'method': method, 'data': str(root / 'data'), 'directory': str(directory)}))
        metrics = measured([str(fixture_tool), str(request)], root, name)
        text = (root / (name + '-stdout.bin')).read_text('utf-8', errors='replace')
        value = json.loads(next(line for line in reversed(text.splitlines()) if line.startswith('{')))
        assert metrics['exit_code'] == 0 and value.get('ok'), value
        results[name] = value
        return value

    clean = backup / 'clean/built/1/text-spool'
    write(clean / 'nested/temporary.bin', b'own intermediate bytes')
    sibling = clean.parent / '1.db'
    write(sibling, b'keep final catalog')
    result = invoke('ordinary', clean)
    check('cleanup: обычный scratch удалён, итоговый sibling сохранён', not result['rejected'] and not clean.exists() and sibling.read_bytes() == b'keep final catalog')
    result = invoke('outside-boundary', target)
    check('cleanup: путь вне BackupRoot отклонён без изменений', result['rejected'] and snapshot(target) == before)
    result = invoke('outside-wrapper', target, method='TryRemoveBuildScratch')
    check('cleanup: wrapper не подавляет нарушение BackupRoot', result['rejected'] and snapshot(target) == before)

    script = root / 'junction.ps1'
    script.write_text('param([string]$Link,[string]$Target)\nNew-Item -ItemType Junction -Path $Link -Target $Target -ErrorAction Stop | Out-Null\n', 'utf-8')
    for name, nested in (('linked-child', True), ('linked-ancestor', False)):
        scratch = backup / name / 'built/1/text-spool'
        link = scratch / 'redirect' if nested else scratch
        link.parent.mkdir(parents=True)
        done = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                               '-File', str(script), str(link), str(target)], capture_output=True, cwd=root, timeout=60,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        if done.returncode:
            raise RuntimeError(done.stderr.decode('utf-8', errors='replace'))
        assert os.lstat(link).st_file_attributes & 0x400
        result = invoke(name, scratch)
        check('cleanup: ' + name + ' отклонён без перехода по junction', result['rejected'] and snapshot(target) == before)
        # Удаляется только сама проверенная junction, без рекурсивного обхода.
        assert work in link.absolute().parents and os.lstat(link).st_file_attributes & 0x400
        os.rmdir(link)
    locked = backup / 'locked/built/1/text-spool'
    write(locked / 'locked.tmp', b'held own scratch file')
    result = invoke('locked-wrapper', locked, mode='cleanup-locked')
    check('cleanup: wrapper возвращается при занятом собственном файле', result['wrapper_returned'] and result['retained_while_locked'])
    check('cleanup: после закрытия дескриптора strict cleanup успешен', result['removed_after_close'] and not locked.exists())
    check('cleanup: проверки занятости не изменили внешний sentinel', snapshot(target) == before)
    (work / 'cleanup-guard-result.json').write_bytes(encoded(results))
    return results


def scratch_entries(data: Path) -> list[str]:
    result = []
    backup = data / 'backup'
    if backup.exists():
        for label in backup.iterdir():
            for name in ('text-spool', 'text-pack'):
                candidate = label / 'built/1' / name
                if os.path.lexists(candidate):
                    result.append(candidate.relative_to(data).as_posix())
    return result


def sparse_gib(path: Path) -> None:
    # FSCTL_SET_SPARSE относится только к нашему открытому временному файлу.
    control = ctypes.WinDLL('kernel32', use_last_error=True).DeviceIoControl
    control.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    control.restype = wintypes.BOOL
    with path.open('r+b') as handle:
        returned = wintypes.DWORD()
        if not control(msvcrt.get_osfhandle(handle.fileno()), 0x900c4, None, 0, None, 0, ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
        handle.truncate(GIB)
        # used остаётся прежним, allocated отражает валидное пустое расширение.
        handle.seek(12)
        handle.write(struct.pack('<i', GIB))
    assert path.stat().st_size == GIB


@contextmanager
def hold_scratch(path: Path):
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                               wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    handle = api.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        api.CloseHandle(handle)


def run_case(work: Path, tool: Path, packed: Path, name: str, descriptions: list[dict], budget: int, *, sparse: bool) -> dict:
    root = work / name
    root.mkdir()
    game = root / 'Лаунчер с пробелом/Игра WXQ(1)'
    prime, cache = game / 'WXQ_Data/Package/builtin', game / 'QtsDataFolder/QtsVFSCache/packages'
    write(game / 'WXQ.exe', b'MZ synthetic marker; never executed')
    for filename in ('QtskDB.dll', 'QtsVFS.dll'):
        write(game / 'WXQ_Data/Plugins/x86_64' / filename, (work / 'sdk' / filename).read_bytes())
    for filename in ('1.db', '1_0.db'):
        target = prime / '1' / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(packed / filename, target)
    if sparse:
        sparse_gib(prime / '1/1_0.db')
    with QtsContainer(prime / '1/1_0.db') as container:
        assert container.invalid_record_count == 0
        for row in descriptions:
            assert digest(container.decode_entry(int(row['id_hex'], 16))) == row['current_sha256']
    header = bytearray(header_of(prime / '1/1.db'))
    header[88:96] = header[80:88]
    write(cache / '1/1.db', qts_container([(HEADER_ID, bytes(header))]))
    write(cache / '1/1.info', encoded({'version': '1.7.0.1', 'revision': '7'}))
    write(cache / 'builtin/Globalindex/GlobalIndexPrime.data', b'future memory fixture index')
    value = header_hash(header)
    log = cache.parent / 'QtsVFS.log'
    write(log, f'MountPackage 1, prime, 7 => succeed! test pkgHash:{value} basePkgHash:{value}\n'.encode())
    os.utime(log, (time.time() + 2, time.time() + 2))
    before, prime_before = snapshot(game), snapshot(prime)
    metrics = {}
    label = ''
    expected_ids = {row['id_hex'] for row in descriptions}
    expected_count = sum(row['rows'] for row in descriptions)
    busy_sequence = None
    for number, command in enumerate(('check', 'backup', 'unpack', 'verify', 'restore')):
        output = root / (command + '.txt')
        args = [str(tool), command, '--payload', str(work / 'payload'), '--data', str(root / 'data'),
                '--out', str(output), '--game', str(game)]
        if command in ('unpack', 'verify'):
            args.extend(['--label', label])
        env = dict(os.environ, WXQ_RU_USERS_ROOT=str(root / 'no-users'), WXQ_RU_TEST_CLOSE_ROOT=str(root),
                   WXQ_RU_TEST_NOKILL='1', APPDATA=str(root / 'profile'), WXQ_RU_TEST_FINAL_DELAY_COMMAND=command,
                   WXQ_RU_TEST_FINAL_DELAY_MS='150')
        busy = command == 'verify' and name == 'all297'
        if busy:
            built = root / 'data/backup' / label / 'built/1'
            busy_path = built / 'text-pack/locked.bin'
            write(busy_path, b'held by the own test process during the first verify')
            receipt_path = root / 'data/backup' / label / 'receipt.json'
            candidate_before = {filename: sha(built / filename) for filename in ('1.db', '1_0.db')}
            receipt_before, game_before_verify = sha(receipt_path), snapshot(game)
            with hold_scratch(busy_path):
                sample = measured(args, root, command, env)
                retained = busy_path.is_file()
        else:
            sample = measured(args, root, command, env)
        result = dict(line.partition('=')[::2] for line in output.read_text('utf-8-sig').splitlines() if '=' in line) if output.exists() else {}
        metrics[command] = sample
        check(name + ': ' + command, sample['exit_code'] == 0 and result.get('status') == 'ok', result)
        if sample['exit_code'] or result.get('status') != 'ok':
            raise RuntimeError({'command': command, 'metrics': sample, 'result': result})
        check(name + ': ' + command + ' peak <= ' + str(budget // MIB) + ' MiB',
              max(sample['peak_working_set_bytes'], sample['peak_pagefile_bytes'], sample['sampled_private_bytes']) <= budget,
              {key: round(value / MIB, 2) for key, value in sample.items() if key.endswith('_bytes')})
        if busy:
            state = json.loads((root / 'data/state.json').read_text('utf-8-sig'))
            install_log = (root / 'data/install.log').read_text('utf-8-sig')
            deferred = 'очистка промежуточной сборки отложена до проверки:' in install_log and str(busy_path.parent) in install_log
            candidates_kept = candidate_before == {filename: sha(built / filename) for filename in ('1.db', '1_0.db')}
            receipt_kept = sha(receipt_path) == receipt_before
            game_kept = snapshot(game) == game_before_verify
            check('busy-verify: установленное state подтверждено при занятом scratch', state.get('installed') is True)
            check('busy-verify: очистка отложена с путём в журнале', retained and deferred)
            check('busy-verify: receipt и итоговые db сохранены', receipt_kept and candidates_kept)
            check('busy-verify: игра не изменена очисткой', game_kept)
            retry = measured(args, root, 'verify-after-release', env)
            retried = dict(line.partition('=')[::2] for line in output.read_text('utf-8-sig').splitlines() if '=' in line)
            metrics['verify-after-release'] = retry
            check('busy-verify: после release повторный verify успешен', retry['exit_code'] == 0 and retried.get('status') == 'ok', retried)
            check('busy-verify: повторный verify peak <= 512 MiB', max(retry['peak_working_set_bytes'], retry['peak_pagefile_bytes'], retry['sampled_private_bytes']) <= budget)
            busy_sequence = {'retained_during_first_verify': retained, 'first_state_installed': state.get('installed'),
                             'deferred_log': deferred, 'receipt_preserved': receipt_kept, 'candidate_db_preserved': candidates_kept,
                             'game_unchanged': game_kept, 'first_verify': dict(result), 'second_verify': retried}
        if command in ('backup', 'verify'):
            current_label = result.get('label', '') if command == 'backup' else label
            built = root / 'data/backup' / current_label / 'built/1'
            entries = {path.name for path in built.iterdir()}
            scratch = scratch_entries(root / 'data')
            if command == 'backup' and scratch:
                install_log = (root / 'data/install.log').read_text('utf-8-sig')
                deferred = 'очистка промежуточной сборки отложена до проверки:' in install_log
                passed = {'1.db', '1_0.db'} <= entries <= {'1.db', '1_0.db', 'text-spool', 'text-pack'} and deferred
            else:
                passed = entries == {'1.db', '1_0.db'} and not scratch
            check(name + ': после ' + command + ' итоговые db сохранены; scratch только с defer до verify', passed,
                  {'entries': sorted(entries), 'scratch': scratch})
        if command == 'backup':
            label = result.get('label', '')
            receipt = json.loads((root / 'data/backup' / label / 'receipt.json').read_text('utf-8-sig'))
            applied = set(receipt.get('applied_resources', {}).get('1', []))
            reused = receipt.get('text_reuse', {}).get('1', {})
            check(name + ': все выбранные maps адаптированы', applied == expected_ids and set(reused) == expected_ids)
            check(name + ': все известные строки перенесены',
                  sum(row['applied_rows'] for row in reused.values()) == expected_count
                  and all(row['changed_source_rows'] == row['missing_rows'] == 0 for row in reused.values()),
                  {'expected_rows': expected_count})
        elif command == 'verify':
            with QtsContainer(cache / '1/1_0.db') as installed:
                check(name + ': только ожидаемые ресурсы в override',
                      set(installed.entries) == {int(identifier, 16) for identifier in expected_ids} and installed.invalid_record_count == 0)
                matches = all(installed.decode_entry(int(row['id_hex'], 16)) == Path(row['expected_path']).read_bytes() for row in descriptions)
                check(name + ': перевод и добавленное unknown field побайтно точны', matches)
    check(name + ': prime побайтно неизменен', snapshot(prime) == prime_before)
    check(name + ': restore вернул всю исходную игру', snapshot(game) == before)
    result = {'case': name, 'maps': len(descriptions), 'applied_rows': expected_count, 'storage_bytes': (prime / '1/1_0.db').stat().st_size,
              'budget_bytes': budget, 'final_observation_delay_ms': 150, 'commands': metrics}
    result['busy_verify'] = busy_sequence
    (root / 'case-result.json').write_bytes(encoded(result))
    return result


def negative_backup(work: Path, tool: Path, packed: Path, name: str, code: str, budget: int) -> dict:
    root = work / name
    game = root / 'game'
    prime = game / 'WXQ_Data/Package/builtin/1'
    cache = game / 'QtsDataFolder/QtsVFSCache/packages'
    write(game / 'WXQ.exe', b'MZ synthetic marker; never executed')
    for filename in ('QtskDB.dll', 'QtsVFS.dll'):
        write(game / 'WXQ_Data/Plugins/x86_64' / filename, (work / 'sdk' / filename).read_bytes())
    for filename in ('1.db', '1_0.db'):
        target = prime / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(packed / filename, target)
    header = bytearray(header_of(prime / '1.db'))
    header[88:96] = header[80:88]
    write(cache / '1/1.db', qts_container([(HEADER_ID, bytes(header))]))
    write(cache / '1/1.info', encoded({'version': '1.7.0.1', 'revision': 'invalid' if code == 'transplant' else '7'}))
    write(cache / 'builtin/Globalindex/GlobalIndexPrime.data', b'negative memory fixture index')
    value = header_hash(header)
    log = cache.parent / 'QtsVFS.log'
    write(log, f'MountPackage 1, prime, 7 => succeed! pkgHash:{value} basePkgHash:{value}\n'.encode())
    os.utime(log, (time.time() + 2, time.time() + 2))
    before = snapshot(game)
    output = root / 'backup.txt'
    env = dict(os.environ, WXQ_RU_USERS_ROOT=str(root / 'no-users'), WXQ_RU_TEST_CLOSE_ROOT=str(root),
               WXQ_RU_TEST_NOKILL='1', APPDATA=str(root / 'profile'), WXQ_RU_TEST_FINAL_DELAY_COMMAND='backup', WXQ_RU_TEST_FINAL_DELAY_MS='150')
    metrics = measured([str(tool), 'backup', '--payload', str(work / 'payload'), '--data', str(root / 'data'),
                        '--out', str(output), '--game', str(game)], root, 'backup', env)
    result = dict(line.partition('=')[::2] for line in output.read_text('utf-8-sig').splitlines() if '=' in line)
    check(name + ': ожидаемый отказ ' + code, metrics['exit_code'] == 2 and result.get('status') == 'stop' and result.get('code') == code, result)
    check(name + ': scratch отсутствует после отказа', not scratch_entries(root / 'data'))
    check(name + ': prepared receipt не оставлен', not list((root / 'data').glob('backup/*/receipt.json')))
    check(name + ': исходная игра побайтно сохранена', snapshot(game) == before)
    check(name + ': peak <= 512 MiB', max(metrics['peak_working_set_bytes'], metrics['peak_pagefile_bytes'], metrics['sampled_private_bytes']) <= budget)
    return {'case': name, 'result': result, 'metrics': metrics, 'scratch': scratch_entries(root / 'data')}


def zero_applied_probe(work: Path, fixture_tool: Path) -> dict:
    game = work / 'no-apply/game'
    data = work / 'zero-direct-data'
    built = data / 'backup/probe/built/1'
    spool = built / 'text-spool'
    write(spool / 'pre-existing-probe.original', b'prove matched0 removes an existing spool')
    sibling = built / 'keep-sibling.txt'
    write(sibling, b'no outer backup-label cleanup')
    before = snapshot(game)
    request = work / 'zero-direct.json'
    request.write_bytes(encoded({'mode': 'zero', 'game': str(game), 'data': str(data), 'directory': str(built),
                                 'payload': str(work / 'payload'), 'dll': str(work / 'sdk/QtskDB.dll')}))
    metrics = measured([str(fixture_tool), str(request)], work, 'zero-direct')
    stdout = (work / 'zero-direct-stdout.bin').read_text('utf-8', errors='replace')
    result = json.loads(next(line for line in reversed(stdout.splitlines()) if line.startswith('{')))
    if metrics['exit_code'] or not result.get('ok'):
        raise RuntimeError({'zero_applied': result, 'metrics': metrics})
    check('zero-direct: BuildTransplant вернул Applied=0 без внешнего cleanup', result['applied_count'] == 0 and result['skipped_count'] > 0)
    check('zero-direct: существовавший spool удалён самой веткой matched0', result['spool_removed'] and not os.path.lexists(spool))
    check('zero-direct: sibling и built сохранены', sibling.read_bytes() == b'no outer backup-label cleanup')
    check('zero-direct: исходная игра не изменена', snapshot(game) == before)
    (work / 'zero-direct-result.json').write_bytes(encoded({'result': result, 'metrics': metrics}))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path)
    parser.add_argument('--carrier', type=Path, default=CARRIER)
    parser.add_argument('--game-sources', type=Path, default=GAME_SOURCES)
    parser.add_argument('--prepared-from', type=Path, help='Копировать неизменяемые prepared-данные в новую tempfile-коробку.')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--budget-mib', type=int, default=512)
    args = parser.parse_args()
    work = temporary_work(args.work)
    print('Песочница: ' + str(work), flush=True)
    prepared = (json.loads((work / 'prepared.json').read_text('utf-8-sig')) if (work / 'prepared.json').exists()
                else clone_prepared(work, args.prepared_from) if args.prepared_from else prepare(work, args.carrier, args.game_sources))
    if args.prepare_only:
        return 0
    tool, fixture_tool = compile_tools(work)
    all_rows = prepared['resources']
    small = [min((row for row in all_rows if row['rows'] > 0), key=lambda row: Path(row['current_path']).stat().st_size)]
    cases = []
    locked = None
    guarded = None
    negative = []
    zero = None
    small_packed = None
    for name, descriptions, sparse in (('all297', all_rows, False), ('sparse1gib', small, True)):
        try:
            packed = fixture_pack(work, fixture_tool, name, descriptions)
            if name == 'all297':
                locked = read_lock_probe(work, fixture_tool, packed, descriptions[0])
                guarded = cleanup_probe(work, fixture_tool)
            else:
                small_packed = packed
            cases.append(run_case(work, tool, packed, name, descriptions, args.budget_mib * MIB, sparse=sparse))
        except Exception:
            detail = traceback.format_exc()
            (work / (name + '-failure.txt')).write_text(detail, 'utf-8')
            check(name + ': завершение сценария', False, detail)
    if small_packed is not None:
        try:
            negative.append(negative_backup(work, tool, small_packed, 'failed-after-spool', 'transplant', args.budget_mib * MIB))
            unknown_path = work / 'unknown-schema.bin'
            unknown_path.write_bytes(b'\x08\x01')
            unknown = dict(small[0], current_path=str(unknown_path))
            unknown_packed = fixture_pack(work, fixture_tool, 'unknown-schema', [unknown])
            negative.append(negative_backup(work, tool, unknown_packed, 'no-apply', 'no_match', args.budget_mib * MIB))
            zero = zero_applied_probe(work, fixture_tool)
        except Exception:
            detail = traceback.format_exc()
            (work / 'negative-failure.txt').write_text(detail, 'utf-8')
            check('negative scratch: завершение сценариев', False, detail)
    result = {'schema': 1, 'work': str(work), 'prepared_maps': prepared['maps'], 'prepared_changed_rows': prepared['changed_rows'],
              'budget_mib': args.budget_mib, 'cases': cases, 'checks': checks, 'failures': [row for row in checks if not row['passed']]}
    result['read_lock'] = locked
    result['cleanup_guards'], result['negative_backups'] = guarded, negative
    result['zero_applied'] = zero
    (work / 'memory-result.json').write_bytes(encoded(result))
    print(json.dumps({'work': str(work), 'maps': prepared['maps'], 'rows': prepared['changed_rows'],
                      'checks': len(checks), 'failures': len(result['failures']), 'cases': cases}, ensure_ascii=False, indent=2), flush=True)
    return 1 if result['failures'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
