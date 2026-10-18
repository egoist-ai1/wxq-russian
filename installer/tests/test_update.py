"""Неоконный тест автообновления (WXQTool.exe update) против локального HTTP-сервера. Сети и GitHub нет.

    python -X utf8 -B installer/tests/test_update.py [--keep]

Всё во временной папке: каталог данных, «игра» (пустые папки), поддельный Setup.exe (маленькая программа, которая пишет свои аргументы в журнал
и меняет version в state.json). Ключи подписи тестовые, генерируются на лету и в репозиторий не попадают; помощник читает их из WXQ_RU_UPDATE_KEYS
(только TESTBUILD). Настоящие игра, лаунчер и установленный русификатор не читаются и не меняются.
Если настоящая WXQ.exe запущена, помощник собирается без остановки по запущенной игре, а проверка «игра запущена» пропускается.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))
import publish_release as pr  # noqa: E402
sys.path.insert(0, str(HERE))
from close_stubs import build_stubs  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

CSC = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')
failures: list[str] = []
SETUP_PAD = 700 * 1024   # размер «установщика»: достаточно, чтобы оборвать загрузку посередине

FAKE_SETUP = r'''
using System; using System.IO; using System.Threading; using System.Text; using System.Text.RegularExpressions;
class P {
  // Версия, зашитая в хвост файла (publish() дописывает "\0<версия>\0..."): так сохранённая копия знает, какая она.
  static string OwnVersion() {
    byte[] b = File.ReadAllBytes(System.Reflection.Assembly.GetEntryAssembly().Location);
    int end = b.Length; while (end > 0 && b[end - 1] == 0) end--;
    int start = end; while (start > 0 && b[start - 1] != 0) start--;
    return Encoding.ASCII.GetString(b, start, end - start);
  }
  static int Main(string[] a) {
  if (a.Length > 0 && a[0] == "sleep") { Thread.Sleep(120000); return 0; }
  string log = Environment.GetEnvironmentVariable("WXQ_FAKE_LOG");
  if (log != null) File.AppendAllText(log, string.Join(" ", a) + "\n");
  string st = Environment.GetEnvironmentVariable("WXQ_FAKE_STATE"), ver = Environment.GetEnvironmentVariable("WXQ_FAKE_VERSION");
  string own = Environment.GetEnvironmentVariable("WXQ_FAKE_OWN") == "1" ? OwnVersion() : null;
  if (own != null) ver = own;
  string stop = Environment.GetEnvironmentVariable("WXQ_FAKE_STOP"), stopLog = Environment.GetEnvironmentVariable("WXQ_FAKE_STOPLOG");
  if (stop != null && stopLog != null) File.AppendAllText(stopLog, "остановка [" + stop + "]: тест\n", new UTF8Encoding(true));
  string t = File.ReadAllText(st);
  if (Environment.GetEnvironmentVariable("WXQ_FAKE_FAIL") == "1") return 5;
  if (own != null && own == Environment.GetEnvironmentVariable("WXQ_FAKE_BAD")) {
    // Новая версия не встала, а откат снял перевод: state.json больше не «установлен».
    File.WriteAllText(st, Regex.Replace(t, "\"installed\"\\s*:\\s*true", "\"installed\": false")); return 5; }
  t = Regex.Replace(t, "\"version\"\\s*:\\s*\"[^\"]*\"", "\"version\": \"" + ver + "\"");
  t = Regex.Replace(t, "\"installed\"\\s*:\\s*false", "\"installed\": true");
  string stamp = Guid.NewGuid().ToString("N");
  if (Regex.IsMatch(t, "\"installed_utc\"")) t = Regex.Replace(t, "\"installed_utc\"\\s*:\\s*\"[^\"]*\"", "\"installed_utc\": \"" + stamp + "\"");
  else t = Regex.Replace(t, "^\\s*\\{", "{\"installed_utc\": \"" + stamp + "\",");
  File.WriteAllText(st, t); return 0; } }
'''


def check(name: str, condition: bool, detail: str = '') -> bool:
    print(('  ok   ' if condition else '  FAIL ') + name + ('' if condition else f'  [{detail}]'), flush=True)
    if not condition:
        failures.append(name)
    return bool(condition)


def game_is_running() -> bool:
    # Имена процессов в UTF-8 (у игры есть китайское имя), те же, что ищет помощник в RequireClosed.
    done = subprocess.run(['powershell', '-NoProfile', '-Command',
                           '[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Process | ForEach-Object { $_.ProcessName }'], capture_output=True)
    names = done.stdout.decode('utf-8', errors='replace').split()
    return any(re.fullmatch(r'(WXQ|WXQLauncher.*|HonorofKingsACE|王者万象棋)', name) for name in names)


def compile_exe(source: Path, out: Path, *extra: str) -> None:
    done = subprocess.run([str(CSC), '/nologo', '/target:exe', '/platform:x64', '/optimize+', '/codepage:65001', *extra, f'/out:{out}', str(source)], capture_output=True)
    if done.returncode:
        raise SystemExit('не собралось: ' + (done.stdout + done.stderr).decode('cp866', errors='replace'))


def build_tool(work: Path) -> tuple[Path, bool]:
    """Возвращает (WXQTool.exe, process_check_disabled)."""
    folder = work / 'tool'
    folder.mkdir()
    source = (PROJECT / 'installer/WXQTool.cs').read_text('utf-8-sig')
    disabled = game_is_running()
    needle = 'if (active.Count > 0)'
    assert source.count(needle) == 1
    if disabled:
        print('       WXQ.exe запущен: помощник собран без остановки по запущенной игре (только для песочницы)', flush=True)
        source = source.replace(needle, 'if (false && active.Count > 0)')
    (folder / 'WXQTool.cs').write_text(source, 'utf-8-sig')
    compile_exe(folder / 'WXQTool.cs', folder / 'WXQTool.exe', '/define:TESTBUILD', '/r:System.Web.Extensions.dll')
    return folder / 'WXQTool.exe', disabled


class Server:
    """Минимальный «GitHub»: update.json (+ETag), update.json.sig, файлы с Range, обрывы и коды ответов по требованию."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.log: list[tuple[str, dict]] = []
        self.override: dict[str, int] = {}     # путь -> код ответа
        self.cut_after: int | None = None      # оборвать выдачу setup после стольких байт
        self.cut_times = 0
        self.bad_sig_times = 0                 # столько первых ответов на update.json.sig отдать испорченными
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):  # noqa: D401
                pass

            def do_GET(self):
                path = self.path.split('?')[0]
                headers = {k.lower(): v for k, v in self.headers.items()}
                outer.log.append((path, headers))
                if path in outer.override:
                    self.send_response(outer.override[path]); self.send_header('Content-Length', '0'); self.end_headers(); return
                body = outer.files.get(path)
                if path == '/update.json.sig' and outer.bad_sig_times > 0 and body is not None:
                    outer.bad_sig_times -= 1
                    body = base64.b64encode(b'x' * 384)   # подпись «не оттуда»: CDN отдал обрывок
                if body is None:
                    self.send_response(404); self.send_header('Content-Length', '0'); self.end_headers(); return
                etag = '"' + hashlib.sha1(body).hexdigest() + '"'
                if path == '/update.json' and headers.get('if-none-match') == etag:
                    self.send_response(304); self.send_header('ETag', etag); self.end_headers(); return
                start = 0
                match = re.match(r'bytes=(\d+)-', headers.get('range', ''))
                if match and path.endswith('.exe'):
                    start = int(match.group(1))
                if start >= len(body) and start:
                    self.send_response(416); self.send_header('Content-Length', '0'); self.end_headers(); return
                chunk = body[start:]
                if start:
                    self.send_response(206)
                    self.send_header('Content-Range', f'bytes {start}-{len(body) - 1}/{len(body)}')
                else:
                    self.send_response(200)
                self.send_header('Content-Length', str(len(chunk)))
                self.send_header('ETag', etag)
                self.end_headers()
                if path.endswith('.exe') and outer.cut_after is not None and outer.cut_times > 0:
                    outer.cut_times -= 1
                    self.wfile.write(chunk[:outer.cut_after]); self.wfile.flush()
                    self.close_connection = True
                    self.connection.shutdown(2)
                    return
                self.wfile.write(chunk)

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}/'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def count(self, name: str) -> int:
        return sum(1 for path, _ in self.log if path == '/' + name)

    def stop(self) -> None:
        self.httpd.shutdown()


class Env:
    def __init__(self, work: Path, tool: Path, server: Server, setup_exe: bytes, key, other_key) -> None:
        self.work, self.tool, self.server, self.setup_exe = work, tool, server, setup_exe
        self.key, self.other_key = key, other_key
        self.count = 0
        self.keys_file = work / 'keys.json'
        self.write_keys([key])

    @staticmethod
    def pem(key) -> str:
        return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode('ascii')

    def write_keys(self, keys) -> None:
        rows = [{'key_id': pr.key_id_of(k.public_key()), 'public_key_pem': self.pem(k)} for k in keys]
        self.keys_file.write_text(json.dumps({'base_url': '', 'keys': rows}), 'utf-8')

    def new_box(self, version: str = '1.0.0') -> dict:
        self.count += 1
        root = self.work / f'box{self.count}'
        game = root / 'Launcher' / 'WXQ1'
        game.mkdir(parents=True)
        data = root / 'data'
        data.mkdir()
        state = data / 'state.json'
        state.write_text(json.dumps({'installed': True, 'version': version, 'game_root': str(game)}, indent=1), 'utf-8')
        self.server.files.clear(); self.server.log.clear(); self.server.override.clear()
        self.server.cut_after = None; self.server.cut_times = 0; self.server.bad_sig_times = 0
        return {'root': root, 'game': game, 'data': data, 'state': state, 'log': root / 'setup-args.txt'}

    def publish(self, version: str, issued: str = '2026-02-01T00:00:00Z', sign_key=None, tamper_setup: bool = False, min_updater: int = 1, game_versions=None) -> bytes:
        """Кладёт на сервер update.json/.sig и установщик; возвращает байты установщика, обещанные манифестом."""
        exe = self.setup_exe + b'\0' + version.encode('ascii') + b'\0' * 8
        folder = self.work / ('pub-' + version)
        folder.mkdir(exist_ok=True)
        (folder / 'Setup.exe').write_bytes(exe)
        manifest = pr.build_manifest(version, folder / 'Setup.exe', None, [], pr.key_id_of(self.key.public_key()), ['https://example.invalid/'],
                                     issued_utc=issued, min_updater=min_updater, notes='тест', game_versions=game_versions)
        manifest['base_urls'] = [self.server.url]
        data = pr.manifest_bytes(manifest)
        signature = pr.sig_to_text(pr.sign_bytes(sign_key or self.key, data)).encode('ascii')
        self.server.files['/update.json'] = data
        self.server.files['/update.json.sig'] = signature
        self.server.files['/Setup.exe'] = (exe[:-1] + bytes([exe[-1] ^ 1])) if tamper_setup else exe
        return exe

    def run(self, box: dict, *flags: str, base: str | None = None, env: dict | None = None, timeout: int = 120) -> tuple[dict, dict]:
        out = box['root'] / 'out.txt'
        out.unlink(missing_ok=True)
        args = [str(self.tool), 'update', *flags, '--data', str(box['data']), '--out', str(out), '--test-base-url', base or self.server.url]
        full = dict(os.environ, WXQ_RU_UPDATE_KEYS=str(self.keys_file), WXQ_RU_TEST_RETRY_MS='50', WXQ_FAKE_LOG=str(box['log']),
                    WXQ_RU_TEST_CLOSE_ROOT=str(box['root']), WXQ_FAKE_STATE=str(box['state']), WXQ_FAKE_VERSION=getattr(self, 'fake_version', '9.9.9'), WXQ_RU_USERS_ROOT=str(box['root'] / 'no-users'))
        full.update(env or {})
        done = subprocess.run(args, capture_output=True, timeout=timeout, env=full)
        rows = {'_exit': str(done.returncode)}
        if out.is_file():
            for line in out.read_text('utf-8-sig').splitlines():
                name, _, value = line.partition('=')
                rows[name] = value
        status_file = box['data'] / 'update-status.json'
        status = json.loads(status_file.read_text('utf-8-sig')) if status_file.is_file() else {}
        if os.environ.get('WXQ_TEST_DEBUG') and status.get('code') == 'game_running':
            log = box['data'] / 'install.log'
            print('DEBUGLOG', log.read_text('utf-8-sig')[-1500:] if log.is_file() else 'no log', flush=True)
        return rows, status

    @staticmethod
    def version_of(box: dict) -> str:
        return json.loads(box['state'].read_text('utf-8-sig'))['version']

    @staticmethod
    def state_of(box: dict) -> dict:
        return json.loads(box['state'].read_text('utf-8-sig'))

    @staticmethod
    def meta_of(box: dict) -> dict:
        path = box['data'] / 'update-meta.json'
        return json.loads(path.read_text('utf-8-sig')) if path.is_file() else {}

    @staticmethod
    def add_receipt(box: dict, status: str = 'applied', ours: bool = True, game_version: str = '1.6.0.1') -> None:
        """Receipt прошлой установки + файлы в кэше игры. ours=False - игра обновилась, файл в кэше уже не наш."""
        cache = box['game'] / 'QtsDataFolder' / 'QtsVFSCache' / 'packages' / '101'
        cache.mkdir(parents=True, exist_ok=True)
        (cache / '101.db').write_bytes(b'db-' + game_version.encode())
        (cache / '101_0.db').write_bytes(b'storage')
        (cache / '101.info').write_text(json.dumps({'version': game_version}), 'utf-8')
        mine = hashlib.sha256((cache / '101.db').read_bytes()).hexdigest()
        folder = box['data'] / 'backup' / '20261018-000000'
        folder.mkdir(parents=True, exist_ok=True)
        receipt = {'status': status, 'game_root': str(box['game']), 'created_utc': '2026-10-18T00:00:00Z',
                   'operations': [{'relative': 'packages/101/101.db', 'before_sha256': 'ab' * 32, 'candidate_sha256': mine if ours else 'cd' * 32}]}
        (folder / 'receipt.json').write_text(json.dumps(receipt), 'utf-8')

    def save_installed(self, box: dict, version: str) -> Path:
        """Сохранённая копия установщика текущей версии (updates\\installed-<sha16>.exe) и её SHA-256 в update-meta.json."""
        exe = self.setup_exe + b'\0' + version.encode('ascii') + b'\0' * 8
        sha = hashlib.sha256(exe).hexdigest()
        updates = box['data'] / 'updates'
        updates.mkdir(exist_ok=True)
        path = updates / ('installed-' + sha[:16] + '.exe')
        path.write_bytes(exe)
        meta = self.meta_of(box)
        meta.update({'installed_setup_sha': sha, 'installed_setup_bytes': len(exe), 'installed_setup_version': version})
        (box['data'] / 'update-meta.json').write_text(json.dumps(meta), 'utf-8')
        return path

    @staticmethod
    def setup_lines(box: dict) -> list[str]:
        return box['log'].read_text('utf-8').splitlines() if box['log'].exists() else []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--keep', action='store_true')
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix='wxq-update-test-'))
    server = Server()
    try:
        tool, process_check_off = build_tool(work)
        compile_src = work / 'FakeSetup.cs'
        compile_src.write_text(FAKE_SETUP, 'utf-8')
        compile_exe(compile_src, work / 'FakeSetup.exe')
        base_exe = (work / 'FakeSetup.exe').read_bytes() + b'\0' * SETUP_PAD
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        other = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        env = Env(work, tool, server, base_exe, key, other)
        env.fake_version = '1.1.0'

        # U1. Нет связи: порт без сервера. Код network, перевод не тронут, статус failed.
        box = env.new_box()
        rows, status = env.run(box, base='http://127.0.0.1:9/')
        check('U1 нет связи: code=network, exit 2, статус failed', rows.get('code') == 'network' and rows['_exit'] == '2' and status.get('state') == 'failed' and status.get('code') == 'network', str(rows))
        check('U1a нет связи: версия не изменилась, next_try_utc выставлен', env.version_of(box) == '1.0.0' and status.get('next_try_utc'), str(status))

        # U2. Нет релизов (404) — не ошибка.
        box = env.new_box()
        rows, status = env.run(box)
        check('U2 404 update.json: idle/no_release, exit 0', rows['_exit'] == '0' and status.get('state') == 'idle' and status.get('code') == 'no_release', str(rows) + str(status))

        # U3. Без ключей — not_configured.
        box = env.new_box()
        env.publish('1.1.0')
        env.write_keys([])
        rows, status = env.run(box)
        env.write_keys([key])
        check('U3 ключей нет: code=not_configured, ничего не скачано', rows.get('code') == 'not_configured' and server.count('update.json') == 0, str(rows))

        # U4. Проверка без установки: ready, setup не скачан; второй раз — 304 по ETag без повторной загрузки подписи.
        box = env.new_box()
        env.publish('1.1.0')
        rows, status = env.run(box, '--check-only')
        check('U4 --check-only: state=ready, available=1.1.0, exit 0', rows['_exit'] == '0' and status.get('state') == 'ready' and status.get('available') == '1.1.0', str(rows) + str(status))
        setups = list((box['data'] / 'updates').glob('*.exe')) if (box['data'] / 'updates').exists() else []
        check('U4a --check-only: установщик скачан в кэш и записан в статус (путь, размер, SHA-256), но не запущен; версия прежняя',
              server.count('Setup.exe') >= 1 and len(setups) == 1 and status.get('setup_path', '').lower() == str(setups[0]).lower()
              and status.get('setup_sha') == hashlib.sha256(setups[0].read_bytes()).hexdigest() and int(status.get('setup_bytes', 0)) == setups[0].stat().st_size
              and not box['log'].exists() and env.version_of(box) == '1.0.0', str(status))
        downloads = server.count('Setup.exe')
        sig_before = server.count('update.json.sig')
        rows, status = env.run(box, '--check-only')
        conditional = [h for p, h in server.log if p == '/update.json' and 'if-none-match' in h]
        check('U4b повторная проверка: условный запрос If-None-Match, без повторной загрузки подписи', len(conditional) == 1 and server.count('update.json.sig') == sig_before and status.get('state') == 'ready' and server.count('Setup.exe') == downloads, str(rows) + str(status))

        # U4c. Задача планировщика без триггеров и с проверкой вместо установки (статически по исходнику: на живой системе задачу не регистрируем).
        tool_src = (PROJECT / 'installer/WXQTool.cs').read_text('utf-8-sig')
        register = tool_src[tool_src.index('static void TaskRegister'):tool_src.index('static void TaskRemove')]
        check('U4c задача: нет Triggers.Create и PT6H, действие "update --check-only"', 'Triggers.Create' not in register and 'PT6H' not in register and 'action.Arguments = "update --check-only"' in register)

        # U5. Неверная подпись (чужой ключ): отказ, установщик не запрашивается и не запускается.
        box = env.new_box()
        env.publish('1.1.0', sign_key=other)
        rows, status = env.run(box)
        check('U5 чужая подпись: code=bad_signature, Setup.exe не запрашивался и не запускался',
              rows.get('code') == 'bad_signature' and server.count('Setup.exe') == 0 and not box['log'].exists() and env.version_of(box) == '1.0.0', str(rows))

        # U5a. Манифест изменён после подписи — то же.
        box = env.new_box()
        env.publish('1.1.0')
        server.files['/update.json'] = server.files['/update.json'].replace(b'1.1.0', b'9.9.9')
        rows, status = env.run(box)
        check('U5a манифест подменён после подписи: bad_signature', rows.get('code') == 'bad_signature' and not box['log'].exists(), str(rows))

        # U6. Подменён установщик (тот же размер, другой SHA-256): отказ, файл удалён, не запущен.
        box = env.new_box()
        env.publish('1.1.0', tamper_setup=True)
        rows, status = env.run(box)
        leftovers = list((box['data'] / 'updates').glob('*.exe')) if (box['data'] / 'updates').exists() else []
        check('U6 установщик с чужим SHA-256: code=sha_mismatch, не запущен, .exe не оставлен',
              rows.get('code') == 'sha_mismatch' and not box['log'].exists() and not leftovers and env.version_of(box) == '1.0.0', str(rows) + str(leftovers))

        # U7. Версия ниже и равная.
        box = env.new_box('1.0.0')
        env.publish('0.9.0')
        rows, status = env.run(box)
        check('U7 версия ниже установленной: code=downgrade', rows.get('code') == 'downgrade' and not box['log'].exists(), str(rows))
        env.publish('1.0.0', issued='2026-02-02T00:00:00Z')
        rows, status = env.run(box)
        check('U7a версия равна установленной: idle/up_to_date, exit 0', rows['_exit'] == '0' and status.get('code') == 'up_to_date' and server.count('Setup.exe') == 0, str(rows) + str(status))

        # U8. issued_utc старее уже принятого: отказ (откат на старый подписанный манифест).
        box = env.new_box('1.0.0')
        env.publish('1.1.0', issued='2026-03-01T00:00:00Z')
        rows, status = env.run(box, '--check-only')
        env.publish('1.2.0', issued='2026-01-01T00:00:00Z')
        rows, status = env.run(box, '--check-only')
        check('U8 issued_utc старее принятого: code=replay', rows.get('code') == 'replay' and not box['log'].exists(), str(rows))

        # U8a. Манифест требует более нового помощника.
        box = env.new_box()
        env.publish('1.1.0', min_updater=2)
        rows, status = env.run(box)
        check('U8a min_updater выше помощника: code=updater_too_old', rows.get('code') == 'updater_too_old' and not box['log'].exists(), str(rows))

        # U9. Игра запущена (процесс из папки лаунчера): deferred, ничего не скачано и не запущено.
        if process_check_off:
            print('  skip U9 (настоящая игра запущена, проверка процессов в песочнице отключена)', flush=True)
        else:
            box = env.new_box()
            env.publish('1.1.0')
            # 0.6.1: фоновое обновление не убивает процесс с видимым окном (идёт игра), поэтому заглушка оконная.
            _console_stub, window_stub = build_stubs(work / 'close-stubs')
            sleeper = box['game'] / 'sleep.exe'
            shutil.copyfile(window_stub, sleeper)
            proc = subprocess.Popen([str(sleeper)])
            try:
                time.sleep(1.5)
                rows, status = env.run(box)
            finally:
                proc.kill(); proc.wait()
            check('U9 игра запущена: state=deferred/game_running, Setup.exe не запускался, версия прежняя',
                  status.get('state') == 'deferred' and status.get('code') == 'game_running' and not box['log'].exists() and env.version_of(box) == '1.0.0', str(rows) + str(status))
            rows, status = env.run(box)
            check('U9a игра закрыта: обновление применено (докачка не нужна, файл уже проверен)', status.get('state') == 'done' and env.version_of(box) == '1.1.0', str(rows) + str(status))

        # U10. Обрыв загрузки посередине: в той же попытке докачка Range, итог проверен по SHA-256.
        box = env.new_box()
        exe = env.publish('1.1.0')
        server.cut_after, server.cut_times = SETUP_PAD // 2, 1
        rows, status = env.run(box)
        ranges = [h['range'] for p, h in server.log if p == '/Setup.exe' and 'range' in h]
        check('U10 обрыв посередине: докачка через Range, обновление применено', status.get('state') == 'done' and len(ranges) == 1 and ranges[0] != 'bytes=0-' and env.version_of(box) == '1.1.0', str(rows) + str(ranges))

        # U10a. Частичный файл от прошлого запуска докачивается с нужного места.
        box = env.new_box()
        exe = env.publish('1.1.0')
        updates = box['data'] / 'updates'
        updates.mkdir()
        part = updates / 'WXQ-Russian-Setup-1.1.0.part'
        part.write_bytes(exe[:SETUP_PAD // 3])
        rows, status = env.run(box)
        ranges = [h['range'] for p, h in server.log if p == '/Setup.exe' and 'range' in h]
        check('U10a частичный .part: запрошен Range с места остановки, итог done', status.get('state') == 'done' and ranges == [f'bytes={SETUP_PAD // 3}-'], str(rows) + str(ranges))

        # U11. Полное применение в песочнице: аргументы Setup, версия в state.json, статус done, лишние файлы убраны.
        box = env.new_box()
        env.publish('1.1.0')
        rows, status = env.run(box)
        logged = box['log'].read_text('utf-8') if box['log'].exists() else ''
        check('U11 применение: status=ok, state=done, installed=1.1.0, state.json обновлён',
              rows['_exit'] == '0' and status.get('state') == 'done' and status.get('installed') == '1.1.0' and env.version_of(box) == '1.1.0', str(rows) + str(status))
        check('U11a аргументы Setup: /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /GAMEDIR=<game_root> /LOG=updates\\setup.log /LAUNCHERRU',
              all(x in logged for x in ('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/GAMEDIR=' + str(box['game']), 'setup.log', '/LAUNCHERRU=')), logged)

        # U12. Повторный запуск: качает только update.json (304), установщик не запрашивает.
        server_setup = server.count('Setup.exe')
        server.log.clear()
        rows, status = env.run(box)
        check('U12 повтор: idle/up_to_date, Setup.exe не запрашивался', status.get('code') == 'up_to_date' and server.count('Setup.exe') == 0 and server_setup == 1, str(rows) + str(status))
        check('U12a повтор: update.json запрошен условно (ETag)', any('if-none-match' in h for p, h in server.log if p == '/update.json'), str(server.log))

        # U13. Установщик завершился, а версия не поменялась: setup_failed, а затем без повторной загрузки до срока.
        box = env.new_box()
        env.publish('1.1.0')
        rows, status = env.run(box, env={'WXQ_FAKE_FAIL': '1'})
        check('U13 Setup вернул ошибку: code=setup_failed, версия прежняя', rows.get('code') == 'setup_failed' and env.version_of(box) == '1.0.0', str(rows))
        server.log.clear()
        rows, status = env.run(box)
        check('U13a повтор до срока: retry_later, Setup.exe не запрашивался и не запускался', status.get('code') == 'retry_later' and server.count('Setup.exe') == 0, str(rows) + str(status))

        # Параллельный запуск (busy по мьютексу обновления) отдельным сценарием не проверяется.

        # U15. Посторонний мусор в updates\ убирается, не мешает.
        box = env.new_box()
        env.publish('1.1.0')
        updates = box['data'] / 'updates'
        updates.mkdir()
        (updates / 'junk.exe').write_bytes(b'junk')
        rows, status = env.run(box)
        check('U15 мусор в updates\\ убран, обновление применено', status.get('state') == 'done' and not (updates / 'junk.exe').exists(), str(rows))

        own = {'WXQ_FAKE_OWN': '1'}
        ready = {'WXQ_RU_TEST_CACHE_READY': '1'}
        not_ready = {'WXQ_RU_TEST_CACHE_READY': '0'}

        # U16. Обычное обновление идёт с /UPDATE=1; после успеха остаётся одна копия installed-<sha16>.exe, её SHA-256 в update-meta.json.
        box = env.new_box()
        env.publish('1.1.0')
        rows, status = env.run(box)
        lines = env.setup_lines(box)
        kept = list((box['data'] / 'updates').glob('installed-*.exe'))
        meta = env.meta_of(box)
        check('U16 /UPDATE=1 в аргументах Setup', status.get('state') == 'done' and len(lines) == 1 and '/UPDATE=1' in lines[0], str(lines))
        check('U16a одна копия installed-<sha16>.exe, SHA-256 в update-meta.json',
              len(kept) == 1 and len(list((box['data'] / 'updates').glob('*.exe'))) == 1 and meta.get('installed_setup_sha', '')[:16] in kept[0].name
              and meta.get('installed_setup_version') == '1.1.0', str(kept) + str(meta))

        # U17. Игра обновилась, кэш ещё не пересоздан: ничего не качаем и не ставим, статус game_changed с понятным текстом.
        box = env.new_box()
        env.add_receipt(box, ours=False, game_version='1.7.0.0')
        env.publish('1.1.0')
        rows, status = env.run(box, env=not_ready)
        check('U17 кэш игры не пересоздан: deferred/game_changed, сети нет, Setup не запускался',
              status.get('state') == 'deferred' and status.get('code') == 'game_changed' and server.count('update.json') == 0 and not box['log'].exists()
              and 'Запустите её один раз' in status.get('message', '') and status.get('game_version') == '1.7.0.0', str(rows) + str(status))

        # U18. Кэш готов, новой версии нет: сохранённый установщик ставит перевод заново (с /UPDATE=1), с сервера Setup не качается.
        box = env.new_box()
        env.add_receipt(box, ours=False)
        env.save_installed(box, '1.0.0')
        env.publish('1.0.0')
        before = env.state_of(box).get('installed_utc')
        rows, status = env.run(box, env={**ready, **own})
        lines = env.setup_lines(box)
        check('U18 кэш готов, новой версии нет: автопересадка (reapplied), /UPDATE=1, Setup с сервера не качался',
              status.get('state') == 'done' and status.get('code') == 'reapplied' and len(lines) == 1 and '/UPDATE=1' in lines[0]
              and server.count('Setup.exe') == 0 and env.state_of(box).get('installed_utc') != before, str(rows) + str(status) + str(lines))

        # U18a. То же без связи: хватает сохранённого установщика.
        box = env.new_box()
        env.add_receipt(box, ours=False)
        env.save_installed(box, '1.0.0')
        rows, status = env.run(box, base='http://127.0.0.1:9/', env={**ready, **own})
        check('U18a нет связи, кэш готов: пересадка из сохранённого установщика', status.get('state') == 'done' and status.get('code') == 'reapplied', str(rows) + str(status))

        # U30. Кнопка «Обновить сейчас» поставила версию (state.json = версия манифеста): следующая проверка сохраняет проверенный установщик как копию
        # текущей версии, и пересадка после обновления игры идёт именно из неё, без сети. Кэш называется понятно (WXQ-Russian-Setup-<версия>.exe).
        box = env.new_box()
        env.publish('1.1.0')
        rows, status = env.run(box, '--check-only')
        named = box['data'] / 'updates' / 'WXQ-Russian-Setup-1.1.0.exe'
        check('U30 кэш установщика называется WXQ-Russian-Setup-<версия>.exe, путь в статусе тот же', named.is_file() and status.get('setup_path', '').lower() == str(named).lower(), str(status))
        box['state'].write_text(json.dumps({'installed': True, 'version': '1.1.0', 'game_root': str(box['game'])}, indent=1), 'utf-8')
        rows, status = env.run(box, '--check-only')
        kept = list((box['data'] / 'updates').glob('installed-*.exe'))
        meta = env.meta_of(box)
        check('U30a после установки проверка сохранила копию текущей версии, статус idle без готового установщика',
              len(kept) == 1 and meta.get('installed_setup_version') == '1.1.0' and not named.exists() and status.get('state') == 'idle' and not status.get('setup_path'), str(status) + str(meta))
        env.add_receipt(box, ours=False)
        rows, status = env.run(box, base='http://127.0.0.1:9/', env={**ready, **own})
        lines = env.setup_lines(box)
        check('U30b пересадка без сети берёт сохранённую копию 1.1.0', status.get('state') == 'done' and status.get('code') == 'reapplied' and len(lines) == 1 and env.version_of(box) == '1.1.0', str(rows) + str(status) + str(lines))

        # U31. Сохранённая копия другой версии (старая 1.0.0 при установленной 1.1.0) не берётся: старую версию поверх новой не ставим.
        box = env.new_box('1.1.0')
        env.add_receipt(box, ours=False)
        env.save_installed(box, '1.0.0')
        rows, status = env.run(box, base='http://127.0.0.1:9/', env={**ready, **own})
        check('U31 копия не той версии: пересадка не запущена, Setup не запускался', not box['log'].exists() and status.get('state') != 'done' and env.version_of(box) == '1.1.0', str(rows) + str(status))

        # U32. Сбой сети при проверке не убирает готовый установщик из статуса (кнопка «Обновить сейчас» остаётся).
        box = env.new_box()
        env.publish('1.1.0')
        rows, status = env.run(box, '--check-only')
        first = status.get('setup_path')
        rows, status = env.run(box, '--check-only', base='http://127.0.0.1:9/')
        check('U32 после сбоя сети готовый установщик сохранён в статусе', bool(first) and status.get('state') == 'failed' and status.get('setup_path') == first and status.get('setup_sha'), str(status))

        # U19. Кэш готов и вышла новая версия: обычное обновление (скачивается новый установщик).
        box = env.new_box()
        env.add_receipt(box, ours=False)
        env.save_installed(box, '1.0.0')
        env.publish('1.1.0')
        rows, status = env.run(box, env=ready)
        check('U19 кэш готов, есть новая версия: обновление до 1.1.0', status.get('state') == 'done' and status.get('installed') == '1.1.0' and server.count('Setup.exe') == 1 and env.version_of(box) == '1.1.0', str(rows) + str(status))

        # U20. Убитый Setup: receipt в состоянии applying - перевод возвращается сохранённым установщиком.
        box = env.new_box()
        env.add_receipt(box, status='applying')
        env.save_installed(box, '1.0.0')
        env.publish('1.0.0')
        rows, status = env.run(box, env=own)
        lines = env.setup_lines(box)
        check('U20 прерванная установка (applying): повторная установка с /UPDATE=1', status.get('code') == 'reapplied' and len(lines) == 1 and '/UPDATE=1' in lines[0], str(rows) + str(status) + str(lines))

        # U21. Новая версия падает: откат снял перевод, возвращается сохранённая версия.
        box = env.new_box()
        env.publish('1.1.0')
        rows, status = env.run(box, env=own)
        env.publish('1.2.0')
        rows, status = env.run(box, env={**own, 'WXQ_FAKE_BAD': '1.2.0'})
        lines = env.setup_lines(box)
        check('U21 новая версия не встала: code=rolled_back, возвращена 1.1.0', rows.get('code') == 'rolled_back' and env.version_of(box) == '1.1.0' and env.state_of(box).get('installed') is True
              and len(lines) == 3 and '/UPDATE=1' in lines[2], str(rows) + str(lines))
        check('U21a сообщение называет возвращённую версию', '1.1.0' in rows.get('message', '') and status.get('installed') == '1.1.0', str(rows) + str(status))

        # U22. Антивирус: файл исчез после проверки - av_blocked с понятным текстом, версия прежняя.
        box = env.new_box()
        env.publish('1.1.0')
        rows, status = env.run(box, env={'WXQ_RU_TEST_QUARANTINE': '1'})
        check('U22 карантин антивируса: code=av_blocked, текст про карантин, версия прежняя',
              rows.get('code') == 'av_blocked' and 'карантин' in rows.get('message', '') and env.version_of(box) == '1.0.0' and not box['log'].exists(), str(rows))

        # U23. Постоянная и временная ошибки: срок повтора 24 ч и 3 ч.
        def hours_left(box_: dict) -> float:
            text = env.meta_of(box_).get('failed_until_utc', '')
            import datetime as dt
            until = dt.datetime.strptime(text, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc)
            return (until - dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600

        box = env.new_box()
        env.publish('1.1.0')
        rows, status = env.run(box, env={'WXQ_FAKE_FAIL': '1'})
        temp_hours = hours_left(box)
        check('U23 временная ошибка: повтор через ~3 ч', 2.5 < temp_hours < 3.5, str(temp_hours))
        # U24. Часы ушли на 30 часов назад (или вперёд): запись «не раньше чем» дальше 25 часов от нынешнего времени считается истёкшей.
        rows, status = env.run(box, env={'WXQ_RU_TEST_CLOCK_HOURS': '-30'})
        check('U24 часы назад: запись о сбое не держит обновление', status.get('state') == 'done' and env.version_of(box) == '1.1.0', str(rows) + str(status))
        box = env.new_box()
        env.publish('1.1.0')
        rows, status = env.run(box, env={'WXQ_FAKE_FAIL': '1'})
        rows, status = env.run(box, env={'WXQ_RU_TEST_CLOCK_HOURS': '5'})
        check('U24a часы вперёд: срок вышел, повторная попытка идёт', status.get('state') == 'done', str(rows) + str(status))
        box = env.new_box()
        env.publish('1.1.0')
        rows, status = env.run(box, env={'WXQ_FAKE_FAIL': '1', 'WXQ_FAKE_STOP': 'version', 'WXQ_FAKE_STOPLOG': str(box['data'] / 'install.log')})
        perm_hours = hours_left(box)
        check('U23a постоянная ошибка (version): повтор через ~24 ч', 23 < perm_hours < 25, str(perm_hours) + str(rows))

        # U25. Свежий ответ с плохой подписью: один повтор. Первая подпись испорчена - вторая проходит; обе испорчены - bad_signature после двух запросов.
        box = env.new_box()
        env.publish('1.1.0')
        server.bad_sig_times = 1
        rows, status = env.run(box)
        check('U25 плохая подпись один раз: повтор помог, обновление применено', status.get('state') == 'done' and server.count('update.json.sig') == 2, str(rows) + str(status))
        box = env.new_box()
        env.publish('1.1.0')
        server.bad_sig_times = 5
        rows, status = env.run(box)
        check('U25a плохая подпись дважды: bad_signature, ровно два запроса подписи', rows.get('code') == 'bad_signature' and server.count('update.json.sig') == 2 and not box['log'].exists(), str(rows))

        # U26. Версии с -rcN: 0.6.0-rc2 < 0.6.0.
        box = env.new_box('0.6.0-rc2')
        env.publish('0.6.0')
        rows, status = env.run(box, env={'WXQ_FAKE_VERSION': '0.6.0'})
        check('U26 0.6.0-rc2 -> 0.6.0: обновление применено', status.get('state') == 'done' and env.version_of(box) == '0.6.0', str(rows) + str(status))
        box = env.new_box('0.6.0')
        env.publish('0.5.9')
        rows, status = env.run(box)
        check('U26a 0.6.0 -> 0.5.9: downgrade', rows.get('code') == 'downgrade', str(rows))

        # U27. Новая версия собрана для более новой игры, чем стоит: deferred/game_old, текущая версия работает.
        box = env.new_box()
        env.add_receipt(box, ours=True, game_version='1.5.0.1')
        env.publish('1.1.0', game_versions=['1.6.0.1'])
        rows, status = env.run(box)
        check('U27 игра старее, чем нужна версии: deferred/game_old, Setup не запускался', status.get('state') == 'deferred' and status.get('code') == 'game_old' and not box['log'].exists(), str(rows) + str(status))

        # U28. Кэш цел (наш перевод на месте), новой версии нет: ничего не делается.
        box = env.new_box()
        env.add_receipt(box, ours=True)
        env.publish('1.0.0')
        rows, status = env.run(box)
        check('U28 перевод цел: up_to_date, Setup не запускался', status.get('code') == 'up_to_date' and not box['log'].exists(), str(rows) + str(status))
    finally:
        server.stop()
        if args.keep:
            print('временная папка оставлена: ' + str(work))
        else:
            shutil.rmtree(work, ignore_errors=True)
    print()
    print('FAIL: ' + ', '.join(failures) if failures else 'все проверки пройдены')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
