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
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

CSC = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')
failures: list[str] = []
SETUP_PAD = 700 * 1024   # размер «установщика»: достаточно, чтобы оборвать загрузку посередине

FAKE_SETUP = r'''
using System; using System.IO; using System.Threading; using System.Text.RegularExpressions;
class P { static int Main(string[] a) {
  if (a.Length > 0 && a[0] == "sleep") { Thread.Sleep(120000); return 0; }
  string log = Environment.GetEnvironmentVariable("WXQ_FAKE_LOG");
  if (log != null) File.AppendAllText(log, string.Join(" ", a) + "\n");
  if (Environment.GetEnvironmentVariable("WXQ_FAKE_FAIL") == "1") return 5;
  string st = Environment.GetEnvironmentVariable("WXQ_FAKE_STATE"), ver = Environment.GetEnvironmentVariable("WXQ_FAKE_VERSION");
  string t = File.ReadAllText(st);
  t = Regex.Replace(t, "\"version\"\\s*:\\s*\"[^\"]*\"", "\"version\": \"" + ver + "\"");
  File.WriteAllText(st, t); return 0; } }
'''


def check(name: str, condition: bool, detail: str = '') -> bool:
    print(('  ok   ' if condition else '  FAIL ') + name + ('' if condition else f'  [{detail}]'), flush=True)
    if not condition:
        failures.append(name)
    return bool(condition)


def game_is_running() -> bool:
    done = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True)
    return bool(re.search(rb'"(WXQ|HonorofKingsACE|WXQLauncher[^"]*)\.exe"', done.stdout, re.IGNORECASE))


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
        self.server.cut_after = None; self.server.cut_times = 0
        return {'root': root, 'game': game, 'data': data, 'state': state, 'log': root / 'setup-args.txt'}

    def publish(self, version: str, issued: str = '2026-02-01T00:00:00Z', sign_key=None, tamper_setup: bool = False, min_updater: int = 1) -> bytes:
        """Кладёт на сервер update.json/.sig и установщик; возвращает байты установщика, обещанные манифестом."""
        exe = self.setup_exe + b'\0' + version.encode('ascii') + b'\0' * 8
        folder = self.work / ('pub-' + version)
        folder.mkdir(exist_ok=True)
        (folder / 'Setup.exe').write_bytes(exe)
        manifest = pr.build_manifest(version, folder / 'Setup.exe', None, [], pr.key_id_of(self.key.public_key()), ['https://example.invalid/'],
                                     issued_utc=issued, min_updater=min_updater, notes='тест')
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
                    WXQ_FAKE_STATE=str(box['state']), WXQ_FAKE_VERSION=getattr(self, 'fake_version', '9.9.9'), WXQ_RU_USERS_ROOT=str(box['root'] / 'no-users'))
        full.update(env or {})
        done = subprocess.run(args, capture_output=True, timeout=timeout, env=full)
        rows = {'_exit': str(done.returncode)}
        if out.is_file():
            for line in out.read_text('utf-8-sig').splitlines():
                name, _, value = line.partition('=')
                rows[name] = value
        status_file = box['data'] / 'update-status.json'
        status = json.loads(status_file.read_text('utf-8-sig')) if status_file.is_file() else {}
        return rows, status

    @staticmethod
    def version_of(box: dict) -> str:
        return json.loads(box['state'].read_text('utf-8-sig'))['version']


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
        check('U4a --check-only: Setup.exe не запрашивался, версия прежняя', server.count('Setup.exe') == 0 and env.version_of(box) == '1.0.0')
        sig_before = server.count('update.json.sig')
        rows, status = env.run(box, '--check-only')
        conditional = [h for p, h in server.log if p == '/update.json' and 'if-none-match' in h]
        check('U4b повторная проверка: условный запрос If-None-Match, без повторной загрузки подписи', len(conditional) == 1 and server.count('update.json.sig') == sig_before and status.get('state') == 'ready', str(rows) + str(status))

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
            sleeper = box['game'] / 'sleep.exe'
            shutil.copyfile(work / 'FakeSetup.exe', sleeper)
            proc = subprocess.Popen([str(sleeper), 'sleep'])
            try:
                time.sleep(0.5)
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
        part = updates / (hashlib.sha256(exe).hexdigest()[:16] + '.part')
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
