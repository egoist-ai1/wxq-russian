"""Проверка живого прогресса WXQTool.exe (параметр --progress) на фиктивной игре. Реальная игра только читается.

    python -X utf8 -B installer/tests/test_tool_progress.py [--work <папка>] [--keep]

Запускается сам по себе, мастер установщика (.iss) не нужен: собирается только помощник (csc) и берутся фикстуры run_tests.py.
Для каждой команды (check, backup, unpack, verify, restore) параллельный поток читает файл прогресса и проверяет:
файл всегда разбирается целиком (ровно 7 строк в порядке протокола), done не убывает и не больше total,
в конце done == total > 0 и state=done. Затем поток команд повторяется без --progress на такой же игре:
результаты --out и состояние папки игры должны совпасть (кроме метки резервной копии, она из времени).
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import run_tests as rt  # noqa: E402

KEYS = ['phase', 'done', 'total', 'item', 'index', 'count', 'state']
results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = '') -> bool:
    results.append((name, bool(condition), detail))
    print(('  ok   ' if condition else '  FAIL ') + name + (('  [' + detail + ']') if detail and not condition else ''), flush=True)
    return bool(condition)


def parse(raw: bytes) -> dict[str, str] | None:
    """Файл прогресса целиком или None, если он не по протоколу (половина файла, BOM, лишние ключи)."""
    if raw.startswith(b'\xef\xbb\xbf'):
        return None
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return None
    lines = text.splitlines()
    if len(lines) != len(KEYS) or not text.endswith('\n'):
        return None
    row = {}
    for line, name in zip(lines, KEYS):
        key, sep, value = line.partition('=')
        if not sep or key != name:
            return None
        row[key] = value
    for numeric in ('done', 'total', 'index', 'count'):
        if not re.fullmatch(r'\d+', row[numeric]):
            return None
    if row['state'] not in ('running', 'done'):
        return None
    return row


class Watcher(threading.Thread):
    """Читает файл прогресса так часто, как получится, пока команда работает."""

    def __init__(self, path: Path):
        super().__init__(daemon=True)
        self.path = path
        self.stop = threading.Event()
        self.samples: list[dict[str, str]] = []
        self.bad: list[bytes] = []
        self.unreadable = 0

    def run(self) -> None:
        while not self.stop.is_set():
            self.sample()
            time.sleep(0.002)
        self.sample()

    def sample(self) -> None:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError:
            self.unreadable += 1  # читатель столкнулся с подменой файла: это не половина файла, просто повторим
            return
        row = parse(raw)
        if row is None:
            self.bad.append(raw)
        else:
            self.samples.append(row)


def run_tool(tool: rt.Tool, box: rt.Sandbox, command: str, progress: Path | None, **extra: str) -> tuple[dict[str, str], Watcher | None]:
    out = box.root / 'tool-out.txt'
    out.unlink(missing_ok=True)
    args = [str(tool.exe), command, '--payload', str(tool.dir), '--data', str(box.data), '--out', str(out), '--game', str(box.game)]
    for name, value in extra.items():
        args += ['--' + name, value]
    watcher = None
    if progress is not None:
        progress.unlink(missing_ok=True)
        args += ['--progress', str(progress)]
        watcher = Watcher(progress)
        watcher.start()
    env = dict(__import__('os').environ, WXQ_RU_USERS_ROOT=str(box.root / 'no-users'))
    started = time.time()
    done = subprocess.run(args, capture_output=True, env=env, timeout=900)
    if watcher is not None:
        watcher.stop.set()
        watcher.join(5)
    rows = {}
    if out.is_file():
        for line in out.read_text('utf-8-sig').splitlines():
            name, _, value = line.partition('=')
            rows[name] = value
    rows['_exit'] = str(done.returncode)
    rows['_seconds'] = f'{time.time() - started:.1f}'
    return rows, watcher


def judge(name: str, watcher: Watcher, rows: dict[str, str], expect_total: bool = True) -> None:
    samples = watcher.samples
    check(f'{name}: файл прогресса виден и разбирается целиком ({len(samples)} чтений)', len(samples) > 0 and not watcher.bad,
          f'битых чтений: {len(watcher.bad)}, первое: {watcher.bad[:1]}')
    dones = [int(s['done']) for s in samples]
    totals = [int(s['total']) for s in samples]
    check(f'{name}: done не убывает', all(a <= b for a, b in zip(dones, dones[1:])), str(dones[:20]))
    check(f'{name}: done не больше total', all(d <= t for d, t in zip(dones, totals)))
    nonzero = sorted(set(t for t in totals if t))
    check(f'{name}: total после плана не меняется', len(nonzero) <= 1, str(nonzero))
    check(f'{name}: phase и index/count в порядке', all(s['phase'] == name for s in samples) and all(int(s['index']) <= max(1, int(s['count'])) for s in samples))
    if not samples:
        return
    last = samples[-1]
    check(f'{name}: финал done == total, state=done', last['state'] == 'done' and last['done'] == last['total'], str(last))
    if expect_total:
        check(f'{name}: total > 0', int(last['total']) > 0, str(last))
    check(f'{name}: первая запись с done=0', samples[0]['done'] == '0', str(samples[0]))
    states = [s['state'] for s in samples]
    check(f'{name}: state идёт running -> done без возврата', states == sorted(states, key=lambda v: v == 'done') and all(s['done'] == s['total'] for s in samples if s['state'] == 'done'))
    distinct = len(set(dones))
    print(f'       {name}: {rows["_seconds"]} с, total={last["total"]}, различных done={distinct}, последний item={last["item"]!r}, '
          f'чтений={len(samples)}, занятых={watcher.unreadable}', flush=True)
    check(f'{name}: item без полного пути', not any(('\\' in s['item'] or ':' in s['item']) for s in samples))


def game_is_running() -> bool:
    done = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True)
    return bool(re.search(rb'"(WXQ|HonorofKingsACE|WXQLauncher[^"]*)\.exe"', done.stdout, re.IGNORECASE))


def neutralize_running_check(tool: rt.Tool, work: Path) -> None:
    """Настоящая игра запущена на этом ПК, а помощник отказывается работать при любом процессе WXQ.
    Для проверки прогресса пересобираем КОПИЮ помощника в work без этой остановки (файл установщика не меняется)."""
    import build_installer as bi
    source = (HERE.parent / 'WXQTool.cs').read_text('utf-8-sig')
    needle = 'if (active.Count > 0)'
    assert source.count(needle) == 1
    folder = work / 'patched-tool'
    folder.mkdir(exist_ok=True)
    (folder / 'WXQTool.cs').write_text(source.replace(needle, 'if (false && active.Count > 0)'), 'utf-8-sig')
    done = subprocess.run([str(bi.CSC), '/nologo', '/target:exe', '/platform:x64', '/optimize+', '/codepage:65001', '/r:System.Web.Extensions.dll',
                           f'/out:{tool.exe}', str(folder / 'WXQTool.cs')], capture_output=True)
    if done.returncode:
        raise SystemExit('не собралась копия помощника: ' + done.stdout.decode('cp866', errors='replace'))


def flow(tool: rt.Tool, box: rt.Sandbox, progress_dir: Path | None) -> dict:
    """check -> backup -> unpack -> verify -> restore; возвращает результаты команд и снимки игры."""
    outs: dict[str, dict] = {}
    watchers: dict[str, Watcher] = {}
    snaps: dict[str, dict] = {'before': box.snapshot()}
    for command in ('check', 'backup', 'unpack', 'verify', 'restore'):
        extra = {}
        if command in ('unpack', 'verify'):
            extra['label'] = outs['backup']['label']
        progress = progress_dir / f'{command}.txt' if progress_dir else None
        outs[command], watcher = run_tool(tool, box, command, progress, **extra)
        if watcher is not None:
            watchers[command] = watcher
        if command in ('unpack', 'restore'):
            snaps[command] = box.snapshot()
    return {'outs': outs, 'watchers': watchers, 'snaps': snaps}


def normalized(outs: dict[str, dict], root: Path) -> dict[str, dict]:
    result = {}
    for command, rows in outs.items():
        row = {k: v for k, v in rows.items() if not k.startswith('_seconds')}
        row = {k: v.replace(str(root), '<песочница>') for k, v in row.items()}
        if 'label' in row:
            row['label'] = '<метка>' if row['label'] else ''
        result[command] = row
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path)
    parser.add_argument('--keep', action='store_true')
    parser.add_argument('--ignore-running-game', action='store_true', help='собрать копию помощника без остановки при запущенной игре (включается само, если WXQ.exe запущен)')
    args = parser.parse_args()
    work = (args.work or Path(tempfile.mkdtemp(prefix='wxq-ru-progress-'))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    import build_installer as bi  # noqa: E402
    bi.GAME_ROOT = rt.REAL_GAME
    rt.Sandbox.prepare_template(work / 'prime-template')
    real_before = rt.real_state()
    tool = rt.Tool(work / 'tool')
    check('помощник собран', tool.exe.is_file())
    if args.ignore_running_game or game_is_running():
        print('       WXQ.exe запущен: проверка идёт на копии помощника без остановки по запущенной игре (только для фиктивной игры)', flush=True)
        neutralize_running_check(tool, work)

    box = rt.Sandbox(work / 'box-progress')
    box.build()
    progress_dir = work / 'progress'
    shutil.rmtree(progress_dir, ignore_errors=True)
    progress_dir.mkdir()
    with_progress = flow(tool, box, progress_dir)
    for command, rows in with_progress['outs'].items():
        check(f'{command}: завершилась успешно (status=ok, код 0)', rows.get('status') == 'ok' and rows['_exit'] == '0', str(rows))
    for command, watcher in with_progress['watchers'].items():
        judge(command, watcher, with_progress['outs'][command])
    check('временные файлы прогресса не остались', not list(progress_dir.glob('*.tmp')))

    log = box.install_log()
    estimates = re.findall(r'прогресс: оценка (\d+) байт, фактически обработано (\d+)', log)
    for command, (estimate, actual) in zip(('check', 'backup', 'unpack', 'verify', 'restore'), estimates):
        print(f'       {command}: оценка {estimate}, фактически {actual}', flush=True)
    check('в журнале по записи оценки на каждую команду', len(estimates) == 5, str(estimates))

    plain_box = rt.Sandbox(work / 'box-plain')
    plain_box.build()
    plain = flow(tool, plain_box, None)
    check('результаты команд (--out) с --progress и без совпадают', normalized(with_progress['outs'], box.root) == normalized(plain['outs'], plain_box.root),
          f'{normalized(with_progress["outs"], box.root)} != {normalized(plain["outs"], plain_box.root)}')
    for step in ('before', 'unpack', 'restore'):
        check(f'папка игры после шага {step} одинакова с --progress и без', with_progress['snaps'][step] == plain['snaps'][step])
    check('откат вернул игру к исходному состоянию', with_progress['snaps']['restore'] == with_progress['snaps']['before'])

    # Недоступный путь прогресса не роняет команду и не меняет результат.
    bad = work / 'not-a-dir'
    bad.write_text('файл вместо папки', 'utf-8')
    box.build()
    rows_bad, _ = run_tool(tool, box, 'check', bad / 'p.txt')
    box.build()
    rows_ref, _ = run_tool(tool, box, 'check', None)
    check('нерабочий путь --progress не мешает команде', normalized({'c': rows_bad}, box.root) == normalized({'c': rows_ref}, box.root) and rows_bad.get('status') == 'ok',
          f'{rows_bad} != {rows_ref}')

    check('настоящая игра не изменилась', rt.real_state() == real_before)
    failed = [r for r in results if not r[1]]
    print(f'\nпроверок: {len(results)}, прошло: {len(results) - len(failed)}, не прошло: {len(failed)}')
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print('рабочая папка оставлена:', work)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
