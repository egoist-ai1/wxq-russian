"""Проход установщика с настоящим окном WXQSetupUI без участия мыши и клавиатуры пользователя.

Окно ведёт встроенный автопилот (только тестовая сборка): WXQ_UI_AUTOPILOT задаёт нажатия по экранам, WXQ_UI_SHOTS — папку для PNG.
Игра фиктивная (/GAMEDIR во временной песочнице), поэтому настоящая игра не затрагивается.

    python -X utf8 -B installer/tests/ui_smoke.py --installer <exe> [--launcher-installer <exe с --launcher-build>] --shots <папка для png> --work <папка>

Нужна ТЕСТОВАЯ сборка с окном (build_installer.py --test-build, пакеты 1+).
Сценарии: U1 установка и закрытие; U2 «уже установлено» и удаление из окна; U3 сбой по версии игры и возврат; U4 переключатель русского лаунчера;
U5 классические страницы Inno при /NOUI=1 (откат без окна).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_tests as rt  # noqa: E402


def stop_tree(process: subprocess.Popen) -> None:
    subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)], capture_output=True)
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--launcher-installer', type=Path, help='тестовая сборка с --launcher-build: сценарий с переключателем «Русский лаунчер»')
    parser.add_argument('--shots', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=1500, help='секунд на одну установку')
    args = parser.parse_args()
    args.shots.mkdir(parents=True, exist_ok=True)
    uninstall_before = rt.uninstall_keys()
    rt.Sandbox.prepare_template(args.work / 'prime-template')
    box = rt.Sandbox(args.work / 'ui-box')
    counter = [0]

    def run(name: str, plan: str, installer: Path | None = None, extra: list[str] | None = None, wait: int | None = None) -> tuple[int | None, str, Path]:
        """Один запуск мастера с автопилотом. Возвращает код выхода (None при зависании), текст журнала и папку снимков."""
        counter[0] += 1
        shots = args.shots / f'{counter[0]:02d}-{name}'
        shutil.rmtree(shots, ignore_errors=True)
        state_dir = args.work / f'ui-state-{counter[0]}'
        shutil.rmtree(state_dir, ignore_errors=True)
        env = dict(os.environ, WXQ_RU_USERS_ROOT=str(box.root / 'no-users'), WXQ_RU_TRUST_ROOT=str(box.launcher), WXQ_UI_AUTOPILOT=plan,
                   WXQ_UI_SHOTS=str(shots), MSYS2_ARG_CONV_EXCL='*')
        command = [str(installer or args.installer), '/CURRENTUSER', f'/DIR={box.app}', f'/DATADIR={box.data}', f'/GAMEDIR={box.game}', f'/LOG={box.log}',
                   f'/UISTATEDIR={state_dir}', *(extra or [])]
        process = subprocess.Popen(command, env=env)
        try:
            process.wait(timeout=wait or args.timeout)
            code: int | None = process.returncode
        except subprocess.TimeoutExpired:
            code = None
            stop_tree(process)
        time.sleep(1.0)
        return code, (box.log.read_text('utf-8-sig', errors='replace') if box.log.is_file() else ''), shots

    def shots_of(folder: Path) -> list[str]:
        return sorted(p.name for p in folder.glob('*.png')) if folder.is_dir() else []

    print('U1. штатная установка: приветствие, установка, «Готово», закрытие')
    box.build()
    code, log, shots = run('install', 'welcome=install;done=close')
    names = ' '.join(shots_of(shots))
    rt.check('U1a мастер завершился сам с кодом 0', code == 0, str(code))
    rt.check('U1b окно показало приветствие, прогресс и «Готово» (снимки)', all(part in names for part in ('welcome', 'progress', 'done')), names)
    rt.check('U1c в журнале: окно запущено, команды install и close', 'Окно WXQSetupUI.exe запущено' in log and 'UI: команда install' in log and 'UI: команда close' in log)
    rt.check('U1d перевод установлен (расписка записана)', len(box.receipts()) >= 1)
    rt.check('U1e ни разу не вернулись на стандартные страницы Inno', 'возвращаю стандартные страницы' not in log)

    print('U2. «уже установлено» и удаление из окна')
    code, log, shots = run('remove', 'installed=remove;removed=close')
    names = ' '.join(shots_of(shots))
    rt.check('U2a мастер завершился сам (код 0; 2 — «отменено» у Inno при закрытии не с экрана «Готово»)', code in (0, 2), str(code))
    rt.check('U2b окно открылось на «Уже установлено», затем удаление и итог', 'installed' in names and 'removed' in names, names)
    rt.check('U2c перевод удалён (расписок нет), в журнале restore завершился успешно', len(box.receipts()) == 0 and 'Tool restore -> ok' in log, f'{len(box.receipts())}')

    print('U3. сбой: версия игры не совпала; возврат на приветствие')
    box.build()
    (box.cache / '1/1.info').write_text('{"version":"9.9.9.9","revision":"0"}', 'utf-8')
    code, log, shots = run('fail', 'welcome=install;fail=back;welcome=close', wait=300)
    names = ' '.join(shots_of(shots))
    rt.check('U3a мастер завершился сам (код 0 или 2, как в U2a)', code in (0, 2), str(code))
    rt.check('U3b окно показало сбой и вернулось на приветствие', 'fail' in names and names.count('welcome') >= 1, names)
    rt.check('U3c до записи ничего не менялось: расписок нет', len(box.receipts()) == 0)

    if args.launcher_installer:
        print('U4. сборка с русским лаунчером: переключатель, установка, «Готово» с обоими ярлыками')
        desktop, startmenu = box.root.parent / 'ui-desktop', box.root.parent / 'ui-start'
        box.build()
        for folder in (desktop, startmenu):
            shutil.rmtree(folder, ignore_errors=True)
        desktop.mkdir(parents=True)
        original = box.launcher / rt.LAUNCHER_EXE_REL
        original.parent.mkdir(parents=True)
        original.write_bytes(b'stub-original-launcher')
        # переключатель по умолчанию ВКЛЮЧЁН: без клика лаунчер ставится
        code, log, shots = run('launcher', 'welcome=install;done=close', args.launcher_installer, [f'/DESKTOPDIR={desktop}', f'/STARTDIR={startmenu}'])
        rt.check('U4a мастер завершился сам с кодом 0', code == 0, str(code))
        rt.check('U4b ярлык русского лаунчера создан (переключатель по умолчанию включён)', (desktop / 'Honor of Kings ACE (RU).lnk').is_file())
        box.uninstall()
        # клик по переключателю выключает: ярлыка нет
        box.build()
        shutil.rmtree(desktop, ignore_errors=True)
        desktop.mkdir(parents=True)
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes(b'stub-original-launcher')
        code, log, shots = run('launcher-off', 'welcome=toggle;welcome=install;done=close', args.launcher_installer, [f'/DESKTOPDIR={desktop}', f'/STARTDIR={startmenu}'])
        rt.check('U4c после клика по переключателю мастер завершился с кодом 0', code == 0, str(code))
        rt.check('U4d ярлыка русского лаунчера нет (переключатель выключен)', not (desktop / 'Honor of Kings ACE (RU).lnk').exists())
        box.uninstall()

    print('U5. /NOUI=1: стандартные страницы Inno, окно WXQSetupUI не запускается')
    box.build()
    counter[0] += 1
    env = dict(os.environ, WXQ_RU_USERS_ROOT=str(box.root / 'no-users'), WXQ_RU_TRUST_ROOT=str(box.launcher), MSYS2_ARG_CONV_EXCL='*')
    process = subprocess.Popen([str(args.installer), '/CURRENTUSER', f'/DIR={box.app}', f'/DATADIR={box.data}', f'/GAMEDIR={box.game}', f'/LOG={box.log}', f'/SECPARENT={args.work / "ui-secparent"}', '/NOUI=1'], env=env)
    end = time.time() + 40
    while time.time() < end and 'отключено параметром /NOUI=1' not in (box.log.read_text('utf-8-sig', errors='replace') if box.log.is_file() else ''):
        time.sleep(0.3)
    log = box.log.read_text('utf-8-sig', errors='replace') if box.log.is_file() else ''
    rt.check('U5a журнал: окно отключено параметром /NOUI=1', 'отключено параметром /NOUI=1' in log)
    rt.check('U5b окно WXQSetupUI не запускалось', 'Окно WXQSetupUI.exe запущено' not in log)
    rt.check('U5c стандартные страницы работают с помощником из защищённой папки (права закрыты, SHA-256 копий совпал)',
             'Защищённая папка создана, права и владелец проверены' in log and 'WXQTool.exe: SHA-256 копии' in log and 'отказ' not in log)
    stop_tree(process)

    rt.check_uninstall_unchanged(uninstall_before)
    failed = [name for name, ok, _ in rt.results if not ok]
    print(f'\nИтог: {len(rt.results) - len(failed)} из {len(rt.results)} проверок пройдено; снимки: {args.shots}')
    if failed:
        print('Не пройдено:', *failed, sep='\n  ')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
