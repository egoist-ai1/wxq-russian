"""Проверка Inno manager на крохотном synthetic пакете и неоконном FakeUI.

    python -X utf8 -B installer/tests/test_setup_manager.py --compile-only

Production Pascal/C#/WPF собираются без подмен через build_installer.
Только TESTBUILD разрешает временные пути, current-user setup и FakeUI.
Ввод/фокус рабочего стола, настоящая игра и запись в HKLM не используются.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import winreg
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / 'installer'))
sys.path.insert(0, str(HERE))
import build_installer as bi  # noqa: E402
import test_cache_reinstall as cache  # noqa: E402
from test_update import Env, Server, rsa  # noqa: E402
from test_setup_update import FAKE_SETUP  # noqa: E402

VERSION = '0.6.4'
failures: list[str] = []
TEST_UNINSTALL = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B3E1D8A4-6F21-4C7B-9A0E-5D2C8F4E7A19}-TEST_is1'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def check(name: str, condition: bool, detail: object = '') -> bool:
    print(('  ok   ' if condition else '  FAIL ') + name + (f' [{detail}]' if not condition and detail else ''), flush=True)
    if not condition:
        failures.append(name)
    return bool(condition)


def wait_for(condition, timeout: float = 20) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.05)
    return bool(condition())


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text('utf-8-sig')) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def snapshot(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha(p.read_bytes()) for p in sorted(root.rglob('*')) if p.is_file()}


def current_test_key_location() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_UNINSTALL, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return winreg.QueryValueEx(key, 'InstallLocation')[0]
    except FileNotFoundError:
        return None


def clean_test_key(work: Path) -> None:
    location = current_test_key_location()
    if location is None:
        return
    # Чужая запись не удаляется даже при совпадении тестового AppId.
    if not Path(location).resolve().is_relative_to(work.resolve()):
        raise RuntimeError('чужая TEST uninstall запись оставлена: ' + location)
    winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, TEST_UNINSTALL, winreg.KEY_WOW64_64KEY, 0)


def make_payload(work: Path) -> tuple[dict, list[tuple[Path, str]]]:
    """Один переведённый ресурс из двух байт; SDK/index/prime синтетические."""
    header = cache.header_of(HERE / 'fixtures/10.db.placeholder')
    official_info = json.dumps({'version': '1.6.0.1', 'revision': '0'}).encode()
    content = {
        '10.db': cache.qts_container([(cache.HEADER_ID, header), (cache.RESOURCE_ID, bytes(56))]),
        '10_0.db': cache.qts_container([(cache.RESOURCE_ID, b'ru')], chunks=True),
        '10.info': official_info,
    }
    entry = {'id': '10', 'prime_pkg_hash': cache.header_hash(header), 'header_hex': header.hex(),
             'info_sha256': sha(official_info), 'info_version': '1.6.0.1',
             'resources': [{'id_hex': f'{cache.RESOURCE_ID:016x}', 'kind': 'text', 'original_sha256': sha(b'cn'),
                            'sha256': sha(b'ru'), 'bytes': 2, 'chunks': 1}], 'files': []}
    files = []
    for name, blob in content.items():
        source = work / 'payload' / name
        write(source, blob)
        flat = 'p10_' + name
        files.append((source, flat))
        entry['files'].append({'name': name, 'bytes': len(blob), 'sha256': sha(blob), 'payload': flat})
    install = {'product': 'WXQ setup manager regression', 'version': VERSION, 'built': '2026-10-20',
               'game_version': '1.6.0.1', 'index_sha256': sha(cache.INDEX),
               'sdk_hashes': {name: sha(data) for name, data in cache.SDK.items()}, 'sdk_known': [],
               'candidates': ['synthetic-manager'], 'packages': [entry],
               'prime': [{'relative': name, 'bytes': len(data), 'sha256': sha(data)} for name, data in cache.PRIME.items()],
               'known': {}}
    return install, files


def compile_installer(work: Path, public_config: dict) -> tuple[Path, dict]:
    staging = work / 'staging'
    staging.mkdir()
    install, files = make_payload(work)
    # Штатные сборщики и Pascal source используются без отключения проверок.
    bi.make_staging(install, files, staging, with_tool=False, test_build=True)
    config = staging / 'update-config.json'
    config.write_text(json.dumps(public_config), 'utf-8')
    bi.build_tool(staging, test_build=True, update_config=config)
    bi.build_ui(staging, test_build=True)
    bi.write_include(staging, files)
    bi.assert_clean(install, files, staging)
    output = work / 'WXQ-Manager-Test.exe'
    command = [str(bi.ISCC), '/Q', *bi.iscc_defines(staging, output, VERSION, '2026-10-20', '1.6.0.1', '10', True),
               str(PROJECT / 'installer/WXQRussian.iss')]
    done = subprocess.run(command, capture_output=True, timeout=180)
    (work / 'iscc.log').write_bytes(done.stdout + done.stderr)
    if done.returncode:
        raise RuntimeError('ISCC FAIL:\n' + (done.stdout + done.stderr).decode('utf-8', errors='replace'))
    print('ISCC PASS: ' + str(output), flush=True)
    return output, install


def compile_fake(source: Path, target: Path) -> None:
    done = subprocess.run([str(bi.CSC), '/nologo', '/target:winexe', '/platform:x64', '/codepage:65001',
                           '/r:System.Web.Extensions.dll', f'/out:{target}', str(source)], capture_output=True)
    if done.returncode:
        raise RuntimeError((done.stdout + done.stderr).decode('cp866', errors='replace'))


def helper_pids(root: Path) -> list[int]:
    """Только чтение PID/пути: проверяем, что helper своей песочницы вышел."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    items = (ctypes.c_uint32 * 8192)()
    needed = ctypes.c_uint32()
    if not psapi.EnumProcesses(items, ctypes.sizeof(items), ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    found = []
    prefix = str(root.resolve()).casefold().rstrip('\\') + '\\'
    for pid in items[:needed.value // ctypes.sizeof(ctypes.c_uint32)]:
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            continue
        try:
            path = ctypes.create_unicode_buffer(32768)
            count = ctypes.c_uint32(len(path))
            if kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(count)) and path.value.casefold().startswith(prefix):
                name = Path(path.value).name.casefold()
                if name == 'wxqtool.exe' or name.startswith('handoff-'):
                    found.append(pid)
        finally:
            kernel.CloseHandle(handle)
    return found


class SlowServer:
    """Loopback-сервер принимает GET и не отвечает до завершения сценария."""
    def __init__(self):
        self.requested, self.release = threading.Event(), threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                outer.requested.set()
                outer.release.wait(30)
                try:
                    self.send_response(503)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}/'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.release.set()
        self.httpd.shutdown()


class Sandbox:
    def __init__(self, root: Path, install: dict):
        self.root, self.install = root, install
        self.game = root / 'Лаунчер с пробелом' / 'Игра WXQ'
        self.data, self.app = root / 'data', root / 'app'
        self.cache = self.game / 'QtsDataFolder/QtsVFSCache/packages'
        write(self.game / 'WXQ.exe', b'synthetic marker: never executed')
        for name, blob in cache.PRIME.items():
            write(self.game / 'WXQ_Data/Package/builtin' / name, blob)
        for name, blob in cache.SDK.items():
            write(self.game / 'WXQ_Data/Plugins/x86_64' / name, blob)
        write(self.cache / 'builtin/Globalindex/GlobalIndexPrime.data', cache.INDEX)
        write(self.cache / '10/10.db', (HERE / 'fixtures/10.db.placeholder').read_bytes())
        write(self.cache / '10/10.info', json.dumps({'version': '1.6.0.1', 'revision': '0'}).encode())
        pkg_hash = install['packages'][0]['prime_pkg_hash']
        write(self.cache.parent / 'QtsVFS.log', f'MountPackage 10, prime, 7 => succeed! test pkgHash:{pkg_hash} basePkgHash:{pkg_hash}\n'.encode())
        os.utime(self.cache.parent / 'QtsVFS.log', (time.time() + 2, time.time() + 2))
        self.index = 0
        self.sessions: list[Session] = []

    def launch(self, installer: Path, fake_ui: Path, keys: Path, base_url: str, extra: tuple[str, ...] = ()) -> 'Session':
        self.index += 1
        session = Session(self, self.index)
        self.sessions.append(session)
        environment = dict(os.environ, WXQ_RU_UPDATE_KEYS=str(keys), WXQ_RU_TEST_RETRY_MS='50',
                           WXQ_RU_USERS_ROOT=str(self.root / 'no-users'), WXQ_RU_TEST_CLOSE_ROOT=str(self.root),
                           WXQ_RU_TEST_NOKILL='1', APPDATA=str(self.root / 'profile'), FAKEUI_SCRIPT='hold',
                           FAKEUI_REPORT=str(session.report), WXQ_FAKE_LOG=str(self.root / 'updated-setup-args.json'))
        args = [str(installer), '/CURRENTUSER', f'/DIR={self.app}', f'/DATADIR={self.data}', f'/GAMEDIR={self.game}',
                f'/LOG={session.log}', f'/UISTATEDIR={session.ui}', f'/UIEXE={fake_ui}', f'/UIEXESHA={sha(fake_ui.read_bytes())}',
                f'/DESKTOPDIR={self.root / "desktop"}', f'/STARTDIR={self.root / "start"}', f'/UPDATEBASE={base_url}', *extra]
        output = session.console.open('wb')
        try:
            session.process = subprocess.Popen(args, env=environment, stdout=output, stderr=subprocess.STDOUT,
                                               creationflags=subprocess.CREATE_NO_WINDOW)
        finally:
            output.close()
        return session


class Session:
    def __init__(self, box: Sandbox, index: int):
        self.box = box
        self.ui = box.root / f'ui-{index}'
        self.log = box.root / f'inno-{index}.log'
        self.console = box.root / f'console-{index}.log'
        self.report = box.root / f'fakeui-{index}.txt'
        self.process: subprocess.Popen | None = None
        self.seq = 0
        self.history: list[dict] = []

    def state(self) -> dict:
        state = read_json(self.ui / 'state.json')
        if state and (not self.history or state.get('rev') != self.history[-1].get('rev')):
            self.history.append(state)
            (self.box.root / (self.ui.name + '-history.json')).write_text(json.dumps(self.history, ensure_ascii=False, indent=1), 'utf-8')
        return state

    def wait(self, predicate, timeout: float = 30) -> bool:
        return wait_for(lambda: predicate(self.state()), timeout)

    def command(self, command: str) -> None:
        self.seq += 1
        with (self.ui / 'commands.jsonl').open('a', encoding='utf-8', newline='\n') as out:
            out.write(json.dumps({'seq': self.seq, 'cmd': command}, separators=(',', ':')) + '\n')

    def close(self) -> bool:
        if self.process is None or self.process.poll() is not None:
            return True
        self.command('close')
        return wait_for(lambda: self.process.poll() is not None, 15)

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(self.process.pid)], capture_output=True)
            self.process.wait(timeout=15)


def runtime_checks(work: Path, executable: Path, install: dict, env: Env, fake_ui: Path) -> None:
    boxes: list[Sandbox] = []
    try:
        # Стартовая проверка сообщает о подписанном обновлении, не трогая игру.
        box = Sandbox(work / 'startup', install)
        boxes.append(box)
        env.new_box(VERSION)
        env.publish('0.6.5')
        before = snapshot(box.game)
        session = box.launch(executable, fake_ui, env.keys_file, env.server.url)
        check('M1 startup update_state=available в file protocol', session.wait(lambda s: s.get('update_state') == 'available'), session.state())
        check('M1-text русское сообщение обновления без mojibake', 'Доступна новая версия' in session.state().get('update_message', ''), session.state())
        check('M1a первая проверка не скачала EXE и не меняла игру', env.server.count('Setup.exe') == 0
              and snapshot(box.game) == before and not (box.data / 'state.json').exists())
        check('M1b закрытие через протокол завершает Inno', session.close())

        # Кнопка проводит download -> ready -> handoff и закрывает старый Inno.
        # Скачанный EXE только сохраняет аргументы, собственного окна у него нет.
        box = Sandbox(work / 'update-click', install)
        boxes.append(box)
        env.new_box(VERSION)
        env.publish('0.6.5')
        before = snapshot(box.game)
        session = box.launch(executable, fake_ui, env.keys_file, env.server.url)
        available = session.wait(lambda s: s.get('update_state') == 'available')
        check('M2 signed новая версия доступна', available, session.state())
        if available:
            session.command('update')
            check('M2a кнопка скачала и закрыла старый Inno', wait_for(lambda: session.process.poll() is not None, 35))
            marker = box.root / 'updated-setup-args.json'
            check('M2b handoff запустил загруженный EXE после закрытия Inno', wait_for(marker.is_file, 15))
            args = read_json(marker)
            check('M2c новый Setup получил видимый UPDATE/CONTINUE', isinstance(args, list) and '/UPDATE=1' in args
                  and '/CONTINUE=1' in args and not any(a.upper().startswith(('/SILENT', '/VERYSILENT')) for a in args), args)
        check('M2d update navigation не меняет игру', snapshot(box.game) == before)

        box = Sandbox(work / 'continue', install)
        boxes.append(box)
        env.new_box(VERSION)
        env.publish(VERSION)
        before = snapshot(box.game)
        session = box.launch(executable, fake_ui, env.keys_file, env.server.url, ('/UPDATE=1', '/CONTINUE=1'))
        done = session.wait(lambda s: s.get('screen') in ('done', 'fail'), 40)
        state = session.state()
        check('M3 CONTINUE запускает установку без команды install', done and state.get('screen') == 'done', state)
        installed = read_json(box.data / 'state.json')
        check('M3a synthetic игра получила перевод и installed state', installed.get('installed') is True
              and installed.get('version') == VERSION and snapshot(box.game) != before, installed)
        manager = box.app / 'WXQ-Russian.exe'
        check('M3b единый manager сохранён как точная копия Setup', manager.is_file() and sha(manager.read_bytes()) == sha(executable.read_bytes()))
        check('M3c завершение установки через протокол', session.close())

        if manager.is_file() and installed.get('installed') is True:
            # Запуск сохранённого файла должен пропустить копирование в самого себя.
            session = box.launch(manager, fake_ui, env.keys_file, env.server.url)
            check('M4 сохранённый manager показывает installed', session.wait(lambda s: s.get('screen') == 'installed'), session.state())
            session.command('install')
            check('M4a повторная установка из самого manager без self-copy ошибки', session.wait(lambda s: s.get('screen') in ('done', 'fail'), 40)
                  and session.state().get('screen') == 'done', session.state())
            check('M4b manager после self-copy skip не изменён', sha(manager.read_bytes()) == sha(executable.read_bytes()))
            check('M4c закрытие manager', session.close())

            session = box.launch(manager, fake_ui, env.keys_file, env.server.url)
            check('M5 до отката экран installed', session.wait(lambda s: s.get('screen') == 'installed'), session.state())
            session.command('remove')
            removed = session.wait(lambda s: s.get('screen') in ('removed', 'fail'), 30)
            check('M5a remove -> removed и байты игры восстановлены', removed and session.state().get('screen') == 'removed'
                  and snapshot(box.game) == before, session.state())
            session.command('back')
            welcome = session.wait(lambda s: s.get('screen') == 'welcome')
            check('M5b back -> welcome без закрытия manager', welcome, session.state())
            if welcome:
                session.command('install')
                check('M5c та же сессия переустанавливает после отката', session.wait(lambda s: s.get('screen') in ('done', 'fail'), 40)
                      and session.state().get('screen') == 'done', session.state())
            check('M5d закрытие после переустановки', session.close())

        box = Sandbox(work / 'continue-mismatch', install)
        boxes.append(box)
        write(box.cache / '10/10.info', b'{"version":"9.9.9.9","revision":"0"}')
        before = snapshot(box.game)
        env.new_box(VERSION)
        env.publish(VERSION)
        session = box.launch(executable, fake_ui, env.keys_file, env.server.url, ('/UPDATE=1', '/CONTINUE=1'))
        check('M6 CONTINUE с несовместимой synthetic игрой завершает проверку', session.wait(lambda s: s.get('screen') in ('done', 'fail'), 40), session.state())
        check('M6a несовместимая версия: fail, нет записи в игру/state', session.state().get('screen') == 'fail'
              and snapshot(box.game) == before and not (box.data / 'state.json').exists(), session.state())
        check('M6b окно сбоя закрывается', session.close())

        for close_early in (False, True):
            box = Sandbox(work / ('slow-close' if close_early else 'slow-deadline'), install)
            boxes.append(box)
            before = snapshot(box.game)
            slow = SlowServer()
            try:
                session = box.launch(executable, fake_ui, env.keys_file, slow.url)
                requested = slow.requested.wait(5)
                label = 'M8' if close_early else 'M7'
                check(label + ' проверка обратилась к медленному loopback feed', requested)
                started = time.monotonic()
                if not close_early:
                    failed = session.wait(lambda s: s.get('update_state') == 'failed', 14)
                    check('M7a медленный feed ограничен временем; установка остаётся доступной', failed
                          and session.state().get('screen') == 'welcome' and time.monotonic() - started < 14, session.state())
                check(label + 'b close при ожидании сети завершает Inno', session.close())
                check(label + 'c helper завершается вместе с окном', wait_for(lambda: not helper_pids(box.root), 3), helper_pids(box.root))
                check(label + 'd игра не изменилась', snapshot(box.game) == before and not (box.data / 'state.json').exists())
            finally:
                slow.stop()
    finally:
        for box in boxes:
            for session in box.sessions:
                session.stop()
        clean_test_key(work)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--compile-only', action='store_true')
    parser.add_argument('--work', type=Path)
    options = parser.parse_args()
    work = options.work or Path(tempfile.mkdtemp(prefix='wxq-manager-test-'))
    work.mkdir(parents=True, exist_ok=True)
    print('Песочница: ' + str(work), flush=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    import publish_release as pr
    config = {'schema': 1, 'base_url': 'http://127.0.0.1:9/',
              'keys': [{'key_id': pr.key_id_of(key.public_key()), 'public_key_pem': Env.pem(key)}]}
    executable, install = compile_installer(work, config)
    if options.compile_only:
        return 0
    if current_test_key_location() is not None:
        raise RuntimeError('существует чужая TEST uninstall запись; тест её не меняет')
    fake_ui = work / 'FakeUI.exe'
    compile_fake(HERE / 'FakeUI.cs', fake_ui)
    source = work / 'FakeSetup.cs'
    source.write_text(FAKE_SETUP, 'utf-8')
    fake_setup = work / 'FakeSetup.exe'
    compile_fake(source, fake_setup)
    server = Server()
    try:
        env = Env(work, work / 'staging/WXQTool.exe', server, fake_setup.read_bytes(), key,
                  rsa.generate_private_key(public_exponent=65537, key_size=3072))
        runtime_checks(work, executable, install, env, fake_ui)
    finally:
        server.stop()
    print(f'Итог: {len(failures)} ошибок. Песочница: {work}', flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
