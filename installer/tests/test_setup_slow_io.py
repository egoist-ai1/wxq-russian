"""Inno ждёт реальный helper после --out: медленный финал и поздний exit=9.

    python -X utf8 -B installer/tests/test_setup_slow_io.py [--compile-only]

Всё работает в tempfile через headless FakeUI/file protocol. Используются
штатные production source и TESTBUILD delay hook; проверки процессов/ACL не
отключаются. Настоящая игра, HKLM, ввод и фокус рабочего стола не затрагиваются.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_setup_manager as manager  # noqa: E402
from test_update import Env, Server, rsa  # noqa: E402

VERSION = '0.6.5'
DELAY_MS = 6000
results: list[dict] = []


def check(name: str, passed: bool, detail: object = '') -> bool:
    passed = bool(passed)
    print(('  ok   ' if passed else '  FAIL ') + name + (f' [{detail}]' if not passed and detail else ''), flush=True)
    results.append({'name': name, 'passed': passed, 'detail': str(detail) if not passed else ''})
    return passed


def text(path: Path) -> str:
    try:
        return path.read_text('utf-8-sig', errors='replace') if path.is_file() else ''
    except OSError:
        return ''


def rows(path: Path) -> dict[str, str]:
    return dict(line.split('=', 1) for line in text(path).splitlines() if '=' in line)


@contextmanager
def delay_environment(command: str, exit_code: int = 0):
    values = {'WXQ_RU_TEST_FINAL_DELAY_COMMAND': command, 'WXQ_RU_TEST_FINAL_DELAY_MS': str(DELAY_MS),
              'WXQ_RU_TEST_FINAL_EXIT_CODE': str(exit_code)}
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class FileTime(ctypes.Structure):
    _fields_ = [('low', ctypes.c_uint32), ('high', ctypes.c_uint32)]


class ProcessRef:
    """Дескриптор точного PID, а не проверка имени или общего мьютекса."""
    def __init__(self, pid: int):
        self.pid = pid
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        self.api.OpenProcess.restype = ctypes.c_void_p
        self.api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.api.WaitForSingleObject.restype = ctypes.c_uint32
        self.api.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        self.api.GetProcessTimes.argtypes = [ctypes.c_void_p, ctypes.POINTER(FileTime), ctypes.POINTER(FileTime),
                                            ctypes.POINTER(FileTime), ctypes.POINTER(FileTime)]
        self.api.CloseHandle.argtypes = [ctypes.c_void_p]
        self.handle = self.api.OpenProcess(0x100000 | 0x1000, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def running(self) -> bool:
        state = self.api.WaitForSingleObject(self.handle, 0)
        if state not in (0, 258):
            raise ctypes.WinError(ctypes.get_last_error())
        return state == 258

    def exit_code(self) -> int:
        value = ctypes.c_uint32()
        if not self.api.GetExitCodeProcess(self.handle, ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        return value.value

    def exit_time(self) -> float:
        created, exited, kernel, user = FileTime(), FileTime(), FileTime(), FileTime()
        if not self.api.GetProcessTimes(self.handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (((exited.high << 32) | exited.low) - 116444736000000000) / 10000000

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def log_time(path: Path, marker: str) -> float | None:
    for line in text(path).splitlines():
        if marker in line:
            return datetime.strptime(line[:23], '%Y-%m-%d %H:%M:%S.%f').astimezone().timestamp()
    return None


def expect_game_payload(box: manager.Sandbox, work: Path, install: dict) -> bool:
    return all((box.cache / '10' / row['name']).is_file()
               and manager.sha((box.cache / '10' / row['name']).read_bytes()) == row['sha256']
               for row in install['packages'][0]['files'])


@contextmanager
def deny_status_replace(path: Path):
    """Чтение разрешено; подмена terminal status.txt заблокирована средствами ОС."""
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


def status_snapshot_case(work: Path, executable: Path, install: dict, fixture: Env, fake_ui: Path) -> None:
    """Final --out должен обновить UI, даже если atomic replace live txt сорван."""
    box = manager.Sandbox(work / 'locked-status-snapshot' / 'Юникод и пробелы', install)
    before = manager.snapshot(box.game)
    server = Server()
    requested, release = threading.Event(), threading.Event()
    original_handler = server.httpd.RequestHandlerClass

    class GateHandler(original_handler):
        def do_GET(self):
            if self.path.split('?')[0] == '/update.json':
                requested.set()
                release.wait(20)
            super().do_GET()

    server.httpd.RequestHandlerClass = GateHandler
    feed = work / 'status-feed'
    feed.mkdir()
    env = Env(feed, fixture.tool, server, b'never executed', fixture.key, fixture.other_key)
    env.new_box(VERSION)
    env.publish(VERSION)
    session = None
    evidence = {}
    try:
        session = box.launch(executable, fake_ui, env.keys_file, server.url)
        reached = requested.wait(5)
        check('snapshot: helper дошёл до управляемого HTTP запроса', reached)
        status_txt = box.data / 'setup-update-status.txt'
        exists = manager.wait_for(status_txt.is_file, 3)
        check('snapshot: live txt содержит checking до ответа сервера', exists and rows(status_txt).get('state') == 'checking', rows(status_txt))
        if not exists:
            return
        with deny_status_replace(status_txt):
            release.set()
            out = session.ui / 'setup-update-out.txt'
            finished = manager.wait_for(lambda: rows(out).get('status') == 'ok', 6)
            check('snapshot: helper завершил signed проверку и записал final --out', finished, rows(out))
            check('snapshot: live txt намеренно остался checking, .new уже current', rows(status_txt).get('state') == 'checking'
                  and rows(Path(str(status_txt) + '.new')).get('state') == 'current', rows(status_txt))
            check('snapshot: final --out содержит полный terminal snapshot', rows(out).get('state') == 'current'
                  and rows(out).get('available') == VERSION and rows(out).get('message') == 'У вас актуальная версия.', rows(out))
            check('snapshot: UI всё равно current из final --out', session.wait(lambda s: s.get('update_state') == 'current', 5), session.state())
            check('snapshot: helper завершился, lock на live status ещё удерживается', manager.wait_for(lambda: not manager.helper_pids(box.root), 3))
            check('snapshot: terminal message в UI читается по-русски', session.state().get('update_message') == 'У вас актуальная версия.', session.state())
            evidence = {'live_txt': rows(status_txt), 'pending_txt': rows(Path(str(status_txt) + '.new')),
                        'final_out': rows(out), 'ui_state': session.state()}
        check('snapshot: игра/state не менялись', manager.snapshot(box.game) == before and not (box.data / 'state.json').exists())
        check('snapshot: закрытие окна после terminal snapshot', session.close())
    finally:
        release.set()
        if session is not None:
            session.stop()
        server.stop()
        manager.clean_test_key(work)
        (box.root / 'locked-status-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=1), 'utf-8')


def delayed_case(work: Path, executable: Path, install: dict, env: Env, fake_ui: Path,
                 name: str, command: str, late_exit: int = 0, request_close: bool = False) -> None:
    box = manager.Sandbox(work / name / 'Путь с пробелами и Юникодом', install)
    before = manager.snapshot(box.game)
    env.new_box(VERSION)
    env.publish(VERSION)
    with delay_environment(command, late_exit):
        session = box.launch(executable, fake_ui, env.keys_file, env.server.url)
    handle = None
    evidence = {'command': command, 'delay_ms': DELAY_MS, 'late_exit': late_exit, 'samples': []}
    try:
        welcome = session.wait(lambda s: s.get('screen') == 'welcome')
        check(name + ': startup UI/file protocol без native окна', welcome, session.state())
        if not welcome:
            return
        # Исключаем короткую проверку канала из списка PID операции установки.
        check(name + ': короткий startup helper завершён до установки',
              manager.wait_for(lambda: not manager.helper_pids(box.root), 12))
        session.command('install')
        started = manager.wait_for(lambda: f'Tool (live): {command}' in text(session.log)
                                   and rows(session.ui / 'tool-out.txt').get('status') == 'ok', 15)
        check(name + ': CreateProcessW запустил helper и получил успешный --out', started, text(session.log)[-1000:])
        if not started:
            return
        seen_out = time.monotonic()
        pids = manager.helper_pids(box.root)
        check(name + ': при готовом --out жив ровно один свой helper', len(pids) == 1, pids)
        if len(pids) != 1:
            return
        handle = ProcessRef(pids[0])
        check(name + ': точный process handle ещё не signaled', handle.running())
        print(f'NATIVE START PASS: {command}, PID={handle.pid}', flush=True)
        forbidden = 'Tool (live): backup' if command == 'check' else 'Tool (live): unpack'
        violations = []
        while handle.running() and time.monotonic() - seen_out < 10:
            state = session.state()
            log = text(session.log)
            sample = {'elapsed': round(time.monotonic() - seen_out, 3), 'pid': handle.pid,
                      'screen': state.get('screen'), 'stage': state.get('stage'), 'rev': state.get('rev'),
                      'next_started': forbidden in log, 'inno_alive': session.process.poll() is None}
            evidence['samples'].append(sample)
            if forbidden in log or state.get('screen') == 'done' or manager.snapshot(box.game) != before or not sample['inno_alive']:
                violations.append(sample)
            if request_close and time.monotonic() - seen_out > 2 and not evidence.get('close_requested'):
                session.command('close')
                evidence['close_requested'] = True
            time.sleep(0.07)
        waited = time.monotonic() - seen_out
        check(name + ': следующий этап не стартовал, пока этот PID жив', not violations, violations[:3])
        check(name + ': Inno дождался задержанного процесса, а не только --out', not handle.running() and waited >= 5.5, waited)
        evidence['waited_seconds'] = waited
        evidence['exit_code'] = handle.exit_code()
        evidence['exit_utc_unix'] = handle.exit_time() if not handle.running() else None
        check(name + ': exit code снят с того же process handle', handle.exit_code() == late_exit, handle.exit_code())
        if request_close:
            check(name + ': close во время операции не оборвал процесс/запись', evidence.get('close_requested')
                  and all(s['inno_alive'] for s in evidence['samples'])
                  and any(s.get('screen') == 'progress' for s in evidence['samples']))
        terminal = session.wait(lambda s: s.get('screen') in ('done', 'fail')
                                or (request_close and session.process.poll() is not None), 25)
        state = session.state()
        if late_exit:
            check(name + ': поздний exit=9 превращает успешный out в fail/tool_exit', terminal
                  and state.get('screen') == 'fail' and state.get('failure_code') == 'tool_exit', state)
            check(name + ': после exit=9 нет backup/unpack, game/state не изменены', forbidden not in text(session.log)
                  and 'Tool (live): unpack' not in text(session.log) and manager.snapshot(box.game) == before
                  and not (box.data / 'state.json').exists() and not list(box.data.glob('backup/*/receipt.json')))
            check(name + ': ложного done не было', not any(s.get('screen') == 'done' for s in session.history))
        else:
            saved = manager.read_json(box.data / 'state.json')
            # Отложенная команда close может закрыть окно сразу после commit.
            # В этом случае проверяем честное сохранённое состояние и exit Inno.
            completed = state.get('screen') == 'done' or (request_close and session.process.poll() == 0
                                                         and saved.get('installed') is True and expect_game_payload(box, work, install))
            check(name + ': после реального завершения результат честно подтверждён', terminal and completed, state)
            next_time = log_time(session.log, forbidden)
            check(name + ': timestamp следующего этапа не раньше exit этого PID', next_time is not None
                  and next_time + 0.015 >= handle.exit_time(), (next_time, handle.exit_time()))
            check(name + ': все synthetic payload bytes и installed state совпадают', expect_game_payload(box, work, install)
                  and saved.get('installed') is True and saved.get('version') == VERSION, saved)
        check(name + ': закрытие после честного результата', session.close())
        check(name + ': после закрытия helper своей песочницы не остаётся', manager.wait_for(lambda: not manager.helper_pids(box.root), 3))
    finally:
        if handle is not None:
            handle.close()
        session.stop()
        manager.clean_test_key(work)
        (box.root / 'slow-io-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=1), 'utf-8')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--compile-only', action='store_true')
    parser.add_argument('--case', choices=('all', 'check', 'remaining', 'late-exit', 'backup', 'status-snapshot'), default='all')
    options = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix='wxq-slow-io-test-'))
    print('Песочница: ' + str(work), flush=True)
    # Меняется только версия собственного synthetic fixture в этом Python-процессе.
    manager.VERSION = VERSION
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    import publish_release as pr
    config = {'schema': 1, 'base_url': 'http://127.0.0.1:9/',
              'keys': [{'key_id': pr.key_id_of(key.public_key()), 'public_key_pem': Env.pem(key)}]}
    executable, install = manager.compile_installer(work, config)
    if options.compile_only:
        return 0
    if manager.current_test_key_location() is not None:
        raise RuntimeError('существующая чужая TEST uninstall запись не изменяется')
    fake_ui = work / 'FakeUI.exe'
    manager.compile_fake(HERE / 'FakeUI.cs', fake_ui)
    server = Server()
    try:
        env = Env(work, work / 'staging/WXQTool.exe', server, b'not-executed', key,
                  rsa.generate_private_key(public_exponent=65537, key_size=3072))
        if options.case in ('all', 'check'):
            delayed_case(work, executable, install, env, fake_ui, 'slow-check', 'check')
        if options.case in ('all', 'remaining', 'late-exit'):
            delayed_case(work, executable, install, env, fake_ui, 'late-exit', 'check', late_exit=9)
        if options.case in ('all', 'remaining', 'backup'):
            delayed_case(work, executable, install, env, fake_ui, 'slow-backup-close', 'backup', request_close=True)
        if options.case in ('all', 'status-snapshot'):
            status_snapshot_case(work, executable, install, env, fake_ui)
    finally:
        server.stop()
        manager.clean_test_key(work)
        (work / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1), 'utf-8')
    failed = sum(not item['passed'] for item in results)
    print(f'Итог: {len(results)} проверок, {failed} ошибок. Песочница: {work}', flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
