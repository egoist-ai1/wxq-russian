"""Проверка активатора WXQLauncherRU.exe без настоящего лаунчера и игры.

    python -X utf8 -B installer/tests/test_launcher_ru.py [-v]

Собирает штатным csc (.NET Framework 4, C# 5): тестовую сборку активатора (/define:TESTBUILD, без манифеста администратора),
стенд FakeInspector.cs (поддельный лаунчер: окно + inspector с /json/list, WebSocket и Runtime.evaluate),
релизную сборку (с манифестом requireAdministrator и ресурсом launcher-expected.txt). Все предупреждения компилятора считаются ошибкой.
Сценарии идут во временной папке; каталог launcher\\ и его родитель закрываются icacls так же, как Program Files.
Раскладка активатора и target.txt берётся из WXQRussian.iss (DestDir и SaveStringsToUTF8File), а не задана в тесте: exe, не лежащий рядом
с target.txt, сразу даёт no-target.
Реальный лаунчер в C:\\Program Files\\WXQLauncher и игра не запускаются. Node берётся из runtime Codex (для проверки process._debugProcess).
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
INSTALLER = HERE.parent
PROJECT = INSTALLER.parent
sys.path.insert(0, str(INSTALLER))
LAUNCHER_BUILD = PROJECT / 'build' / 'launcher-ru-2026-10-14-faf8e0c3'
CSC = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
NODE = Path(os.path.expanduser('~')) / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
EXE_NAME = '王者万象棋.exe'
BUILD = '26083.0.7.30004'
REFS = ['/r:System.dll', '/r:System.Core.dll', '/r:System.Web.Extensions.dll']
FAKE_RUNTIME = ("exports.install=function(o){require('fs').writeFileSync(process.env.FAKE_MARKER,"
                "JSON.stringify({dict:o.dictionaryPath,sha:o.dictionarySha256,hasElectron:!!o.electron}));};\n")

WORK: Path
BIN: Path
RELEASE_EXPECTED = {'runtime_sha256': 'a' * 64, 'dictionary_sha256': 'b' * 64}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def csc(args: list[str], out: Path) -> None:
    """args: ключи csc, последним элементом исходный файл."""
    proc = subprocess.run([str(CSC), '/nologo', '/warnaserror+', '/optimize+', '/platform:x64', *args[:-1], f'/out:{out}', args[-1]],
                          capture_output=True, cwd=INSTALLER)
    text = (proc.stdout + proc.stderr).decode('cp866', 'replace')
    if proc.returncode != 0 or 'warning' in text.lower():
        raise RuntimeError('csc: ' + text)


def setUpModule() -> None:
    global WORK, BIN
    WORK = Path(tempfile.mkdtemp(prefix='wxqlru-'))
    BIN = WORK / 'bin'
    BIN.mkdir()
    csc(['/target:winexe', '/define:TESTBUILD', *REFS, '/r:System.Management.dll', str(INSTALLER / 'WXQLauncherRU.cs')], BIN / 'test.exe')
    csc(['/target:winexe', *REFS, '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll', str(HERE / 'FakeInspector.cs')], BIN / 'fake.exe')
    expected = BIN / 'expected.txt'
    expected.write_text(''.join(f'{k}={v}\n' for k, v in RELEASE_EXPECTED.items()), encoding='utf-8')
    csc(['/target:winexe', *REFS, '/r:System.Management.dll', f'/win32manifest:{INSTALLER / "WXQLauncherRU.manifest"}',
         f'/resource:{expected},launcher-expected.txt', str(INSTALLER / 'WXQLauncherRU.cs')], BIN / 'release.exe')


def tearDownModule() -> None:
    kill_under(WORK)
    shutil.rmtree(WORK, ignore_errors=True)


def kill_under(root: Path) -> None:
    """Останавливает только процессы, чей exe лежит в нашей временной папке."""
    script = (f"Get-CimInstance Win32_Process | Where-Object {{ $_.ExecutablePath -like '{root}\\*' }} | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True)


def alive(pid: int) -> bool:
    out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'], capture_output=True).stdout.decode('cp866', 'replace')
    return str(pid) in out


def free_port() -> int:
    for _ in range(50):
        port = random.randint(20000, 60000)
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    raise RuntimeError('нет свободного порта')


def port_listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(('127.0.0.1', port)) == 0


def iss_layout(app: Path) -> tuple[Path, Path]:
    """Где установщик кладёт активатор и куда пишет target.txt (из WXQRussian.iss), с {app} = app."""
    iss = (INSTALLER / 'WXQRussian.iss').read_text('utf-8-sig')
    exe = re.search(r'Source: "\{#Staging\}\\(?:launcher\\)?WXQLauncherRU\.exe"; DestDir: "([^"]+)"', iss)
    target = re.search(r"SaveStringsToUTF8File\(ExpandConstant\('([^']*target\.txt)'\)", iss)
    assert exe and target, 'в WXQRussian.iss не найдены активатор или target.txt'
    place = lambda text: Path(text.replace('{app}', str(app)))
    return place(exe.group(1)) / 'WXQLauncherRU.exe', place(target.group(1))


def iss_dest_dirs() -> dict[str, str]:
    """DestDir каждого файла лаунчера из [Files] (имя -> DestDir)."""
    iss = (INSTALLER / 'WXQRussian.iss').read_text('utf-8-sig')
    return {name: dest for name, dest in re.findall(r'Source: "\{#Staging\}\\launcher\\([^"]+)"; DestDir: "([^"]+)"', iss)}


class Rig:
    """Каталог сценария: «установка лаунчера» (PF), папка launcher\\ активатора, статус и журнал стенда."""

    def __init__(self, name: str, launcher_src: Path | None = None, thumbprint: str = '0' * 40, lock: bool = True) -> None:
        self.root = WORK / name
        self.pf = self.root / 'PF'
        self.build = self.pf / 'WXQLauncher' / BUILD
        self.launcher_dir = self.root / 'app' / 'launcher'
        self.status_dir = self.root / 'status'
        self.log = self.root / 'fake.log'
        self.marker = self.root / 'marker.json'
        self.port = free_port()
        self.game = 'FakeGame' + str(self.port)
        for d in (self.build / 'resources' / 'app.asar.unpacked' / 'node_modules' / '@tencent' / 'native-resource' / 'dist' / 'lib' / 'locales-i18n',
                  self.launcher_dir, self.status_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.exe = self.build / EXE_NAME
        shutil.copy2(launcher_src or (BIN / 'fake.exe'), self.exe)
        lib = self.build / 'resources' / 'app.asar.unpacked' / 'node_modules' / '@tencent' / 'native-resource' / 'dist' / 'lib'
        self.asar = self.build / 'resources' / 'app.asar'
        self.asar.write_bytes(b'asar-' + name.encode())
        (lib / 'skin.zip').write_bytes(b'skin-' + name.encode())
        (lib / 'locales-i18n' / 'launcher.json').write_text('{"a":"b"}', encoding='utf-8')
        self.activator, self.target_txt = iss_layout(self.root / 'app')
        self.activator.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BIN / 'test.exe', self.activator)
        self.target_txt.write_text(str(self.exe) + '\n', encoding='utf-8')
        runtime = self.launcher_dir / 'launcher-ru-runtime.cjs'
        runtime.write_text(FAKE_RUNTIME, encoding='utf-8')
        dictionary = self.launcher_dir / 'launcher-ru.json'
        dictionary.write_text('{"你好":"Привет"}', encoding='utf-8')
        (self.launcher_dir / 'launcher-expected.txt').write_text(
            f'runtime_sha256={sha(runtime)}\ndictionary_sha256={sha(dictionary)}\n', encoding='utf-8')
        profile = {'schema': 1, 'builds': [{
            'build': BUILD, 'exe_rel': f'{BUILD}\\{EXE_NAME}', 'signer_thumbprint': thumbprint,
            'sha256': {'exe': sha(self.exe), 'app_asar': sha(self.asar), 'skin_zip': sha(lib / 'skin.zip'),
                       'launcher_json': sha(lib / 'locales-i18n' / 'launcher.json')}}]}
        (self.launcher_dir / 'profiles.json').write_text(json.dumps(profile, ensure_ascii=False), encoding='utf-8')
        if lock:
            self.lock()

    GRANTS = ['/inheritance:r', '/grant:r', '*S-1-5-18:(OI)(CI)F', '/grant:r', '*S-1-5-32-544:(OI)(CI)F', '/grant:r', '*S-1-5-32-545:(OI)(CI)RX', '/C', '/Q']

    def lock(self) -> None:
        """Как установщик в Program Files: {app} и launcher\\ закрыты, писать могут только SYSTEM и администраторы, пользователям чтение."""
        subprocess.run(['icacls', str(self.launcher_dir.parent), *self.GRANTS], check=True, capture_output=True)
        subprocess.run(['icacls', str(self.launcher_dir), *self.GRANTS], check=True, capture_output=True)
        # файлы наследуют права от каталога (иначе при /T флаги OI/CI оставят их без записей)
        subprocess.run(['icacls', str(self.launcher_dir / '*'), '/reset', '/T', '/C', '/Q'], check=True, capture_output=True)

    def unlock(self) -> None:
        subprocess.run(['icacls', str(self.launcher_dir), '/inheritance:e', '/C', '/Q'], capture_output=True)
        self.unlock_parent()

    def unlock_parent(self) -> None:
        subprocess.run(['icacls', str(self.launcher_dir.parent), '/inheritance:e', '/C', '/Q'], capture_output=True)

    def junction_launcher_tree(self) -> None:
        """Каталог WXQLauncher в «Program Files» становится junction на папку вне его (обычный пользователь может так подменить путь цели)."""
        real = self.root / 'elsewhere'
        real.mkdir()
        shutil.move(str(self.pf / 'WXQLauncher'), str(real / 'WXQLauncher'))
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(self.pf / 'WXQLauncher'), str(real / 'WXQLauncher')], check=True, capture_output=True)

    def env(self, **extra: str) -> dict[str, str]:
        env = dict(os.environ, FAKE_PORT=str(self.port), FAKE_LOG=str(self.log), FAKE_MARKER=str(self.marker))
        env.update(extra)
        return env

    def run(self, *flags: str, signature: bool = False, signal: bool = True, env: dict[str, str] | None = None) -> dict:
        args = [str(self.activator), f'--test-port={self.port}', f'--test-status-dir={self.status_dir}',
                '--test-ignore-owner', f'--test-trust-root={self.pf}', f'--test-game={self.game}', '--test-flag=1', *flags]
        if signal:
            args.append('--test-signal-activation')  # поддельный лаунчер открывает inspector по событию, а не через _debugProcess
        if not signature:
            args.append('--test-no-signature')
        subprocess.run(args, env=env or self.env(), timeout=120, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        status = self.status_dir / 'launcher-status.json'
        return json.loads(status.read_text(encoding='utf-8')) if status.exists() else {}

    def spawn_fake(self, **extra: str) -> subprocess.Popen:
        proc = subprocess.Popen([str(self.exe)], env=self.env(**extra), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 15
        while time.time() < deadline and not self.rows('start'):
            time.sleep(0.1)
        time.sleep(1.5)  # окно и событие активации создаются сразу после старта
        return proc

    def wait_starts(self, count: int, seconds: float = 10) -> None:
        """Оригинал, запущенный активатором, пишет в журнал чуть позже выхода активатора."""
        deadline = time.time() + seconds
        while time.time() < deadline and len(self.rows('start')) < count:
            time.sleep(0.1)

    def rows(self, method: str | None = None) -> list[dict]:
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines() if line.strip()]
        return [r for r in rows if method is None or r['method'] == method]

    def evaluations(self) -> list[dict]:
        return self.rows('Runtime.evaluate')

    def launcher_pid(self) -> int | None:
        rows = self.rows('start')
        return rows[0]['pid'] if rows else None

    def cleanup(self) -> None:
        kill_under(self.root)
        self.unlock()


class LauncherRU(unittest.TestCase):
    rig: Rig

    def make(self, name: str, **kwargs) -> Rig:
        self.rig = Rig(name, **kwargs)
        self.addCleanup(self.rig.cleanup)
        return self.rig

    # ---------- успешные сценарии ----------

    def check_attached(self, rig: Rig, status: dict) -> None:
        self.assertEqual((status.get('result'), status.get('code')), ('ok', 'attached'), status)
        self.assertEqual(status['profile'], BUILD)
        self.assertEqual(sorted(status), ['code', 'profile', 'result', 'time'])
        # Только Runtime.evaluate, ровно два вызова: личность и одно выражение подключения. Никакого Debugger.
        rows = [r for r in rig.rows() if r['method'] not in ('start', 'http')]
        self.assertTrue(all(r['method'] == 'Runtime.evaluate' for r in rows), rows)
        self.assertEqual(len(rows), 2, rows)
        self.assertIn('process.uptime()', rows[0]['expr'])
        install = rows[1]['expr']
        runtime = rig.launcher_dir / 'launcher-ru-runtime.cjs'
        self.assertIn(sha(runtime), install)
        self.assertIn(sha(rig.launcher_dir / 'launcher-ru.json'), install)
        self.assertIn('.install(', install)
        self.assertIn('getBuiltinModule("inspector").close()', install)
        self.assertNotIn('Debugger', ''.join(r['expr'] for r in rows))
        self.assertFalse(port_listening(rig.port), 'порт должен закрыться')
        self.assertTrue(alive(rig.launcher_pid()), 'лаунчер должен остаться жить')

    def test_attach_to_own_started_launcher(self) -> None:
        rig = self.make('own')
        status = rig.run()
        self.check_attached(rig, status)
        rig.wait_starts(1)
        self.assertEqual(len(rig.rows('start')), 1)

    def test_attach_to_running_launcher(self) -> None:
        rig = self.make('running')
        proc = rig.spawn_fake()
        status = rig.run()
        self.check_attached(rig, status)
        rig.wait_starts(1)
        self.assertEqual(len(rig.rows('start')), 1, 'второй лаунчер запускать нельзя')
        self.assertIsNone(proc.poll())

    def test_node_process_debug_mechanics(self) -> None:
        """Настоящая механика process._debugProcess на обычном Node (подписан, проверяется Authenticode)."""
        if not NODE.exists():
            self.skipTest('нет node.exe')
        thumbprint = subprocess.run(['powershell', '-NoProfile', '-Command', f"(Get-AuthenticodeSignature -LiteralPath '{NODE}').SignerCertificate.Thumbprint"],
                                    capture_output=True, text=True).stdout.strip()
        self.assertEqual(len(thumbprint), 40)
        rig = self.make('node', launcher_src=NODE, thumbprint=thumbprint)
        node = subprocess.Popen([str(rig.exe), f'--inspect-port={rig.port}', '-e', 'setInterval(()=>{},1e3)'], env=rig.env())
        time.sleep(1.5)
        self.assertFalse(port_listening(rig.port))
        # заголовок цели у обычного Node не «electron/js2c/browser_init»; в тестовой сборке задаём префикс
        probe = rig.run('--test-title=', signature=True, signal=False)  # подпись проверяется по-настоящему
        self.assertIsNone(node.poll(), 'Node должен остаться жить')
        self.assertEqual((probe.get('result'), probe.get('code')), ('ok', 'attached'), probe)
        self.assertTrue(rig.marker.exists(), 'модуль не загружен в процесс')
        data = json.loads(rig.marker.read_text(encoding='utf-8'))
        self.assertEqual(data['sha'], sha(rig.launcher_dir / 'launcher-ru.json'))
        self.assertFalse(port_listening(rig.port), 'inspector должен закрыться')
        # Пока порт закрыт, второй запуск снова проходит тем же путём.
        rig.marker.unlink()
        again = rig.run('--test-title=', signature=True, signal=False)
        self.assertEqual(again.get('code'), 'attached', again)
        self.assertTrue(rig.marker.exists())

    # ---------- отказы цели и порта ----------

    def refusal(self, rig: Rig, status: dict, code: str) -> None:
        self.assertEqual(status.get('result'), 'fallback', status)
        self.assertTrue(status['code'].split(';')[0] == code, status)
        self.assertFalse([r for r in rig.evaluations() if '.install(' in r['expr']], 'перевод не должен подключаться')

    def test_wrong_target_title_closes_owned_launcher_and_restarts_original(self) -> None:
        rig = self.make('title')
        status = rig.run(env=rig.env(FAKE_MODE='wrongtitle'))
        self.refusal(rig, status, 'target-list')
        self.assertEqual(status['code'], 'target-list;port-open-closed')
        self.assertEqual(rig.evaluations(), [])
        first = rig.launcher_pid()
        rig.wait_starts(2)
        self.assertEqual(len(rig.rows('start')), 2, 'свой процесс с открытым inspector закрыт, оригинал запущен заново')
        self.assertFalse(alive(first), 'закрыт мягко (CloseMainWindow)')

    def test_two_targets(self) -> None:
        rig = self.make('two')
        rig.spawn_fake(FAKE_MODE='two')
        status = rig.run(env=rig.env(FAKE_MODE='two'))
        self.refusal(rig, status, 'target-list')
        self.assertEqual(status['code'], 'target-list;port-open-left', 'чужой процесс не трогаем')
        self.assertTrue(alive(rig.launcher_pid()))

    def test_websocket_address_not_loopback(self) -> None:
        rig = self.make('badws')
        rig.spawn_fake(FAKE_MODE='badws')
        status = rig.run()
        self.refusal(rig, status, 'target-list')
        self.assertEqual(rig.evaluations(), [])

    def test_listener_address_not_loopback(self) -> None:
        rig = self.make('any')
        rig.spawn_fake(FAKE_BIND='any')
        status = rig.run()
        self.refusal(rig, status, 'port-owner')
        self.assertEqual(rig.rows('http'), [], 'к чужому адресу не обращаемся')

    def test_foreign_port_owner(self) -> None:
        rig = self.make('foreign')
        rig.spawn_fake(FAKE_MODE='foreign')
        status = rig.run()
        self.refusal(rig, status, 'port-owner')
        self.assertEqual(status['code'], 'port-owner', 'порт слушает не лаунчер: его процесс не закрываем')
        self.assertEqual(rig.rows('http'), [])
        self.assertTrue(alive(rig.launcher_pid()))

    def test_port_already_busy(self) -> None:
        rig = self.make('busy')
        with socket.socket() as busy:
            busy.bind(('127.0.0.1', rig.port))
            busy.listen(1)
            status = rig.run()
        self.assertEqual((status['result'], status['code']), ('fallback', 'port-busy'))
        rig.wait_starts(1)
        self.assertEqual(len(rig.rows('start')), 1, 'оригинал запущен')

    def test_evaluate_exception_closes_inspector(self) -> None:
        rig = self.make('evalerr')
        status = rig.run(env=rig.env(FAKE_MODE='evalerror'))
        self.assertEqual((status['result'], status['code']), ('fallback', 'install-failed'))
        rows = rig.evaluations()
        self.assertEqual(len(rows), 3, rows)
        self.assertIn('getBuiltinModule("inspector").close()', rows[2]['expr'])
        first = rig.launcher_pid()
        rig.wait_starts(2)
        self.assertEqual(len(rig.rows('start')), 2, 'порт был открыт в нашем процессе: процесс закрыт, оригинал запущен заново')
        self.assertFalse(alive(first))

    def test_port_not_closed_owned_launcher_is_never_killed(self) -> None:
        rig = self.make('noclose')
        status = rig.run(env=rig.env(FAKE_MODE='noclose', FAKE_IGNORECLOSE='1'))
        self.assertEqual((status['result'], status['code']), ('fallback', 'port-open-left;restart-skipped'))
        self.assertEqual(len(rig.evaluations()), 2, 'выражение подключения успело уйти')
        self.assertTrue(alive(rig.launcher_pid()), 'Kill нет: лаунчер мог начать автообновление; просьба закрыться передана')
        rig.wait_starts(2, seconds=2)
        self.assertEqual(len(rig.rows('start')), 1, 'второй экземпляр поверх живого не запускаем')

    def test_port_not_closed_owned_launcher_closes_politely(self) -> None:
        rig = self.make('noclose3')
        status = rig.run(env=rig.env(FAKE_MODE='noclose'))
        self.assertEqual((status['result'], status['code']), ('fallback', 'port-open-closed'))
        rig.wait_starts(2)
        self.assertEqual(len(rig.rows('start')), 2, 'закрылся по CloseMainWindow: оригинал запущен заново')

    def test_port_not_closed_running_launcher_is_left(self) -> None:
        rig = self.make('noclose2')
        rig.spawn_fake(FAKE_MODE='noclose')
        status = rig.run()
        self.assertEqual((status['result'], status['code']), ('fallback', 'port-open-left'))
        self.assertTrue(alive(rig.launcher_pid()), 'чужой (уже работавший) лаунчер не убиваем')

    # ---------- сверка профиля и условия запуска ----------

    def no_inspector(self, rig: Rig) -> None:
        self.assertEqual(rig.rows('http'), [])
        self.assertEqual(rig.evaluations(), [])

    def test_profile_mismatch_starts_original(self) -> None:
        rig = self.make('mismatch')
        rig.asar.write_bytes(b'changed')
        status = rig.run()
        self.assertEqual((status['result'], status['code']), ('fallback', 'hash-asar'))
        rig.wait_starts(1)
        self.assertEqual(len(rig.rows('start')), 1, 'оригинал запущен без подключения')
        self.no_inspector(rig)
        self.assertTrue(alive(rig.launcher_pid()))

    def test_flag_off_starts_original_silently(self) -> None:
        rig = self.make('noflag')
        status = rig.run('--test-flag=0')
        self.assertEqual(status, {}, 'без флага статус не пишется')
        rig.wait_starts(1)
        self.assertEqual(len(rig.rows('start')), 1)
        self.no_inspector(rig)

    def test_game_running_skips_attachment(self) -> None:
        rig = self.make('game')
        game = BIN / (rig.game + '.exe')
        shutil.copy2(BIN / 'fake.exe', game)
        proc = subprocess.Popen([str(game)], env=rig.env(FAKE_ROLE='idle'))
        self.addCleanup(proc.kill)
        time.sleep(1)
        status = rig.run()
        self.assertEqual((status['result'], status['code']), ('skipped', 'game-running'))
        rig.wait_starts(2)
        self.assertEqual(len(rig.rows('start')), 2)  # игра (idle) и запущенный оригинал
        self.no_inspector(rig)

    def assert_not_started(self, rig: Rig, status: dict, code: str) -> None:
        self.assertEqual((status.get('result'), status.get('code')), ('fallback', code), status)
        time.sleep(1.5)
        self.assertEqual(rig.rows('start'), [], 'повышенный процесс ничего не запускает')
        self.no_inspector(rig)

    def test_writable_directory_refused_and_nothing_started(self) -> None:
        rig = self.make('acl', lock=False)
        self.assert_not_started(rig, rig.run(), 'acl')   # цель в доверенной папке, но launcher\ открыт для записи: нужны оба условия

    def test_writable_directory_flag_off_nothing_started(self) -> None:
        rig = self.make('acl0', lock=False)
        self.assert_not_started(rig, rig.run('--test-flag=0'), 'acl')

    def test_writable_parent_refused(self) -> None:
        rig = self.make('aclparent')
        rig.unlock_parent()
        self.assert_not_started(rig, rig.run(), 'acl')

    def test_writable_file_in_launcher_dir_refused(self) -> None:
        rig = self.make('aclfile', lock=False)
        rig.lock()
        subprocess.run(['icacls', str(rig.launcher_dir / 'launcher-ru.json'), '/grant', '*S-1-5-32-545:(W)', '/C', '/Q'], check=True, capture_output=True)
        self.assert_not_started(rig, rig.run(), 'acl')

    def test_target_outside_program_files_refused(self) -> None:
        rig = self.make('outside')
        self.assert_not_started(rig, rig.run(f'--test-trust-root={rig.root / "nowhere"}'), 'untrusted-target')

    def test_target_outside_program_files_flag_off(self) -> None:
        rig = self.make('outside0')
        self.assert_not_started(rig, rig.run('--test-flag=0', f'--test-trust-root={rig.root / "nowhere"}'), 'untrusted-target')

    def test_junction_in_target_path_refused(self) -> None:
        rig = self.make('junction')
        rig.junction_launcher_tree()
        self.assertTrue(rig.exe.exists(), 'через junction файл виден')
        self.assert_not_started(rig, rig.run(), 'untrusted-target')

    def test_untrusted_acl_and_root_not_started(self) -> None:
        rig = self.make('acl2', lock=False)
        self.assert_not_started(rig, rig.run(f'--test-trust-root={rig.root / "nowhere"}'), 'acl')

    # ---------- раскладка как в установщике ----------

    def test_installed_layout_activator_next_to_target_txt(self) -> None:
        """Раскладку задаёт WXQRussian.iss: активатор в той же папке, что target.txt и все файлы лаунчера, и находит target.txt."""
        iss = (INSTALLER / 'WXQRussian.iss').read_text('utf-8-sig')
        dests = iss_dest_dirs()
        self.assertEqual(set(dests), set(__import__('build_installer').LAUNCHER_FILES) | {'WXQLauncherRU.exe'}, dests)
        self.assertEqual(len(set(dests.values())), 1, 'активатор и все файлы лаунчера кладутся в одну папку: ' + str(dests))
        for shortcut in re.findall(r'Filename: "([^"]*WXQLauncherRU\.exe)"', iss):
            self.assertEqual(shortcut, dests['WXQLauncherRU.exe'] + '\\WXQLauncherRU.exe', 'ярлык указывает на активатор')
        rig = self.make('layout')
        self.assertEqual(rig.activator.parent, rig.launcher_dir)
        self.assertEqual(rig.target_txt.parent, rig.activator.parent, 'target.txt рядом с активатором')
        self.assertTrue(rig.target_txt.is_file())
        self.assertEqual(sorted(p.name for p in rig.launcher_dir.iterdir() if p.is_file()),
                         sorted(['WXQLauncherRU.exe', 'target.txt', 'launcher-ru-runtime.cjs', 'launcher-ru.json', 'profiles.json', 'launcher-expected.txt']))
        status = rig.run()
        self.check_attached(rig, status)

    def test_activator_exe_outside_target_dir_gives_no_target(self) -> None:
        """Контрольный: exe не рядом с target.txt (как было в ошибочной раскладке) -> no-target, без запуска."""
        rig = self.make('misplaced', lock=False)
        misplaced = rig.launcher_dir.parent / 'WXQLauncherRU.exe'
        shutil.copy2(rig.activator, misplaced)
        rig.activator.unlink()
        rig.activator = misplaced
        status = rig.run()
        self.assertEqual((status.get('result'), status.get('code')), ('fallback', 'no-target'), status)
        self.assertEqual(rig.rows('start'), [])

    # ---------- runtime читает только установленные файлы ----------

    def test_runtime_reads_only_installed_files(self) -> None:
        """По коду настоящего runtime: все чтения файлов — файлы из LAUNCHER_FILES (их ставит установщик), require только встроенные модули."""
        import build_installer as bi
        self.assertTrue(LAUNCHER_BUILD.is_dir(), LAUNCHER_BUILD)
        text = (LAUNCHER_BUILD / 'launcher-ru-runtime.cjs').read_text('utf-8')
        code = '\n'.join(l for l in text.splitlines() if not l.lstrip().startswith('//'))
        reads = set(re.findall(r"\bread\('([^']+)'\)", text))
        for const in re.findall(r"\bread\((\w+)\)", text):
            reads.update(re.findall(rf"const {const} = '([^']+)'", text))
        self.assertTrue({'launcher-ru.json', 'launcher-entry-public-ru.draft.json', 'launcher-layout.css', 'main-logo-title.a73b14e6.png'} <= reads, reads)
        # единственные обращения к файловой системе: read() из payloadRoot, чтение самого модуля, запись необязательного статуса
        self.assertEqual(set(re.findall(r'\bfs\.(\w+)\(', text)), {'readFileSync', 'mkdirSync', 'writeFileSync'})
        self.assertEqual(text.count('fs.readFileSync('), 2)
        self.assertIn('fs.readFileSync(path.join(payloadRoot, name))', text)
        self.assertIn('fs.readFileSync(__filename)', text)
        self.assertEqual({m for m in re.findall(r"require\('([^']+)'\)", code)}, {'node:fs', 'node:path', 'node:crypto', 'electron'})
        self.assertNotIn('child_process', text)
        self.assertIn('payloadRoot || __dirname', text, 'каталог файлов по умолчанию — папка самого модуля')
        missing = sorted(reads - set(bi.LAUNCHER_FILES))
        self.assertEqual(missing, [], 'runtime читает файлы, которые установщик не ставит')
        manifest = json.loads((LAUNCHER_BUILD / 'manifest.json').read_text('utf-8-sig'))
        for name in reads:
            self.assertIn(name, manifest['files'])
            self.assertEqual(sha(LAUNCHER_BUILD / name), manifest['files'][name])

    def test_unsigned_launcher_refused(self) -> None:
        rig = self.make('unsigned')
        status = rig.run(signature=True)
        self.assertEqual((status['result'], status['code']), ('fallback', 'signature'))
        rig.wait_starts(1)
        self.assertEqual(len(rig.rows('start')), 1)
        self.no_inspector(rig)

    def test_thumbprint_mismatch(self) -> None:
        if not NODE.exists():
            self.skipTest('нет node.exe')
        rig = self.make('thumb', launcher_src=NODE, thumbprint='1' * 40)
        status = rig.run(signature=True)
        self.assertEqual((status['result'], status['code']), ('fallback', 'thumbprint'))

    def test_module_hash_mismatch(self) -> None:
        rig = self.make('module', lock=False)
        (rig.launcher_dir / 'launcher-ru-runtime.cjs').write_text('exports.install=function(){};', encoding='utf-8')
        rig.lock()
        status = rig.run()
        self.assertEqual((status['result'], status['code']), ('fallback', 'module-pin'))
        self.no_inspector(rig)

    # ---------- состав сборок ----------

    def test_release_build_has_no_test_hooks(self) -> None:
        data = (BIN / 'release.exe').read_bytes()
        test = (BIN / 'test.exe').read_bytes()
        for option in ('--test-port', '--test-status-dir', '--test-signal-activation', '--test-no-signature', '--test-trust-root'):
            self.assertIn(option.encode('utf-16le'), test, option)
            self.assertNotIn(option.encode('utf-16le'), data, option)
            self.assertNotIn(option.encode('ascii'), data, option)
        self.assertNotIn(b'TESTBUILD', data)
        self.assertNotIn(b'WXQFakeInspect', data)
        self.assertIn(b'requireAdministrator', data)
        self.assertIn('Egoist\\WXQ-Russian'.encode('utf-16le'), data)


if __name__ == '__main__':
    unittest.main(verbosity=2)
