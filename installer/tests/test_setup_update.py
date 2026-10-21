"""Обновление из открытого установщика: signed feed, загрузка по кнопке, handoff.

    python -X utf8 -B installer/tests/test_setup_update.py [--keep]

Штатный build_installer.build_tool собирает TESTBUILD без подмены production
guards. HTTP, тестовые ключи, данные, parent.exe и Setup.exe живут в tempfile.
Настоящие игра, лаунчер, Task Scheduler, реестр и интерфейс не используются.
Fake Setup пишет JSON своих аргументов, а parent только ждёт завершения тестом.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / 'installer'))
sys.path.insert(0, str(HERE))
import build_installer as bi  # noqa: E402
from test_update import Env, Server, SETUP_PAD, rsa  # noqa: E402

failures: list[str] = []
FAKE_SETUP = r'''
using System;
using System.IO;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
class P {
    [STAThread]
    static int Main(string[] args) {
        if (args.Length > 0 && args[0] == "parent") {
            Mutex gate = null;
            if (args.Length > 2) gate = new Mutex(true, args[2]);
            File.WriteAllText(args[1], "ready");
            try { Thread.Sleep(120000); }
            finally { if (gate != null) { gate.ReleaseMutex(); gate.Dispose(); } }
            return 0;
        }
        string log = Environment.GetEnvironmentVariable("WXQ_FAKE_LOG");
        if (log == null) return 9;
        File.WriteAllText(log, new JavaScriptSerializer().Serialize(args), new UTF8Encoding(false));
        return 0;
    }
}
'''


def check(name: str, condition: bool, detail: object = '') -> bool:
    print(('  ok   ' if condition else '  FAIL ') + name + (f' [{detail}]' if not condition and detail else ''), flush=True)
    if not condition:
        failures.append(name)
    return bool(condition)


def snapshot(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


def wait_for(condition, timeout: float = 15) -> bool:
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


class SetupEnv(Env):
    def new_box(self, version: str = '1.0.0', *, first_install: bool = False) -> dict:
        box = super().new_box(version)
        if first_install:
            box['state'].unlink()
        canary = box['game'] / 'cache' / 'untouched.db'
        canary.parent.mkdir()
        canary.write_bytes(b'TEST: update navigation must not change game bytes')
        box['snapshot'] = snapshot(box['game'])
        box['state_bytes'] = box['state'].read_bytes() if box['state'].exists() else None
        return box

    def run(self, box: dict, mode: str = 'check', *, current: str = '1.0.0', flags: tuple[str, ...] = (),
            base: str | None = None, extra_env: dict | None = None, timeout: int = 30) -> tuple[dict, dict]:
        out = box['root'] / 'out.txt'
        out.unlink(missing_ok=True)
        args = [str(self.tool), 'setup-update', '--mode', mode, '--current-version', current,
                '--data', str(box['data']), '--out', str(out), '--test-base-url', base or self.server.url, *flags]
        full = dict(os.environ, WXQ_RU_UPDATE_KEYS=str(self.keys_file), WXQ_RU_TEST_RETRY_MS='50',
                    WXQ_FAKE_LOG=str(box['log']), WXQ_RU_TEST_CLOSE_ROOT=str(box['root']),
                    WXQ_RU_USERS_ROOT=str(box['root'] / 'no-users'))
        full.update(extra_env or {})
        # Handoff запускает короткого child, который наследует стандартные handles.
        # PIPE заставил бы communicate ждать его EOF вместо завершения основного CLI.
        output = box['root'] / ('cli-' + mode + '.log')
        with output.open('wb') as log:
            done = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, env=full, timeout=timeout,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        rows = {'_exit': str(done.returncode)}
        if out.is_file():
            for line in out.read_text('utf-8-sig').splitlines():
                name, _, value = line.partition('=')
                rows[name] = value
        status = read_json(box['data'] / 'setup-update-status.json')
        if done.returncode and not status:
            rows['_log'] = output.read_text('utf-8-sig', errors='replace')[-2000:]
        return rows, status

    @staticmethod
    def unchanged(box: dict) -> bool:
        now = box['state'].read_bytes() if box['state'].exists() else None
        return snapshot(box['game']) == box['snapshot'] and now == box['state_bytes']

    @staticmethod
    def status(box: dict) -> dict:
        return read_json(box['data'] / 'setup-update-status.json')

    @staticmethod
    def setup_args(box: dict) -> list[str]:
        try:
            return json.loads(box['log'].read_text('utf-8-sig')) if box['log'].is_file() else []
        except (OSError, ValueError):
            return []


def start_parent(work: Path, executable: Path, name: str, mutex: str | None = None) -> subprocess.Popen:
    ready = work / f'{name}-ready.txt'
    args = [str(executable), 'parent', str(ready)] + ([mutex] if mutex else [])
    parent = subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW)
    if not wait_for(ready.is_file, timeout=5):
        parent.kill()
        parent.wait()
        raise RuntimeError('не запустился изолированный родитель: ' + name)
    return parent


def stop_parent(parent: subprocess.Popen) -> None:
    if parent.poll() is None:
        parent.kill()
    parent.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--keep', action='store_true')
    options = parser.parse_args()
    failures.clear()
    work = Path(tempfile.mkdtemp(prefix='wxq-setup-update-test-'))
    server = Server()
    parents: list[subprocess.Popen] = []
    try:
        staging = work / 'tool'
        staging.mkdir()
        bi.build_tool(staging, test_build=True)
        source = work / 'FakeSetup.cs'
        source.write_text(FAKE_SETUP, 'utf-8')
        fake = work / 'FakeSetup.exe'
        built = subprocess.run([str(bi.CSC), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
                                '/codepage:65001', '/r:System.Web.Extensions.dll', f'/out:{fake}', str(source)], capture_output=True)
        if built.returncode:
            raise RuntimeError((built.stdout + built.stderr).decode('cp866', errors='replace'))
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        other = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        env = SetupEnv(work, staging / 'WXQTool.exe', server, fake.read_bytes() + b'\0' * SETUP_PAD, key, other)

        # Первый запуск не требует ни state.json, ни распознанной игры/prime.
        box = env.new_box(first_install=True)
        env.publish('1.1.0')
        rows, status = env.run(box)
        check('S1 первая установка: available без state.json и prime', rows['_exit'] == '0' and status.get('state') == 'available'
              and status.get('available') == '1.1.0', (rows, status))
        check('S1a check получает только signed feed; EXE не запрошен и не запущен', server.count('update.json') >= 1
              and server.count('update.json.sig') >= 1 and server.count('Setup.exe') == 0 and not box['log'].exists())
        check('S1b два формата статуса и общий --out', (box['data'] / 'setup-update-status.txt').is_file()
              and rows.get('state') == status.get('state') and rows.get('available') == status.get('available'), rows)
        check('S1c check не меняет игру и не создаёт installed state', env.unchanged(box))
        signatures = server.count('update.json.sig')
        rows, status = env.run(box)
        check('S2 повторный check: ETag, без EXE и повторной подписи', status.get('state') == 'available'
              and any('if-none-match' in h for p, h in server.log if p == '/update.json')
              and server.count('update.json.sig') == signatures and server.count('Setup.exe') == 0, (rows, status))

        box = env.new_box('1.0.0')
        env.publish('1.1.0')
        rows, status = env.run(box, current='1.2.0')
        check('S3 запущенный установщик новее feed: current', rows['_exit'] == '0' and status.get('state') == 'current'
              and server.count('Setup.exe') == 0, (rows, status))
        box = env.new_box('1.3.0')
        env.publish('1.2.0')
        rows, status = env.run(box, current='1.0.0')
        check('S3a установленная версия новее запущенного установщика: current', rows['_exit'] == '0'
              and status.get('state') == 'current' and server.count('Setup.exe') == 0, (rows, status))
        box = env.new_box('1.1.0')
        env.publish('1.1.0')
        rows, status = env.run(box, current='1.1.0')
        check('S3b равная версия приложения: current', rows['_exit'] == '0' and status.get('state') == 'current', (rows, status))
        rows, status = env.run(box, 'download', current='1.1.0')
        check('S3c download не понижает и не загружает актуальную версию', rows['_exit'] == '0'
              and status.get('state') == 'current' and server.count('Setup.exe') == 0 and env.unchanged(box), (rows, status))

        # Новый игровой перевод не доказывает обновление EXE старого ярлыка.
        rows, status = env.run(box, current='1.0.0')
        check('S3d старое приложение видит выпуск с уже установленным переводом', rows['_exit'] == '0'
              and status.get('state') == 'available' and env.unchanged(box), (rows, status))
        rows, status = env.run(box, 'download', current='1.0.0')
        check('S3e восстановление старого приложения скачивает ту же версию перевода', rows['_exit'] == '0'
              and status.get('state') == 'ready' and env.unchanged(box), (rows, status))

        box = env.new_box(first_install=True)
        started = time.monotonic()
        rows, status = env.run(box, base='http://127.0.0.1:9/')
        elapsed = time.monotonic() - started
        check('S4 offline: быстрый failed/network без изменения игры', rows.get('code') == 'network'
              and status.get('state') == 'failed' and elapsed < 8 and env.unchanged(box), (rows, status, elapsed))

        box = env.new_box()
        env.publish('1.1.0', sign_key=other)
        rows, status = env.run(box)
        check('S5 чужая подпись: failed/bad_signature, EXE не запрошен', rows.get('code') == 'bad_signature'
              and status.get('state') == 'failed' and server.count('Setup.exe') == 0 and env.unchanged(box), (rows, status))
        box = env.new_box()
        env.publish('1.1.0')
        server.files['/update.json'] = server.files['/update.json'].replace(b'1.1.0', b'8.8.8')
        rows, status = env.run(box)
        check('S5a подмена JSON после подписи: отказ до EXE', rows.get('code') == 'bad_signature'
              and status.get('state') == 'failed' and server.count('Setup.exe') == 0, (rows, status))

        box = env.new_box()
        env.publish('1.1.0', issued='2026-03-01T00:00:00Z')
        env.run(box)
        env.publish('1.2.0', issued='2026-01-01T00:00:00Z')
        rows, status = env.run(box)
        check('S6 откат даты подписанного feed: failed/replay', rows.get('code') == 'replay'
              and status.get('state') == 'failed' and server.count('Setup.exe') == 0, (rows, status))
        box = env.new_box()
        env.publish('1.1.0', min_updater=10000)
        rows, status = env.run(box)
        check('S6a min_updater: failed/updater_too_old', rows.get('code') == 'updater_too_old'
              and status.get('state') == 'failed' and server.count('Setup.exe') == 0, (rows, status))

        # Кнопка загрузки отдельно от проверки и установки.
        box = env.new_box(first_install=True)
        promised = env.publish('1.1.0')
        env.run(box)
        rows, status = env.run(box, 'download')
        path = Path(status.get('setup_path', str(box['root'] / 'missing.exe')))
        check('S7 download: ready и точный signed SHA/размер', rows['_exit'] == '0' and status.get('state') == 'ready'
              and path.is_file() and path.read_bytes() == promised
              and status.get('setup_sha') == hashlib.sha256(promised).hexdigest()
              and int(status.get('setup_bytes', 0)) == len(promised), (rows, status))
        check('S7a download не запускает Setup и не меняет игру/state', not box['log'].exists() and env.unchanged(box))
        downloaded = server.count('Setup.exe')
        rows, status = env.run(box, 'download')
        check('S7b повторная кнопка использует уже проверенный кэш', status.get('state') == 'ready'
              and server.count('Setup.exe') == downloaded and not box['log'].exists(), (rows, status))

        box = env.new_box()
        env.publish('1.1.0')
        server.cut_after, server.cut_times = SETUP_PAD // 2, 1
        rows, status = env.run(box, 'download')
        ranges = [h['range'] for p, h in server.log if p == '/Setup.exe' and 'range' in h]
        check('S8 download после обрыва: Range и ready', status.get('state') == 'ready'
              and any(x != 'bytes=0-' for x in ranges) and not box['log'].exists(), (rows, status, ranges))

        box = env.new_box()
        env.publish('1.1.0', tamper_setup=True)
        rows, status = env.run(box, 'download')
        check('S9 download с другим SHA: failed/sha_mismatch, запуска нет', rows.get('code') == 'sha_mismatch'
              and status.get('state') == 'failed' and not box['log'].exists() and env.unchanged(box), (rows, status))

        box = env.new_box()
        env.publish('1.1.0')
        server.files['/Setup.exe'] += b'extra unsigned bytes'
        rows, status = env.run(box, 'download')
        check('S9a EXE больше подписанного размера: failed, запуска нет', rows['_exit'] != '0'
              and status.get('state') == 'failed' and not box['log'].exists() and env.unchanged(box), (rows, status))

        # Живой parent нужен для ack; Setup не стартует до его выхода.
        box = env.new_box(first_install=True)
        env.publish('1.1.0')
        env.run(box, 'download')
        parent = start_parent(work, fake, 'handoff-parent')
        parents.append(parent)
        rows, status = env.run(box, 'handoff', flags=('--parent', str(parent.pid), '--game', str(box['game']), '--launcher', '1'))
        check('S10 handoff ack возвращается при ещё живом parent', rows['_exit'] == '0'
              and status.get('state') == 'handoff' and parent.poll() is None, (rows, status))
        time.sleep(0.3)
        check('S10a Setup не исполняется до выхода exact parent', not box['log'].exists())
        stop_parent(parent)
        check('S10b после выхода parent Setup запущен', wait_for(lambda: env.setup_args(box)))
        check('S10b1 child сообщил успешный запуск', wait_for(lambda: env.status(box).get('code') == 'launched'), env.status(box))
        args = env.setup_args(box)
        check('S10c видимый Setup: UPDATE/CONTINUE и выбранные игра/лаунчер', '/UPDATE=1' in args and '/CONTINUE=1' in args
              and '/GAMEDIR=' + str(box['game']) in args and '/LAUNCHERRU=1' in args
              and not any(x.upper().startswith(('/VERYSILENT', '/SILENT')) for x in args), args)
        check('S10d handoff не меняет игру и installed state', env.unchanged(box))

        box = env.new_box()
        env.publish('1.1.0')
        _, status = env.run(box, 'download')
        path = Path(status.get('setup_path', str(box['root'] / 'missing.exe')))
        if path.is_file():
            content = path.read_bytes()
            path.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
        parent = start_parent(work, fake, 'tampered-before-parent')
        parents.append(parent)
        rows, status = env.run(box, 'handoff', flags=('--parent', str(parent.pid)))
        check('S11 подмена EXE до handoff: failed/sha_mismatch и запуска нет', rows.get('code') == 'sha_mismatch'
              and rows['_exit'] != '0' and status.get('state') == 'failed' and not box['log'].exists(), (rows, status))
        stop_parent(parent)

        box = env.new_box()
        env.publish('1.1.0')
        _, status = env.run(box, 'download')
        path = Path(status.get('setup_path', str(box['root'] / 'missing.exe')))
        parent = start_parent(work, fake, 'tampered-while-parent')
        parents.append(parent)
        rows, status = env.run(box, 'handoff', flags=('--parent', str(parent.pid)))
        check('S12 handoff для повторной проверки принят', rows['_exit'] == '0'
              and status.get('state') == 'handoff' and not box['log'].exists(), (rows, status))
        if path.is_file():
            content = path.read_bytes()
            path.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
        stop_parent(parent)
        finished = wait_for(lambda: env.status(box).get('state') == 'failed')
        check('S12a подмена EXE во время ожидания: child отказывает после parent exit', finished
              and env.status(box).get('code') == 'sha_mismatch' and not box['log'].exists() and env.unchanged(box), env.status(box))

        box = env.new_box()
        env.publish('1.1.0')
        env.run(box, 'download')
        rows, status = env.run(box, 'handoff')
        check('S13 handoff без parent отвергается', rows['_exit'] != '0' and rows.get('code') == 'parent_changed'
              and not box['log'].exists(), (rows, status))

        box = env.new_box()
        env.publish('1.1.0')
        env.run(box, 'download')
        parent = start_parent(work, fake, 'wrong-start-parent')
        parents.append(parent)
        rows, status = env.run(box, 'launch', flags=('--parent', str(parent.pid), '--parent-start', '0', '--ticket', '1' * 32))
        check('S14 тот же PID с неверным StartTime: failed/parent_changed', rows['_exit'] != '0'
              and rows.get('code') == 'parent_changed' and status.get('state') == 'failed' and not box['log'].exists(), (rows, status))
        stop_parent(parent)

        box = env.new_box()
        env.publish('1.1.0')
        env.run(box, 'download')
        parent = start_parent(work, fake, 'bad-cached-manifest-parent')
        parents.append(parent)
        rows, status = env.run(box, 'handoff', flags=('--parent', str(parent.pid)))
        check('S15 handoff перед подменой cached manifest принят', rows['_exit'] == '0'
              and status.get('state') == 'handoff' and not box['log'].exists(), (rows, status))
        cached = box['data'] / 'update-cache.json'
        cached.write_bytes(cached.read_bytes().replace(b'1.1.0', b'8.8.8'))
        stop_parent(parent)
        finished = wait_for(lambda: env.status(box).get('state') == 'failed')
        check('S15a cached manifest подменён при ожидании: bad_signature, Setup не стартует', finished
              and env.status(box).get('code') == 'bad_signature' and not box['log'].exists() and env.unchanged(box), env.status(box))

        print(f'\nИтог: {len(failures)} ошибок. Песочница: {work}', flush=True)
        return 1 if failures else 0
    finally:
        for parent in parents:
            stop_parent(parent)
        server.stop()
        if options.keep or failures:
            print('Сохранено: ' + str(work), flush=True)
        else:
            shutil.rmtree(work)


if __name__ == '__main__':
    raise SystemExit(main())
