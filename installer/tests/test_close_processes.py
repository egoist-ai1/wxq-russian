"""Закрытие игры, лаунчера и зависших процессов (Closer в WXQTool.cs, команда close-probe тестовой сборки).

Только заглушки: настоящие процессы не затрагиваются (WXQ_RU_TEST_CLOSE_ROOT ограничивает поиск папкой теста).
Запуск: python test_close_processes.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from close_stubs import CSC, build_stubs  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = '') -> None:
    print(('  ok   ' if ok else '  FAIL ') + name + ('' if ok else '  :: ' + detail), flush=True)
    if not ok:
        failures.append(name)


def alive(pid: int) -> bool:
    out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH', '/FO', 'CSV'], capture_output=True).stdout.decode('cp866', errors='replace')
    return f'"{pid}"' in out


def wait_dead(pid: int, seconds: float = 5) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if not alive(pid):
            return True
        time.sleep(0.2)
    return not alive(pid)


class Lab:
    def __init__(self, work: Path) -> None:
        self.work = work
        folder = work / 'tool'
        folder.mkdir()
        source = (PROJECT / 'installer/WXQTool.cs').read_text('utf-8-sig')
        (folder / 'WXQTool.cs').write_text(source, 'utf-8-sig')
        done = subprocess.run([str(CSC), '/nologo', '/target:exe', '/platform:x64', '/optimize+', '/codepage:65001', '/define:TESTBUILD',
                               '/r:System.Web.Extensions.dll', f'/out:{folder / "WXQTool.exe"}', str(folder / 'WXQTool.cs')], capture_output=True)
        if done.returncode:
            raise SystemExit('помощник не собрался: ' + (done.stdout + done.stderr).decode('cp866', errors='replace'))
        self.tool = folder / 'WXQTool.exe'
        self.console, self.window = build_stubs(work / 'stubs')
        self.count = 0
        self.procs: list[subprocess.Popen] = []

    def box(self) -> dict:
        self.count += 1
        root = self.work / f'box{self.count}'
        game = root / 'game'
        data = game / 'data'      # каталог данных внутри папки игры: безымянные процессы оттуда (значок) не закрываются
        for d in (game, data, root / 'foreign'):
            d.mkdir(parents=True)
        return {'root': root, 'game': game, 'data': data, 'foreign': root / 'foreign'}

    def start(self, box: dict, exe: Path, where: Path, name: str, *args: str, env: dict | None = None) -> subprocess.Popen:
        target = where / name
        shutil.copyfile(exe, target)
        full = dict(os.environ)
        full.update(env or {})
        proc = subprocess.Popen([str(target), *args], env=full)
        self.procs.append(proc)
        return proc

    def probe(self, box: dict, mode: str | None, env: dict | None = None, timeout: int = 90,
              game: str | None = None, launcher: str | None = None) -> tuple[int, str]:
        out = box['root'] / 'probe-out.txt'
        out.unlink(missing_ok=True)
        args = [str(self.tool), 'close-probe', '--data', str(box['data']), '--game', game or str(box['game']), '--out', str(out)]
        if launcher is not None:
            args += ['--launcher', launcher]
        if mode:
            args += ['--close', mode]
        full = dict(os.environ, WXQ_RU_TEST_CLOSE_ROOT=str(box['root']))
        full.update(env or {})
        done = subprocess.run(args, capture_output=True, timeout=timeout, env=full)
        log = box['data'] / 'install.log'
        box['roots'] = ''
        if out.exists():
            for line in out.read_text('utf-8-sig', errors='replace').splitlines():
                if line.startswith('roots='):
                    box['roots'] = line[6:]
        return done.returncode, log.read_text('utf-8-sig', errors='replace') if log.exists() else ''

    def cleanup(self) -> None:
        for proc in self.procs:
            try:
                proc.kill()
            except OSError:
                pass


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix='wxq-close-'))
    lab = Lab(work)
    try:
        # C1. all: безоконная заглушка с именем из списка закрыта, в журнале PID, имя, путь, способ.
        box = lab.box()
        p = lab.start(box, lab.console, box['foreign'], 'WXQLauncherRU.exe')
        time.sleep(0.8)
        code, log = lab.probe(box, 'all')
        check('C1 имя из списка вне папок: процесс закрыт', code == 0 and wait_dead(p.pid), f'code={code}\n{log}')
        check('C1a журнал: имя, PID, путь, способ', 'WXQLauncherRU.exe, PID %d' % p.pid in log and str(box['foreign']) in log and 'принудительно' in log, log)

        # C2. all: безымянная заглушка в папке игры закрыта; безымянная вне папок цела; значок русификатора (каталог данных) цел.
        box = lab.box()
        inside = lab.start(box, lab.console, box['game'], 'other.exe')
        outside = lab.start(box, lab.console, box['foreign'], 'other2.exe')
        tray = lab.start(box, lab.console, box['data'], 'tray.exe')
        time.sleep(0.8)
        code, log = lab.probe(box, 'all')
        check('C2 процесс из папки игры без имени из списка закрыт', code == 0 and wait_dead(inside.pid), log)
        check('C2a чужой процесс вне папок цел', outside.poll() is None)
        check('C2b процесс из каталога данных (значок) цел', tray.poll() is None)

        # C3. none: ничего не закрывается.
        box = lab.box()
        p = lab.start(box, lab.console, box['foreign'], 'WXQ.exe')
        time.sleep(0.8)
        code, log = lab.probe(box, None)
        check('C3 без --close процесс цел, помощник не падает', code == 0 and p.poll() is None, log)

        # C4. имя с иероглифами.
        box = lab.box()
        p = lab.start(box, lab.console, box['foreign'], '王者万象棋.exe')
        time.sleep(0.8)
        code, log = lab.probe(box, 'all')
        check('C4 «王者万象棋.exe» без родителя закрыт', code == 0 and wait_dead(p.pid), log)

        # C5. stale (фоновая задача) ничего не закрывает: ни окно, ни безоконный остаток. Применяет обновление только кнопка «Обновить сейчас».
        box = lab.box()
        windowless = lab.start(box, lab.console, box['foreign'], 'WXQ.exe')
        windowed = lab.start(box, lab.window, box['foreign'], 'WXQLauncher.exe')
        time.sleep(1.5)
        code, log = lab.probe(box, 'stale')
        check('C5 stale: ничего не закрыто', code == 0 and windowed.poll() is None and windowless.poll() is None, f'code={code}\n{log}')

        # C6. all: окно закрывается через CloseMainWindow.
        box = lab.box()
        w = lab.start(box, lab.window, box['foreign'], 'WXQLauncher.exe')
        time.sleep(1.5)
        code, log = lab.probe(box, 'all')
        check('C6 окно закрыто обычным способом', code == 0 and wait_dead(w.pid) and 'CloseMainWindow' in log, f'code={code}\n{log}')

        # C7. all: окно не закрывается само, помощник доходит до принудительного завершения.
        box = lab.box()
        w = lab.start(box, lab.window, box['foreign'], 'HonorofKingsACE.exe', 'ignore')
        time.sleep(1.5)
        code, log = lab.probe(box, 'all')
        check('C7 окно игнорирует закрытие: завершено принудительно', code == 0 and wait_dead(w.pid) and 'принудительно' in log, f'code={code}\n{log}')

        # C8. дерево процессов: дочерний процесс (вне списка) закрыт вместе с родителем.
        box = lab.box()
        pidfile = box['root'] / 'child.pid'
        kid = box['foreign'] / 'kid.exe'
        shutil.copyfile(lab.console, kid)
        parent = lab.start(box, lab.console, box['foreign'], 'WXQ.exe', 'tree', env={'WXQ_STUB_CHILD': str(kid), 'WXQ_STUB_PIDFILE': str(pidfile)})
        end = time.time() + 5
        while not pidfile.exists() and time.time() < end:
            time.sleep(0.2)
        child_pid = int(pidfile.read_text()) if pidfile.exists() else 0
        code, log = lab.probe(box, 'all')
        check('C8 родитель закрыт, потомок вне папок игры и лаунчера остался жив', code == 0 and wait_dead(parent.pid) and child_pid > 0 and alive(child_pid), f'child={child_pid}\n{log}')
        check('C8a журнал: потомок оставлен с пояснением', 'оставлен дочерний процесс' in log, log)
        if child_pid:
            subprocess.run(['taskkill', '/F', '/PID', str(child_pid)], capture_output=True)

        # C8b. потомок из папки игры закрывается (родитель с именем из списка лежит вне папок).
        box = lab.box()
        pidfile = box['root'] / 'child.pid'
        kid = box['game'] / 'kid.exe'
        shutil.copyfile(lab.console, kid)
        parent = lab.start(box, lab.console, box['foreign'], 'WXQ.exe', 'tree', env={'WXQ_STUB_CHILD': str(kid), 'WXQ_STUB_PIDFILE': str(pidfile)})
        end = time.time() + 5
        while not pidfile.exists() and time.time() < end:
            time.sleep(0.2)
        child_pid = int(pidfile.read_text()) if pidfile.exists() else 0
        code, log = lab.probe(box, 'all')
        check('C8b потомок из папки игры закрыт вместе с родителем', code == 0 and wait_dead(parent.pid) and child_pid > 0 and wait_dead(child_pid), f'child={child_pid}\n{log}')

        # C9. неубиваемый (в тесте убийство отключено): ошибка running с именем и PID, в журнале «не закрылись».
        box = lab.box()
        p = lab.start(box, lab.console, box['foreign'], 'WXQLauncher.exe')
        time.sleep(0.8)
        code, log = lab.probe(box, 'all', env={'WXQ_RU_TEST_NOKILL': '1'})
        check('C9 не закрылся: код 2, running, имя и PID в сообщении', code == 2 and 'running' in log and 'WXQLauncher.exe (PID %d)' % p.pid in log, f'code={code}\n{log}')
        check('C9a процесс остался жив', p.poll() is None)

        # C10. чужой процесс с другим именем не затронут ни в одном режиме.
        box = lab.box()
        stranger = lab.start(box, lab.console, box['foreign'], 'notepad-like.exe')
        time.sleep(0.8)
        lab.probe(box, 'all')
        lab.probe(box, 'stale')
        check('C10 чужое имя вне списка цело', stranger.poll() is None)

        # C11. «Коробка»: root\neighbor, root\WXQLauncher (с exe лаунчера), root\WXQLauncher\game. Корень лаунчера принят.
        box = lab.box()
        lbox = box['root'] / 'WXQLauncher'
        game = lbox / 'WXQ(1)'
        neighbor = box['root'] / 'neighbor'
        for d in (game, game / 'data', neighbor):
            d.mkdir(parents=True, exist_ok=True)
        box['data'] = game / 'data'
        shutil.copyfile(lab.console, lbox / 'WXQLauncher.exe')
        in_launcher = lab.start(box, lab.console, lbox, 'helper.exe')
        in_game = lab.start(box, lab.console, game, 'other.exe')
        beside = lab.start(box, lab.console, neighbor, 'other.exe')
        time.sleep(0.8)
        code, log = lab.probe(box, 'all', game=str(game), launcher=str(lbox))
        roots = [r.lower().rstrip('\\') for r in box['roots'].split('|') if r]
        check('C11 корень лаунчера и корень игры приняты', str(lbox).lower() in roots and str(game).lower() in roots, str(roots))
        check('C11a процессы лаунчера и игры закрыты', code == 0 and wait_dead(in_launcher.pid) and wait_dead(in_game.pid), f'code={code}\n{log}')
        check('C11b сосед в соседней папке жив', beside.poll() is None, log)

        # C12. Родитель игры - не лаунчер (имя не WXQLauncher*): корнем он не становится, сосед и «WXQLauncherX» вне корней выживают.
        box = lab.box()
        games = box['root'] / 'Games'
        game = games / 'WXQ(1)'
        for d in (game, game / 'data'):
            d.mkdir(parents=True, exist_ok=True)
        box['data'] = game / 'data'
        in_game = lab.start(box, lab.console, game, 'other.exe')
        beside = lab.start(box, lab.console, games, 'other.exe')
        lookalike = lab.start(box, lab.console, box['foreign'], 'WXQLauncherX.exe')
        time.sleep(0.8)
        code, log = lab.probe(box, 'all', game=str(game))
        roots = [r.lower().rstrip('\\') for r in box['roots'].split('|') if r]
        check('C12 корень = только папка игры', roots == [str(game).lower()], str(roots))
        check('C12a процесс в папке игры закрыт', code == 0 and wait_dead(in_game.pid), f'code={code}\n{log}')
        check('C12b процесс в родительской папке игры цел', beside.poll() is None, log)
        check('C12c похожее имя вне корней цело', lookalike.poll() is None, log)

        # C13. Запрещённые корни: диск, Windows, Program Files, ProgramData, Users не бывают корнем ни как игра, ни как лаунчер.
        box = lab.box()
        for game, launcher in (('C:\\Windows', 'C:\\'), ('C:\\', 'C:\\WXQLauncher'), ('C:\\Program Files', 'C:\\Program Files'),
                               ('C:\\ProgramData', None), ('C:\\Users', None), (os.environ.get('USERPROFILE', '%USERPROFILE%'), None)):
            code, log = lab.probe(box, 'all', game=game, launcher=launcher)
            roots = [r for r in box['roots'].split('|') if r]
            check(f'C13 корень не принят: игра={game} лаунчер={launcher}', code == 0 and roots == [], f'{roots}\n{log}')
    finally:
        lab.cleanup()
        shutil.rmtree(work, ignore_errors=True)
    print('Провалено: %d' % len(failures) if failures else 'Все проверки пройдены')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
