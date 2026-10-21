"""Изолированная проверка поиска лаунчера по профилям и подсказкам пути.

    python -X utf8 -B installer/tests/test_launcher_discovery.py

Штатный csc компилирует актуальный WXQTool.cs с TESTBUILD и отдельной точкой
входа reflection-стенда. Стенд вызывает только FindLauncherExe: production Main,
мьютекс, поиск/закрытие процессов, реестр, ярлыки и сканирование дисков не нужны.
WXQ_RU_USERS_ROOT изолирует системные источники. Все профили, «exe», игра и
junction создаются в tempfile; фиктивный лаунчер никогда не запускается.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[2]
CSC = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
BUILD = '26099.0.1.30004'
EXE = '王者万象棋.exe'
RELATIVE_EXE = BUILD + '\\' + EXE
failures: list[str] = []

HARNESS = r'''
using System;
using System.IO;
using System.Reflection;
using System.Text;
using System.Web.Script.Serialization;

namespace Egoist.WXQRussian
{
    static class LauncherDiscoveryHarness
    {
        static int Main(string[] args)
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            if (args.Length != 3) throw new ArgumentException("game launcher payload");
            // Только Context и нужный private-метод; Program.Main не вызывается.
            var context = new Context { Game = args[0], Launcher = args[1], Payload = args[2] };
            MethodInfo method = typeof(Program).GetMethod("FindLauncherExe", BindingFlags.Static | BindingFlags.NonPublic);
            if (method == null) throw new MissingMethodException("FindLauncherExe");
            method.Invoke(null, new object[] { context });
            Console.WriteLine(new JavaScriptSerializer().Serialize(context.Result));
            return 0;
        }
    }
}
'''


def check(name: str, condition: bool, detail: object = '') -> bool:
    print(('  ok   ' if condition else '  FAIL ') + name + (f' [{detail}]' if not condition and detail else ''), flush=True)
    if not condition:
        failures.append(name)
    return bool(condition)


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def tree(path: Path) -> dict[str, str]:
    return {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(path.rglob('*')) if p.is_file() and not p.is_symlink()}


def build_harness(work: Path) -> Path:
    source = work / 'LauncherDiscoveryHarness.cs'
    source.write_text(HARNESS, 'utf-8-sig')
    output = work / 'LauncherDiscoveryHarness.exe'
    done = subprocess.run([str(CSC), '/nologo', '/target:exe', '/platform:x64', '/optimize+', '/codepage:65001',
                           '/define:TESTBUILD', '/r:System.Web.Extensions.dll', '/r:Microsoft.CSharp.dll',
                           '/main:Egoist.WXQRussian.LauncherDiscoveryHarness', f'/out:{output}',
                           str(PROJECT / 'installer/WXQTool.cs'), str(PROJECT / 'installer/WXQCompatText.cs'),
                           str(PROJECT / 'installer/WXQCompatPack.cs'), str(source)], capture_output=True, timeout=120)
    if done.returncode:
        raise RuntimeError('csc не собрал reflection-стенд: ' + (done.stdout + done.stderr).decode('cp866', errors='replace'))
    return output


class Sandbox:
    def __init__(self, root: Path, rel: str = RELATIVE_EXE):
        self.root = root
        self.game = root / 'Игра отдельно' / 'WXQ с пробелами'
        self.launcher = self.game.parent
        self.payload = root / 'payload'
        self.game.mkdir(parents=True)
        write(self.game / 'WXQ.exe', b'NOT EXECUTABLE: synthetic game marker')
        self.profile(rel)

    def profile(self, rel: str) -> None:
        profile = {'schema': 1, 'builds': [{'build': BUILD, 'exe_rel': rel}]}
        write(self.payload / 'launcher-profiles.json', json.dumps(profile, ensure_ascii=False).encode('utf-8'))

    def fake_launcher(self, root: Path, build: str = BUILD) -> Path:
        return write(root / build / EXE, b'NOT EXECUTABLE: synthetic launcher marker')

    def run(self, harness: Path, hints: str = '') -> dict[str, str]:
        before = tree(self.root)
        env = dict(os.environ, WXQ_RU_USERS_ROOT=str(self.root / 'no-users'),
                   WXQ_RU_TEST_HINTS=hints, WXQ_RU_TRUST_ROOT=str(self.root / 'unrelated-trust-root'),
                   APPDATA=str(self.root / 'profile'))
        done = subprocess.run([str(harness), str(self.game), str(self.launcher), str(self.payload)],
                              capture_output=True, env=env, timeout=30)
        if done.returncode:
            raise RuntimeError('reflection-стенд: ' + (done.stdout + done.stderr).decode('utf-8-sig', errors='replace'))
        rows = json.loads(done.stdout.decode('utf-8-sig'))
        check(self.root.name + ': поиск не изменяет фикстуры', tree(self.root) == before)
        return rows


def expect(name: str, rows: dict[str, str], reason: str, exe: Path | None = None) -> None:
    check(name + ': точная причина ' + reason, rows.get('launcher_reason') == reason, rows)
    if exe is None:
        check(name + ': цель и сборка не выбраны', rows.get('launcher_exe') == '' and rows.get('launcher_build') == '', rows)
    else:
        actual = rows.get('launcher_exe', '')
        check(name + ': выбран точный exe и профиль',
              os.path.normcase(os.path.abspath(actual)) == os.path.normcase(str(exe.absolute()))
              and rows.get('launcher_build') == BUILD, rows)


def junction(link: Path, target: Path) -> None:
    assert link.is_absolute() and target.is_absolute() and not link.exists()
    link.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, WXQ_TEST_JUNCTION_PATH=str(link), WXQ_TEST_JUNCTION_TARGET=str(target))
    script = "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:WXQ_TEST_JUNCTION_PATH -Target $env:WXQ_TEST_JUNCTION_TARGET | Out-Null"
    done = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                          capture_output=True, env=env, timeout=30)
    if done.returncode:
        raise RuntimeError('не создана временная junction: ' + (done.stdout + done.stderr).decode('utf-8', errors='replace'))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='wxq-launcher-discovery-') as temporary:
        work = Path(temporary)
        harness = build_harness(work)

        box = Sandbox(work / 'custom-unicode-spaces')
        box.launcher = box.root / 'Своя установка ACE (другой каталог)'
        box.game = box.launcher / 'WXQ с пробелами'
        box.game.mkdir(parents=True)
        write(box.game / 'WXQ.exe', b'NOT EXECUTABLE: custom game marker')
        exe = box.fake_launcher(box.launcher)
        expect('Произвольный локальный путь с Unicode и пробелами', box.run(harness), 'found', exe)

        box = Sandbox(work / 'separate-install-hint')
        launcher = box.root / 'Лаунчер на другом пути'
        exe = box.fake_launcher(launcher)
        expect('Отдельные каталоги игры и лаунчера: подсказка каталога', box.run(harness, str(launcher)), 'found', exe)

        box = Sandbox(work / 'quoted-exe-hint')
        exe = box.fake_launcher(box.root / 'Лаунчер отдельно (пробелы)')
        expect('Подсказка exe в кавычках с аргументами', box.run(harness, f'"{exe}" --ignored-argument'), 'found', exe)

        box = Sandbox(work / 'ambiguous-installations')
        box.launcher = box.root / 'Лаунчер первый'
        box.fake_launcher(box.launcher)
        second = box.root / 'Лаунчер второй'
        box.fake_launcher(second)
        expect('Две разные установки с подходящими профилями', box.run(harness, str(second)), 'ambiguous')

        for index, relative in enumerate(('../escape/' + EXE, 'build/../../escape/' + EXE)):
            box = Sandbox(work / f'traversal-{index}', relative)
            write(box.root / 'escape' / EXE, b'NOT EXECUTABLE: traversal decoy')
            expect('Выход exe_rel за корень: ' + relative, box.run(harness), 'profile-not-found')

        box = Sandbox(work / 'unknown-build')
        box.fake_launcher(box.launcher, 'unknown-build-999')
        expect('Неизвестная сборка: подходящего exe профиля нет', box.run(harness), 'profile-not-found')

        box = Sandbox(work / 'unsafe-junction')
        actual = box.root / 'Реальная временная цель'
        box.fake_launcher(actual)
        link = box.root / 'Каталог junction'
        junction(link, actual)
        # Одноимённый exe вне подсказок нельзя выбирать вместо небезопасной цели.
        box.fake_launcher(box.root / 'Посторонняя неподсказанная установка')
        try:
            expect('Junction не допускается, посторонняя цель не выбирается', box.run(harness, str(link)), 'unsafe-path')
        finally:
            # Удаляется только junction внутри tempfile, её целевая папка остаётся.
            os.rmdir(link)
        check('Удаление junction не затронуло её временную цель', (actual / BUILD / EXE).is_file())

    print('ИТОГО: ' + ('все проверки пройдены' if not failures else f'не прошли: {len(failures)}'), flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
