"""Динамические проверки раннего выхода и ошибок запуска активатора.

Запускать только через isolated_test_run.py (private desktop + kill-on-close Job).
Исполняются исключительно fixture EXE в Temp. Настоящие игра/лаунчер/UAC не нужны.
--source позволяет проверить исторический исходник, не меняя production файлы.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_launcher_ru as fixture  # noqa: E402


EARLY_EXIT_SOURCE = r'''using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
class EarlyExitFixture {
 static int Main() {
  string log=Environment.GetEnvironmentVariable("FAKE_LOG");
  string count=Path.Combine(Path.GetDirectoryName(log),"start-count.txt");
  int n=File.Exists(count)?int.Parse(File.ReadAllText(count))+1:1;
  File.WriteAllText(count,n.ToString());
  File.AppendAllText(log,"{\"pid\":"+Process.GetCurrentProcess().Id+
   ",\"method\":\"start\",\"expr\":\"fixture-"+n+"\"}\n");
  if(n==1) return 13;
  Thread.Sleep(Timeout.Infinite); return 0;
 }
}'''

REQUIRE_ADMIN_SOURCE = r'''class NeedsAdminFixture {
 static int Main() { return 91; }
}'''

REQUIRE_ADMIN_MANIFEST = '''<?xml version="1.0" encoding="utf-8"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
 <assemblyIdentity version="1.0.0.0" name="WXQ.Test.NeedsAdmin"/>
 <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security>
  <requestedPrivileges><requestedExecutionLevel level="requireAdministrator" uiAccess="false"/>
  </requestedPrivileges></security></trustInfo>
</assembly>
'''


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_isolation() -> dict:
    """Не разрешаем fixture requireAdministrator даже начать вне privateDesktop/Job."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    user.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user.GetThreadDesktop.restype = wintypes.HANDLE
    user.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                              wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    in_job = wintypes.BOOL()
    if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(in_job)) or not in_job:
        raise RuntimeError('Запустите через isolated_test_run.py: Job отсутствует')
    desktop = user.GetThreadDesktop(kernel.GetCurrentThreadId())
    name = ctypes.create_unicode_buffer(512)
    needed = wintypes.DWORD()
    if not user.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not name.value.startswith('WXQTest-'):
        raise RuntimeError('Запустите через isolated_test_run.py: private desktop отсутствует')
    return {'in_job': True, 'private_desktop': name.value}


def run_activator(rig: fixture.Rig, *flags: str) -> tuple[int, dict]:
    command = [str(rig.activator), f'--test-port={rig.port}', f'--test-status-dir={rig.status_dir}',
               '--test-ignore-owner', f'--test-trust-root={rig.pf}', f'--test-game={rig.game}',
               '--test-flag=1', '--test-signal-activation', '--test-no-signature', *flags]
    # Дочерний fallback остаётся жить: pipe stdout/stderr держал бы communicate
    # после выхода активатора. Эти тесты читают статус/журнал, а не консоль.
    done = subprocess.run(command, env=rig.env(), stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
    path = rig.status_dir / 'launcher-status.json'
    status = json.loads(path.read_text('utf-8')) if path.exists() else {}
    return done.returncode, status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=fixture.INSTALLER / 'WXQLauncherRU.cs')
    args = parser.parse_args()
    work = args.work.resolve()
    if not work.is_relative_to(Path(os.environ['TEMP']).resolve()):
        raise ValueError('--work должен находиться в Temp')
    work.mkdir(parents=True, exist_ok=True)
    source = args.source.resolve(strict=True)
    source_sha = sha(source)
    details = {'source': str(source), 'source_sha256': source_sha,
               'test_sha256': sha(Path(__file__)), 'fixture_sha256': sha(Path(fixture.__file__)),
               'isolation': require_isolation(), 'checks': [], 'cases': [],
               'boundary': 'Only synthetic Temp PE fixtures and TESTBUILD activator. No live game, launcher, UAC, focus or input.'}

    def save() -> None:
        details['passed'] = bool(details['checks']) and all(row['passed'] for row in details['checks'])
        (work / 'result.json').write_text(json.dumps(details, ensure_ascii=False, indent=2) + '\n', 'utf-8')

    def check(name: str, passed: bool, detail: object = '') -> None:
        details['checks'].append({'name': name, 'passed': bool(passed), 'detail': detail})
        print(('PASS ' if passed else 'FAIL ') + name, flush=True)
        save()

    fixture.setUpModule()
    try:
        fixture.csc(['/target:winexe', '/define:TESTBUILD', *fixture.REFS,
                     '/r:System.Management.dll', str(source)], fixture.BIN / 'test.exe')
        details['activator_sha256'] = sha(fixture.BIN / 'test.exe')
        # PE без окна завершается при первом старте. При втором остаётся жить.
        early_source = work / 'EarlyExitFixture.cs'
        early_source.write_text(EARLY_EXIT_SOURCE, 'utf-8')
        early_exe = work / 'EarlyExitFixture.exe'
        fixture.csc(['/target:exe', str(early_source)], early_exe)
        rig = fixture.Rig('failure-owned-dead', launcher_src=early_exe)
        try:
            code, status = run_activator(rig)
            rig.wait_starts(2, seconds=5)
            starts = rig.rows('start')
            case = {'name': 'owned-died-before-activation', 'exit_code': code,
                    'status': status, 'starts': starts}
            details['cases'].append(case)
            check('owned-dead: ровно один fallback-перезапуск', len(starts) == 2, case)
            check('owned-dead: первый PID завершён', bool(starts) and not fixture.alive(starts[0]['pid']), case)
            check('owned-dead: fallback PID жив', len(starts) == 2 and fixture.alive(starts[1]['pid']), case)
            check('owned-dead: без инспектора', not fixture.port_listening(rig.port), case)
            check('owned-dead: fallback отмечен', status.get('result') == 'fallback' and code == 0, case)
        finally:
            rig.cleanup()

        # Повреждённый PE проходит profile SHA. Код зависит от Windows: 193/216.
        invalid = work / 'NotAPe.exe'
        invalid.write_bytes(b'WXQ synthetic invalid PE for native ERROR_BAD_EXE_FORMAT\n')
        rig = fixture.Rig('failure-native-193', launcher_src=invalid)
        try:
            native_error = None
            try:
                subprocess.run([str(rig.exe)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=5)
            except OSError as error:
                native_error = error.winerror
            code, status = run_activator(rig)
            case = {'name': 'native-bad-exe', 'native_error': native_error,
                    'exit_code': code, 'status': status}
            details['cases'].append(case)
            check('badPE: независимый CreateProcess вернул non-740 ошибку',
                  native_error is not None and native_error != 740, case)
            check('badPE: ненулевой код активатора', code != 0, case)
            check('badPE: настоящий код Windows сохранён',
                  status.get('code') == 'launch-failed-' + str(native_error), case)
            check('badPE: без инспектора', not fixture.port_listening(rig.port), case)
        finally:
            rig.cleanup()

        # asInvoker TESTBUILD + CreateProcess(false) не открывает UAC, а получает 740.
        # TESTBUILD ElevateSelf только пишет status и возвращает 740.
        admin_source = work / 'NeedsAdminFixture.cs'
        admin_source.write_text(REQUIRE_ADMIN_SOURCE, 'utf-8')
        admin_manifest = work / 'NeedsAdminFixture.manifest'
        admin_manifest.write_text(REQUIRE_ADMIN_MANIFEST, 'utf-8')
        admin_exe = work / 'NeedsAdminFixture.exe'
        fixture.csc(['/target:winexe', f'/win32manifest:{admin_manifest}', str(admin_source)], admin_exe)
        rig = fixture.Rig('failure-native-740', launcher_src=admin_exe)
        try:
            code, status = run_activator(rig, '--test-standard-user')
            case = {'name': 'native-elevation-required-test-guard', 'exit_code': code, 'status': status}
            details['cases'].append(case)
            check('native740: TESTBUILD вернул 740', code == 740, case)
            check('native740: elevation-required без UAC', status.get('code') == 'elevation-required', case)
            check('native740: без инспектора', not fixture.port_listening(rig.port), case)
        finally:
            rig.cleanup()
        check('исходник не менялся во время проверки', sha(source) == source_sha)
    finally:
        fixture.tearDownModule()
        save()
    print(json.dumps({'passed': details['passed'], 'checks': len(details['checks']),
                      'work': str(work)}, ensure_ascii=False), flush=True)
    return 0 if details['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
