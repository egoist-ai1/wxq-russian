"""Проверка стороны Inno в протоколе с окном WXQSetupUI (installer\\ui\\PROTOCOL.md) на подставном окне FakeUI.exe.

    python -X utf8 -B installer/tests/ui_protocol.py --work <папка> [--installer <тестовая сборка с пакетом 1>]

Реальное окно не нужно: FakeUI.exe создаёт тот же мьютекс, читает state.json и пишет commands.jsonl по сценарию (FAKEUI_SCRIPT).
Проверяется: разбор команд (битые, неполные, чужие, повторные номера, неизвестная команда), целостность state.json при чтении без передышки,
честное скрытие окна мастера (ни одного непрозрачного кадра), откат на стандартные страницы Inno (нет файла, окно не стартовало,
окно упало, /NOUI=1), выход по команде. Установка идёт на фиктивной игре во временной папке; реальная игра только читается.
Нужна ТЕСТОВАЯ сборка (build_installer.py --test-build): /UIEXE= и /UISTATEDIR= работают только в ней.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_tests as rt  # noqa: E402

user32 = ctypes.WinDLL('user32', use_last_error=True)
user32.FindWindowW.restype = wintypes.HWND
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetLayeredWindowAttributes.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(wintypes.DWORD)]
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
GWL_EXSTYLE, WS_EX_LAYERED, HIDE_MASK = -20, 0x80000, 0x080800A0   # HIDE_MASK: LAYERED, TOOLWINDOW, TRANSPARENT, NOACTIVATE (как в UiHideWizard)
CSC = rt.INSTALLER_DIR / 'build_installer.py'


def build_fake(work: Path) -> Path:
    import build_installer as bi
    exe = work / 'FakeUI.exe'
    if not exe.is_file():
        done = subprocess.run([str(bi.CSC), '/nologo', '/target:winexe', '/platform:anycpu', '/codepage:65001', '/r:System.Web.Extensions.dll', f'/out:{exe}',
                               str(Path(__file__).with_name('FakeUI.cs'))], capture_output=True)
        if done.returncode:
            raise SystemExit('FakeUI.cs не собрался:\n' + (done.stdout + done.stderr).decode('cp866', errors='replace'))
    return exe


def all_windows(class_name: str) -> list[int]:
    found, hwnd = [], None
    while True:
        hwnd = user32.FindWindowExW(None, hwnd, class_name, None)
        if not hwnd:
            return found
        found.append(hwnd)


def window_state(class_name: str = 'TWizardForm', hwnd: int | None = None) -> dict | None:
    """Окно мастера Inno: видимо ли, прозрачно ли (alpha 0 + стили скрытия)."""
    hwnd = hwnd or user32.FindWindowW(class_name, None)
    if not hwnd:
        return None
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    alpha = ctypes.c_ubyte(255)
    if ex & WS_EX_LAYERED:
        key, flags = wintypes.DWORD(0), wintypes.DWORD(0)
        user32.GetLayeredWindowAttributes(hwnd, ctypes.byref(key), ctypes.byref(alpha), ctypes.byref(flags))
    return {'visible': bool(user32.IsWindowVisible(hwnd)), 'alpha': alpha.value, 'hidden_styles': (ex & HIDE_MASK) == HIDE_MASK, 'ex': ex}


class Sampler(threading.Thread):
    """Каждые 3 мс смотрит окна мастера и служебное окно приложения: считает кадры, когда окно видимо И непрозрачно."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stop_flag = False
        self.samples = 0
        self.opaque_visible = 0
        self.opaque_wizard = 0
        self.seen = False
        self.events: list[str] = []
        self.ignore = {name: set(all_windows(name)) for name in ('TWizardForm', 'TApplication')}   # чужие окна, что были до запуска
        self.started = time.time()

    def run(self) -> None:
        while not self.stop_flag:
            for name, hwnd in [(n, h) for n in ('TWizardForm', 'TApplication') for h in all_windows(n) if h not in self.ignore[n]]:
                state = window_state(name, hwnd)
                if state:
                    self.seen = True
                    self.samples += 1
                    if state['visible'] and state['alpha'] > 0:
                        self.opaque_visible += 1
                        if name == 'TWizardForm':
                            self.opaque_wizard += 1
                        if len(self.events) < 6:
                            pid = wintypes.DWORD(0)
                            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                            exe = ctypes.create_unicode_buffer(520)
                            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
                            size = wintypes.DWORD(520)
                            if handle:
                                ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, exe, ctypes.byref(size))
                                ctypes.windll.kernel32.CloseHandle(handle)
                            self.events.append(f"{time.time() - self.started:.2f}s {name} hwnd={hwnd:#x} pid={pid.value} {Path(exe.value).name} alpha={state['alpha']} ex={state['ex']:#x}")
            time.sleep(0.003)


def stop_tree(process: subprocess.Popen) -> None:
    subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)], capture_output=True)
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def wait_until(predicate, timeout: float, step: float = 0.1) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def report_path(state_dir: Path) -> Path:
    """Отчёт FakeUI лежит рядом с защищённой папкой (сама папка удаляется установщиком при выходе)."""
    return state_dir.parent / (state_dir.name + '.report.txt')


def report_of(state_dir: Path) -> dict[str, str]:
    path = report_path(state_dir)
    rows: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text('utf-8').splitlines():
            name, _, value = line.partition('=')
            rows[name] = value
    return rows


def current_sid() -> str:
    """SID текущего пользователя (в тестовой сборке он добавлен к правам защищённой папки, иначе тест запер бы себя)."""
    done = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True, timeout=60)
    return done.stdout.decode('cp866', errors='replace').strip().split(',')[-1].strip('"')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--installer', type=Path, help='тестовая сборка (установщик с пакетом 1 достаточно); без параметра собирается сама')
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(rt.INSTALLER_DIR))
    import build_installer as bi  # noqa: E402
    bi.GAME_ROOT = rt.REAL_GAME
    installer = (args.installer or rt.build_variant(work, 'pkg1', (rt.CARRIER,))).resolve(strict=True)
    fake = build_fake(work)
    rt.Sandbox.prepare_template(work / 'prime-template')
    box = rt.Sandbox(work / 'box')
    state_before = rt.real_state()
    uninstall_before = rt.uninstall_keys()
    counter = [0]

    def launch(script: str, extra: list[str] | None = None, ui: Path | None = None, mismatch: bool = False) -> tuple[subprocess.Popen, Path]:
        counter[0] += 1
        box.build()
        if mismatch:   # версия игры не совпадает: установка останавливается до записи, быстро и без изменений
            (box.cache / '1/1.info').write_text('{"version":"9.9.9.9","revision":"0"}', 'utf-8')
        state_dir = work / f'state-{counter[0]}'
        import shutil
        shutil.rmtree(state_dir, ignore_errors=True)
        env = dict(os.environ, WXQ_RU_USERS_ROOT=str(box.root / 'no-users'), WXQ_RU_TRUST_ROOT=str(box.launcher), FAKEUI_SCRIPT=script, MSYS2_ARG_CONV_EXCL='*',
                   FAKEUI_REPORT=str(report_path(state_dir)))
        report_path(state_dir).unlink(missing_ok=True)
        command = [str(installer), '/CURRENTUSER', f'/DIR={box.app}', f'/DATADIR={box.data}', f'/GAMEDIR={box.game}', f'/LOG={box.log}',
                   f'/UISTATEDIR={state_dir}', f'/UIEXE={ui or fake}', *(extra or [])]
        return subprocess.Popen(command, env=env), state_dir

    def log_text() -> str:
        return box.log.read_text('utf-8-sig', errors='replace') if box.log.is_file() else ''

    print('W1. окно: разбор команд, неполные и чужие строки, повторные номера')
    before = None
    sampler = Sampler()
    process, state_dir = launch(
        'wait=welcome'
        '|raw=garbage\\n'                                         # не JSON
        '|raw={"seq":1,"cmd":"format"}\\n'                         # команда вне списка
        '|raw={"seq":2,"cmd":"install","path":"C:\\\\x"}\\n'       # лишнее поле (путь)
        '|raw={"seq":03,"cmd":"close"}\\n'                         # номер с нулём впереди
        '|raw={"seq":9,"cmd":"close"} \\n'                         # хвост после строки
        '|raw={"seq":4,"cmd":"install","options":{"launcher":"yes"}}\\n'   # значение вне true/false
        '|raw={"seq":5,"cmd":"remove"}\\n'                         # удаление на экране приветствия без установленного перевода: игнор
        '|raw={"seq":6,"cmd":"launch"}\\n'                         # запуск вне экрана «Готово»: игнор
        '|raw={"seq":7,"cmd":"back"}\\n'                           # возврат вне экрана сбоя: игнор
        '|raw={"seq":8,"cmd":"install"'                            # половина строки: перевода строки нет, брать нельзя
        '|sleep=700'
        '|raw=}\\n'                                                # теперь строка целая: установка стартует и сразу падает на версии игры
        '|wait=fail'
        '|cmd=8,close'                                             # номер уже был: игнор
        '|raw=' + 'x' * 200 + '\\n'                                # слишком длинная строка
        '|cmd=10,back|wait=welcome'
        '|cmd=11,close',
        mismatch=True)
    sampler.start()
    box_before = None
    try:
        # снимок игры для сравнения после сбоя до записи
        time.sleep(1.5)
        box_before = box.snapshot()
        ok = wait_until(lambda: 'UI: команда close' in log_text(), 240)
        rt.check('W1a команда close выполнена и мастер завершился', ok and wait_until(lambda: process.poll() is not None, 60))
    finally:
        sampler.stop_flag = True
        stop_tree(process)
    text = log_text()
    report = report_of(state_dir)
    rejected = text.count('строка команды отвергнута')
    rt.check('W1b отвергнуты ровно восемь строк (мусор, чужая команда, лишнее поле, ноль впереди, хвост, значение вне true/false, повтор номера, длинная)',
             rejected == 8, f'{rejected}')
    rt.check('W1c установка запущена один раз (команда из половины строки взята только после перевода строки)', text.count('UI: команда install') == 1, str(text.count('UI: команда install')))
    rt.check('W1d удаление на экране приветствия без установленного перевода ничего не сделало: журнал без отката', 'Tool: restore' not in text and 'Tool (live): restore' not in text)
    rt.check('W1e команды back и close приняты', 'UI: команда back' in text and 'UI: команда close' in text)
    screens = report.get('screens', '')
    rt.check('W1f экраны окна по порядку: приветствие, прогресс, сбой, приветствие (и quit в конце)', screens.startswith('welcome,progress,fail,welcome') and screens.endswith('quit'), screens)
    rt.check('W1g сбой до записи: файлы игры не изменились', box_before is not None and box.snapshot() == box_before)
    rt.check('W1h окно мастера Inno ни разу не было видимо и непрозрачно, а служебное окно приложения (до первого вызова скрипта) не дольше ~50 мс',
             sampler.seen and sampler.opaque_wizard == 0 and sampler.opaque_visible <= 15, f'seen={sampler.seen} samples={sampler.samples} wizard={sampler.opaque_wizard} app+wizard={sampler.opaque_visible} {sampler.events}')

    print('W2. окно: state.json читается целиком при чтении без передышки (идёт настоящая проверка игры)')
    process, state_dir = launch('wait=welcome|cmd=1,install|hammer=15000')
    time.sleep(22)   # установка стартует, окно 15 секунд читает state.json подряд
    report = report_of(state_dir)
    stop_tree(process)
    reads = int(report.get('reads', '0') or 0)
    rt.check('W2a прочитано не меньше 150 состояний подряд', reads >= 150, str(report))
    rt.check('W2b ни одной оборванной записи (partial = 0) и rev не убывал (regress = 0)', report.get('partial') == '0' and report.get('regress') == '0', str(report))
    rt.check('W2c состояние менялось (rev вырос больше чем на 20)', int(report.get('rev_max', '0') or 0) > 20, str(report) + ' ' + log_text()[-600:])

    print('W3. откат на стандартные страницы Inno')
    cases = [
        ('W3a нет exe окна', work / 'нет-такого.exe', [], 'hold', 'WXQSetupUI.exe не найден'),
        ('W3b окно не создало мьютекс за 10 секунд', None, [], 'nomutex|hold', 'не запустился за 10 секунд'),
        ('W3c /NOUI=1', None, ['/NOUI=1'], 'hold', 'отключено параметром /NOUI=1'),
    ]
    for name, ui, extra, script, marker in cases:
        process, state_dir = launch(script, extra, ui=ui)
        found = wait_until(lambda: marker in log_text(), 40)
        state = None
        if found:
            wait_until(lambda: (window_state() or {}).get('visible', False), 30)
            state = window_state()
        rt.check(f'{name}: причина отката в журнале', found, log_text()[-300:])
        rt.check(f'{name}: окно мастера Inno видимо и без стилей скрытия', bool(state) and state['visible'] and not state['hidden_styles'] and state['alpha'] == 255, str(state))
        if name.startswith('W3b'):
            rt.check('W3b окно, запоздавшее с мьютексом, получило quit и закрылось', wait_until(lambda: report_of(state_dir).get('screens', '').endswith('quit'), 30), str(report_of(state_dir)))
        if name.startswith('W3c'):
            rt.check('W3c при /NOUI=1 окно не копировалось и не запускалось, а помощник работает из защищённой папки',
                     'Окно WXQSetupUI.exe запущено' not in log_text() and not (state_dir / 'WXQSetupUI.exe').exists() and (state_dir / 'WXQTool.exe').is_file())
        stop_tree(process)
        time.sleep(0.5)

    print('W4. окно упало во время работы')
    process, state_dir = launch('wait=welcome|sleep=800|die')
    found = wait_until(lambda: 'закрылось не по команде' in log_text(), 40)
    wait_until(lambda: (window_state() or {}).get('visible', False), 30)
    state = window_state()
    rt.check('W4a мастер заметил исчезновение окна', found, log_text()[-300:])
    rt.check('W4b окно мастера вернулось на экран', bool(state) and state['visible'] and not state['hidden_styles'], str(state))
    stop_tree(process)

    print('W5. окно закрыто командой close с приветствия')
    process, state_dir = launch('wait=welcome|cmd=1,close')
    exited = wait_until(lambda: process.poll() is not None, 60)
    rt.check('W5a мастер завершился по close без видимого окна', exited)
    if not exited:
        stop_tree(process)
    rt.check('W5c защищённая папка удалена при завершении установщика', wait_until(lambda: not state_dir.exists(), 15), str(sorted(p.name for p in state_dir.glob('*')) if state_dir.exists() else ''))
    rt.check('W5d в журнале: защищённая папка удалена', 'Защищённая папка удалена' in log_text())
    wait_until(lambda: report_of(state_dir).get('screens', '').endswith('quit'), 15)
    report = report_of(state_dir)
    rt.check('W5b окно получило quit', report.get('screens', '').endswith('quit'), str(report))

    print('W6. окно упало во время записи: установка не прерывается, мастер Inno снова виден')
    process, state_dir = launch('wait=welcome|cmd=1,install|sleep=2000|die')
    found = wait_until(lambda: 'закрылось не по команде' in log_text(), 90)
    visible = wait_until(lambda: (window_state() or {}).get('visible', False), 30)
    state = window_state()
    mid = log_text()
    rt.check('W6a мастер заметил исчезновение окна во время установки', found and 'UI: команда install' in mid, mid[-300:])
    rt.check('W6b окно мастера Inno стало видимым и непрозрачным (без стилей скрытия)', visible and bool(state) and state['visible'] and not state['hidden_styles'], str(state))
    finished = wait_until(lambda: 'Tool verify -> ok' in log_text(), 300)
    after = log_text()
    rt.check('W6c установка дошла до конца (verify ok), запись не прервана и не откатывалась', finished and 'Tool (live): restore' not in after and 'Tool restore' not in after, after[-400:])
    stop_tree(process)

    print('W7. защищённая папка установщика: права, состав, SHA-256 копий (тестовая сборка добавляет текущего пользователя к Администраторам и SYSTEM)')
    process, state_dir = launch('wait=welcome|hold')
    started = wait_until(lambda: 'Окно WXQSetupUI.exe запущено' in log_text(), 90)
    rt.check('W7a окно запущено', started, log_text()[-300:])
    me = current_sid()
    protected, rows = rt.acl_rows(state_dir)
    sids = sorted(r[0] for r in rows)
    rt.check('W7b наследование отключено; записи ровно: Администраторы S-1-5-32-544, SYSTEM S-1-5-18, текущий пользователь',
             protected and sids == sorted(['S-1-5-32-544', 'S-1-5-18', me]), f'{protected} {rows}')
    rt.check('W7c ни одной унаследованной записи, нет Users, Authenticated Users, Everyone; везде полный доступ с наследованием на папки и файлы',
             all(r[2] == 'False' and 'FullControl' in r[1] for r in rows) and not ({'S-1-5-32-545', 'S-1-5-11', 'S-1-1-0'} & set(sids)), str(rows))
    names = ('WXQTool.exe', 'WXQSetupUI.exe', 'install.json', 'state.json')
    rt.check('W7d в папке помощник, окно, install.json и файл состояния', all((state_dir / n).is_file() for n in names), str(sorted(p.name for p in state_dir.glob('*'))))
    file_protected, file_rows = rt.acl_rows(state_dir / 'WXQTool.exe')
    rt.check('W7e файл внутри наследует те же права (те же три SID, все унаследованы)',
             sorted(r[0] for r in file_rows) == sids and all(r[2] == 'True' for r in file_rows), str(file_rows))
    text = log_text()
    import re
    logged = re.search(r'WXQTool\.exe: SHA-256 копии в защищённой папке совпал с эталоном сборки \(([0-9a-f]{64})\)', text)
    rt.check('W7f SHA-256 копии помощника в журнале совпадает с файлом в папке и с эталоном сборки (сверка прошла)',
             bool(logged) and logged.group(1) == rt.sha(state_dir / 'WXQTool.exe'), text[-300:])
    rt.check('W7g окно запущено именно из защищённой папки (процесс WXQSetupUI.exe - копия подставного окна, SHA совпал)', rt.sha(state_dir / 'WXQSetupUI.exe') == rt.sha(fake))
    owner = subprocess.run(['powershell.exe', '-NoProfile', '-Command', f"(Get-Acl -LiteralPath '{state_dir}').GetOwner([Security.Principal.SecurityIdentifier]).Value"], capture_output=True, text=True).stdout.strip()
    rt.check('W7h владелец защищённой папки - создавший её процесс (в тестовой сборке пользователь или Администраторы), установщик это проверил; проверки повторялись после копирования и перед запуском окна',
             owner in (me, 'S-1-5-32-544', 'S-1-5-18') and 'права и владелец проверены' in text and 'отказ' not in text and 'неверный владелец' not in text, f'{owner} {text[-300:]}')
    stop_tree(process)

    print('W8. сверка SHA-256 копии окна: совпала - окно работает; не совпала - стандартные страницы Inno, копия удалена')
    process, state_dir = launch('wait=welcome|hold', ['/UIEXESHA=' + rt.sha(fake)])
    rt.check('W8a верный эталон: окно запущено', wait_until(lambda: 'Окно WXQSetupUI.exe запущено' in log_text(), 90) and 'WXQSetupUI.exe: SHA-256 копии' in log_text(), log_text()[-300:])
    stop_tree(process)
    process, state_dir = launch('hold', ['/UIEXESHA=' + '0' * 64])
    found = wait_until(lambda: 'не прошёл проверку в защищённой папке' in log_text(), 60)
    wait_until(lambda: (window_state() or {}).get('visible', False), 30)
    state = window_state()
    rt.check('W8b чужой эталон: окно не запускается, причина в журнале', found and 'Окно WXQSetupUI.exe запущено' not in log_text() and 'не совпал с эталоном' in log_text(), log_text()[-400:])
    rt.check('W8c копия окна удалена, помощник на месте, стандартные страницы Inno видимы', not (state_dir / 'WXQSetupUI.exe').exists() and (state_dir / 'WXQTool.exe').is_file() and
             bool(state) and state['visible'] and not state['hidden_styles'] and state['alpha'] == 255, str(state))
    stop_tree(process)

    print('W9. нехватка места для данных перевода (тестовый /FREEBYTES=1000): сбой до записи, окно показывает экран сбоя, игра не тронута')
    process, state_dir = launch('wait=welcome|cmd=1,install|wait=fail|cmd=2,close', ['/FREEBYTES=1000'])
    time.sleep(1.0)
    snapshot9 = box.snapshot()
    found = wait_until(lambda: 'Места для данных перевода не хватает' in log_text(), 120)
    wait_until(lambda: process.poll() is not None, 60)
    stop_tree(process)
    report = report_of(state_dir)
    rt.check('W9a в журнале: места не хватает, данные не извлекались', found and 'Tool backup' not in log_text(), log_text()[-400:])
    rt.check('W9b окно показало экран сбоя', 'fail' in report.get('screens', ''), str(report))
    rt.check('W9c до записи ничего не менялось: расписок нет, игра как была', len(box.receipts()) == 0 and box.snapshot() == snapshot9)

    rt.check('Z настоящая игра не изменялась', rt.real_state() == state_before)
    rt.check_uninstall_unchanged(uninstall_before)
    failed = [name for name, ok, _ in rt.results if not ok]
    print(f'\nИтог: {len(rt.results) - len(failed)} из {len(rt.results)} проверок пройдено')
    if failed:
        print('Не пройдено:', *failed, sep='\n  ')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
