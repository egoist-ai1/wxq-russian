"""Две реальные TESTBUILD Inno-сборки: установка, обновление и цель ярлыка.

    python -X utf8 -B installer/tests/isolated_test_run.py --work <Temp-guard> -- \
        <python.exe> -X utf8 -B installer/tests/test_setup_upgrade.py --work <Temp-run>

Сборки используют штатные Pascal/C#/WPF и synthetic game из test_setup_manager.
Inno запускать только через isolated_test_run.py: его privateDesktop и Job
изолируют также неожиданные нативные диалоги и оставшиеся дочерние процессы.
FakeUI не создаёт окна; desktop/start и TEST uninstall key ограничены песочницей.
Проверяется реальная замена manager, а не только передача управления fake Setup.
--full-handoff проверяет также кнопку обновления со скачиванием настоящего Inno.
Она требует TESTBUILD-поддержки test-setup-profile.json в SetupLaunch; исторические
RED-бинарники запускаются без этого флага.
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
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import test_setup_manager as tm  # noqa: E402
import publish_release as pr  # noqa: E402

OLD_VERSION = '0.6.4'
NEW_VERSION = '0.6.7'


class RetrySession(tm.Session):
    def command(self, command: str) -> None:
        # Чтение Inno может на миллисекунды занять файл. Номер команды не меняем
        # между попытками; полная строка либо дописана однажды, либо есть ошибка.
        self.seq += 1
        text = json.dumps({'seq': self.seq, 'cmd': command}, separators=(',', ':')) + '\n'
        for attempt in range(20):
            try:
                with (self.ui / 'commands.jsonl').open('a', encoding='utf-8', newline='\n') as out:
                    out.write(text)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.025)


# Sandbox.launch создаёт Session из globals своего модуля. Подмена только fixture
# данного Python-процесса, штатные источники и производственные бинарники не меняются.
tm.Session = RetrySession


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict[str, str]:
    return {name: sha(PROJECT / name) for name in (
        'installer/WXQRussian.iss', 'installer/WXQTool.cs',
        'installer/ui/WXQSetupUI.cs', 'installer/build_installer.py')}


class Results:
    def __init__(self, work: Path):
        self.work = work
        self.checks: list[dict] = []
        self.details: dict = {'source_before': source_hashes()}

    def check(self, name: str, condition: bool, detail: object = '') -> bool:
        condition = bool(condition)
        self.checks.append({'name': name, 'passed': condition, 'detail': detail})
        print(('  ok   ' if condition else '  FAIL ') + name
              + (f' [{detail}]' if not condition and detail else ''), flush=True)
        self.save()
        return condition

    def save(self) -> None:
        body = dict(self.details, checks=self.checks,
                    passed=bool(self.checks) and all(x['passed'] for x in self.checks))
        (self.work / 'result.json').write_text(json.dumps(body, ensure_ascii=False, indent=2), 'utf-8')


def build_version(work: Path, version: str, config: dict) -> tuple[Path, dict]:
    folder = work / ('build-' + version)
    folder.mkdir()
    previous = tm.VERSION
    try:
        tm.VERSION = version
        return tm.compile_installer(folder, config)
    finally:
        tm.VERSION = previous


def publish_exact(server: tm.Server, setup: Path, key, version: str) -> dict:
    """Публикуем точные байты Inno, без хвостового fake-version overlay Env.publish."""
    manifest = pr.build_manifest(version, setup, None, [], pr.key_id_of(key.public_key()),
                                 ['https://example.invalid/'], issued_utc='2026-10-22T00:00:00Z',
                                 notes='Изолированная проверка двух настоящих Inno', game_versions=['1.6.0.1'])
    manifest['base_urls'] = [server.url]
    data = pr.manifest_bytes(manifest)
    server.files.clear()
    server.files['/update.json'] = data
    server.files['/update.json.sig'] = pr.sig_to_text(pr.sign_bytes(key, data)).encode('ascii')
    server.files['/' + setup.name] = setup.read_bytes()
    return manifest


def shortcut_info(link: Path, work: Path) -> dict:
    """Читаем существующий .lnk через WScript.Shell, не запускаем его и не сохраняем."""
    script = work / 'read-shortcut.ps1'
    if not script.exists():
        script.write_text('''param([Parameter(Mandatory=$true)][string]$Link)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($Link)
[ordered]@{ target=$shortcut.TargetPath; arguments=$shortcut.Arguments; working_dir=$shortcut.WorkingDirectory } | ConvertTo-Json -Compress
''', 'utf-8-sig')
    done = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(script), str(link)],
                          capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    if done.returncode:
        raise RuntimeError(done.stderr.decode('utf-8', errors='replace'))
    return json.loads(done.stdout.decode('utf-8-sig'))


def finish(session: tm.Session, result: Results, prefix: str, timeout: int = 45) -> bool:
    terminal = session.wait(lambda s: s.get('screen') in ('done', 'fail'), timeout)
    return result.check(prefix, terminal and session.state().get('screen') == 'done', session.state())


def install_command(session: tm.Session, result: Results, prefix: str, timeout: int = 45) -> bool:
    rev = session.state().get('rev', 0)
    session.command('install')
    terminal = session.wait(lambda s: s.get('rev', 0) > rev and s.get('screen') in ('done', 'fail'), timeout)
    state = session.state()
    result.check(prefix + ' команда не пропала: есть терминальный ответ', terminal, state)
    return result.check(prefix + ' успешная установка', terminal and state.get('screen') == 'done', state)


def setup_mutex_present() -> bool:
    """Только чтение: новый Inno закрыт, когда его глобальный mutex исчез."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.OpenMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenMutexW(0x00100000, False, r'Global\Egoist.WXQRussian.Setup')
    if handle:
        kernel.CloseHandle(handle)
        return True
    error = ctypes.get_last_error()
    if error == 2:
        return False
    if error == 5:
        return True
    raise ctypes.WinError(error)


def full_handoff(work: Path, old: Path, new: Path, install: dict, key, result: Results,
                 fake: Path, server: tm.Server, env: tm.Env, boxes: list) -> None:
    box = tm.Sandbox(work / 'real-handoff', install)
    boxes.append(box)
    publish_exact(server, old, key, OLD_VERSION)
    initial = box.launch(old, fake, env.keys_file, server.url)
    ready = initial.wait(lambda s: s.get('screen') == 'welcome' and s.get('update_state') == 'current')
    result.check('H1 старый Inno готов перед полным handoff', ready, initial.state())
    if not ready:
        return
    installed = install_command(initial, result, 'H2 старый Inno до handoff')
    manager = box.app / 'WXQ-Russian.exe'
    result.check('H3 до update установлена старая сборка', installed and manager.is_file()
                 and sha(manager) == sha(old) and tm.read_json(box.data / 'state.json').get('version') == OLD_VERSION)
    result.check('H4 первая сессия закрылась', initial.close())
    if not installed:
        return
    profile = {'app_root': str(box.app), 'ui_root': str(box.root / 'ui-handoff'),
               'fake_ui': str(fake), 'fake_ui_sha': sha(fake),
               'desktop_root': str(box.root / 'desktop'), 'start_root': str(box.root / 'start'),
               'update_base': server.url}
    (box.data / 'test-setup-profile.json').write_text(json.dumps(profile), 'utf-8')
    result.details['handoff_test_profile'] = profile
    manifest = publish_exact(server, new, key, NEW_VERSION)
    old_open = box.launch(manager, fake, env.keys_file, server.url)
    available = old_open.wait(lambda s: s.get('screen') == 'installed' and s.get('update_state') == 'available')
    result.check('H5 старый manager увидел подписанный реальный новый Setup', available, old_open.state())
    if not available:
        return
    result.details['full_handoff_exercised'] = True
    old_open.command('update')
    closed = tm.wait_for(lambda: old_open.process.poll() is not None, 40)
    result.check('H6 кнопка update закрыла прежний Inno', closed, old_open.state())
    # Этот Inno запущен реальным SetupLaunch. Его жизненный цикл принадлежит
    # корневому Job; управление выполняется тем же файлом команд, без поиска PID.
    child = RetrySession(box, 900)
    child.ui = Path(profile['ui_root'])
    child.log = box.data / 'updates/setup.log'
    box.sessions.append(child)
    applied = child.wait(lambda s: s.get('screen') in ('done', 'fail'), 60)
    result.check('H7 загруженный настоящий новый Inno завершил установку', applied
                 and child.state().get('screen') == 'done', child.state())
    state = tm.read_json(box.data / 'state.json')
    result.check('H8 handoff заменил manager и записал новую версию', state.get('installed') is True
                 and state.get('version') == NEW_VERSION and manager.is_file() and sha(manager) == sha(new), state)
    downloaded = box.data / 'updates' / ('WXQ-Russian-Setup-' + NEW_VERSION + '.exe')
    result.check('H9 скачанные байты совпали с подписанным реальным Setup', downloaded.is_file()
                 and downloaded.stat().st_size == manifest['setup']['bytes']
                 and sha(downloaded) == manifest['setup']['sha256'] == sha(new))
    if child.ui.is_dir():
        child.command('close')
    result.check('H10 новый Inno завершился после команды close', tm.wait_for(lambda: not setup_mutex_present(), 30))
    links = sorted((box.root / 'desktop').glob('*.lnk'))
    target = None
    if len(links) == 1:
        info = shortcut_info(links[0], work)
        target = Path(info['target']).resolve()
        if not target.is_relative_to(box.root.resolve()):
            raise RuntimeError('Цель ярлыка после handoff вне песочницы')
        result.check('H11 ярлык после handoff ведёт на новую сборку', target == manager.resolve()
                     and target.is_file() and sha(target) == sha(new) and not info['arguments'], info)
    else:
        result.check('H11 ярлык после handoff ведёт на новую сборку', False, [str(p) for p in links])
    if target is None or not target.is_file() or setup_mutex_present():
        return
    reopened = box.launch(target, fake, env.keys_file, server.url)
    ready = reopened.wait(lambda s: s.get('screen') == 'installed'
                          and s.get('update_state') in ('current', 'available', 'failed'))
    result.check('H12 цель ярлыка после полного handoff показывает current', ready
                 and reopened.state().get('update_state') == 'current', reopened.state())
    result.check('H13 приложение и перевод после handoff имеют новую версию', ready
                 and reopened.state().get('manager_version') == NEW_VERSION
                 and reopened.state().get('translation_version') == NEW_VERSION, reopened.state())
    result.check('H14 reopened после полного handoff закрывается', reopened.close())
    result.check('H15 короткие helper handoff завершились', tm.wait_for(lambda: not tm.helper_pids(box.root), 5), tm.helper_pids(box.root))


def runtime(work: Path, old: Path, new: Path, install: dict, key, result: Results, full: bool = False) -> None:
    fake = work / 'FakeUI.exe'
    tm.compile_fake(HERE / 'FakeUI.cs', fake)
    server = tm.Server()
    env = tm.Env(work, work / ('build-' + NEW_VERSION) / 'staging/WXQTool.exe', server,
                 new.read_bytes(), key, tm.rsa.generate_private_key(public_exponent=65537, key_size=3072))
    boxes: list[tm.Sandbox] = []
    try:
        box = tm.Sandbox(work / 'upgrade', install)
        boxes.append(box)
        publish_exact(server, old, key, OLD_VERSION)
        initial = tm.snapshot(box.game)
        first = box.launch(old, fake, env.keys_file, server.url)
        ready = first.wait(lambda s: s.get('screen') == 'welcome' and s.get('update_state') in ('current', 'failed'))
        result.check('G1 старая сборка открылась на welcome', ready, first.state())
        if not ready:
            return
        old_ok = install_command(first, result, 'G2 старая сборка')
        old_state = tm.read_json(box.data / 'state.json')
        result.check('G3 установлен старый payload', old_ok and old_state.get('version') == OLD_VERSION
                     and old_state.get('installed') is True, old_state)
        manager = box.app / 'WXQ-Russian.exe'
        result.check('G4 manager старой сборки побайтно равен старому Setup', manager.is_file()
                     and sha(manager) == sha(old), str(manager))
        result.check('G5 старая сессия закрылась', first.close())
        if not old_ok:
            return
        publish_exact(server, new, key, NEW_VERSION)
        update = box.launch(new, fake, env.keys_file, server.url, ('/UPDATE=1', '/CONTINUE=1'))
        updated = finish(update, result, 'G6 настоящий новый Inno автоматически завершил обновление')
        state = tm.read_json(box.data / 'state.json')
        result.check('G7 state содержит новую установленную версию', updated and state.get('installed') is True
                     and state.get('version') == NEW_VERSION, state)
        result.check('G8 старый и новый Setup различаются', sha(old) != sha(new))
        result.check('G9 manager заменён точной копией нового Setup', manager.is_file()
                     and sha(manager) == sha(new), {'actual': sha(manager) if manager.is_file() else '', 'expected': sha(new)})
        links = sorted((box.root / 'desktop').glob('*.lnk'))
        result.check('G10 создан ровно один ярлык менеджера в test desktop', len(links) == 1, [str(p) for p in links])
        target = None
        if len(links) == 1:
            info = shortcut_info(links[0], work)
            result.details['shortcut'] = dict(link=str(links[0]), **info)
            target = Path(info['target'])
            result.check('G11 ярлык ведёт в test app manager без старых аргументов', target.resolve() == manager.resolve()
                         and info['arguments'] == '', info)
            # Проверяем происхождение до исполнения: чужой/внешний target никогда не запускаем.
            if not target.resolve().is_relative_to(box.root.resolve()):
                raise RuntimeError('Цель ярлыка вне песочницы; исполнение остановлено')
        result.check('G12 сессия нового Inno закрылась', update.close())
        if not updated or target is None or not target.is_file():
            return
        reopened = box.launch(target, fake, env.keys_file, server.url)
        reopened_ready = reopened.wait(lambda s: s.get('screen') == 'installed'
                                       and s.get('update_state') in ('current', 'available', 'failed'))
        result.check('G13 точная цель ярлыка открылась на installed', reopened_ready, reopened.state())
        result.check('G14 повторное обновление до той же версии не предлагается', reopened_ready
                     and reopened.state().get('update_state') == 'current', reopened.state())
        if reopened_ready:
            install_command(reopened, result, 'G15 переустановка через цель ярлыка')
            result.check('G16 переустановка сохранила актуальную версию', tm.read_json(box.data / 'state.json').get('version') == NEW_VERSION)
            log = reopened.log.read_text('utf-8-sig', errors='replace') if reopened.log.is_file() else ''
            result.check('G17 команда install видна в журнале Inno', 'UI: команда install' in log)
        result.check('G18 цель ярлыка не изменилась при self-copy skip', sha(target) == sha(new))
        result.check('G19 reopened сессия закрылась', reopened.close())
        result.check('G20 helper собственной песочницы вышел', tm.wait_for(lambda: not tm.helper_pids(box.root), 5), tm.helper_pids(box.root))
        result.details['game_changed_by_real_test_install'] = tm.snapshot(box.game) != initial

        # Новое приложение должно пережить отказ перевода: ярлык нельзя оставлять
        # на старом менеджере только потому, что следующая игра пока несовместима.
        broken = tm.Sandbox(work / 'update-game-refused', install)
        boxes.append(broken)
        publish_exact(server, old, key, OLD_VERSION)
        before_failure = broken.launch(old, fake, env.keys_file, server.url)
        ready = before_failure.wait(lambda s: s.get('screen') == 'welcome'
                                    and s.get('update_state') in ('current', 'failed'))
        result.check('G24 старая версия готова для отказа следующего патча', ready, before_failure.state())
        if ready:
            old_installed = install_command(before_failure, result, 'G25 старая версия перед несовместимым патчем')
            result.check('G26 старая версия перед отказом закрылась', before_failure.close())
            if old_installed:
                tm.write(broken.cache / '10/10.info', b'{"version":"9.9.9.9","revision":"0"}')
                failed_before = tm.snapshot(broken.game)
                publish_exact(server, new, key, NEW_VERSION)
                refused = broken.launch(new, fake, env.keys_file, server.url, ('/UPDATE=1', '/CONTINUE=1'))
                finished = refused.wait(lambda s: s.get('screen') in ('done', 'fail'), 45)
                result.check('G27 новый перевод явно отказал несовместимому synthetic патчу', finished
                             and refused.state().get('screen') == 'fail', refused.state())
                result.check('G28 отказ до записи сохранил игру', tm.snapshot(broken.game) == failed_before)
                broken_manager = broken.app / 'WXQ-Russian.exe'
                result.check('G29 менеджер обновлён несмотря на отказ игрового перевода', broken_manager.is_file()
                             and sha(broken_manager) == sha(new), {'actual': sha(broken_manager) if broken_manager.is_file() else '',
                                                                 'old': sha(old), 'new': sha(new)})
                links = sorted((broken.root / 'desktop').glob('*.lnk'))
                if len(links) == 1:
                    info = shortcut_info(links[0], work)
                    target = Path(info['target'])
                    result.check('G30 ярлык после отказа ведёт на новую сборку', target.is_file()
                                 and target.resolve() == broken_manager.resolve() and sha(target) == sha(new), info)
                else:
                    result.check('G30 ярлык после отказа ведёт на новую сборку', False, [str(p) for p in links])
                result.check('G31 явный отказ закрывается', refused.close())
                if len(links) == 1:
                    target = Path(shortcut_info(links[0], work)['target']).resolve()
                    if not target.is_relative_to(broken.root.resolve()):
                        raise RuntimeError('Цель ярлыка после отказа вне песочницы')
                    retry = broken.launch(target, fake, env.keys_file, server.url)
                    ready = retry.wait(lambda s: s.get('screen') in ('welcome', 'installed')
                                       and s.get('update_state') in ('current', 'available', 'failed'))
                    result.check('G32 ярлык после отказа не предлагает повторно тот же update', ready
                                 and retry.state().get('update_state') == 'current', retry.state())
                    if ready:
                        rev = retry.state().get('rev', 0)
                        retry.command('install')
                        answered = retry.wait(lambda s: s.get('rev', 0) > rev
                                              and s.get('screen') in ('done', 'fail'), 45)
                        result.check('G33 повторный install несовместимого патча отвечает явным fail', answered
                                     and retry.state().get('screen') == 'fail', retry.state())
                    result.check('G34 повторное окно после отказа закрывается', retry.close())

        # Фоновая медленная сеть не должна съедать реальную команду установки.
        slow_box = tm.Sandbox(work / 'install-while-checking', install)
        boxes.append(slow_box)
        slow = tm.SlowServer()
        try:
            waiting = slow_box.launch(new, fake, env.keys_file, slow.url)
            got = waiting.wait(lambda s: s.get('screen') == 'welcome' and s.get('update_state') == 'checking')
            result.check('G21 окно готово, проверка обновлений ещё идёт', got and slow.requested.wait(5), waiting.state())
            if got:
                install_command(waiting, result, 'G22 install при незавершённой проверке обновлений')
            result.check('G23 сессия медленной сети закрылась', waiting.close())
        finally:
            slow.stop()
        if full:
            full_handoff(work, old, new, install, key, result, fake, server, env, boxes)
    finally:
        for box in boxes:
            for session in box.sessions:
                session.stop()
        server.stop()
        tm.clean_test_key(work)
        result.details['source_after'] = source_hashes()
        result.details['sources_unchanged_during_run'] = result.details['source_before'] == result.details['source_after']
        result.save()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path)
    parser.add_argument('--compile-only', action='store_true')
    parser.add_argument('--full-handoff', action='store_true',
                        help='реальный signed download → дочерний TESTBUILD Inno → новый manager')
    parser.add_argument('--reuse-build-root', type=Path,
                        help='повтор сценариев на ранее закреплённых TESTBUILD EXE, без перекомпиляции')
    options = parser.parse_args()
    temp = Path(tempfile.gettempdir()).resolve()
    work = (options.work or Path(tempfile.mkdtemp(prefix='wxq-upgrade-regression-'))).resolve()
    if not work.is_relative_to(temp):
        raise RuntimeError('Проверка запускается только в Temp-песочнице')
    work.mkdir(parents=True, exist_ok=True)
    if tm.current_test_key_location() is not None:
        raise RuntimeError('Существует чужая TEST uninstall запись; она не изменяется')
    print('Песочница: ' + str(work), flush=True)
    result = Results(work)
    key = tm.rsa.generate_private_key(public_exponent=65537, key_size=3072)
    config = {'schema': 1, 'base_url': 'http://127.0.0.1:9/',
              'keys': [{'key_id': pr.key_id_of(key.public_key()), 'public_key_pem': tm.Env.pem(key)}]}
    if options.reuse_build_root:
        cached = options.reuse_build_root.resolve()
        if not cached.is_relative_to(temp):
            raise RuntimeError('Готовые TESTBUILD берутся только из Temp')
        prior = json.loads((cached / 'result.json').read_text('utf-8'))
        old = cached / ('build-' + OLD_VERSION) / 'WXQ-Manager-Test.exe'
        new = cached / ('build-' + NEW_VERSION) / 'WXQ-Manager-Test.exe'
        if sha(old) != prior['old_setup_sha256'] or sha(new) != prior['new_setup_sha256']:
            raise RuntimeError('SHA ранее собранных TESTBUILD изменились')
        install = json.loads((cached / ('build-' + NEW_VERSION) / 'staging/install.json').read_text('utf-8-sig'))
        result.details.update(reused_build_root=str(cached), compiled_source_sha256=prior['source_before'])
    else:
        old, _ = build_version(work, OLD_VERSION, config)
        new, install = build_version(work, NEW_VERSION, config)
    result.details.update(old_version=OLD_VERSION, new_version=NEW_VERSION, old_setup=str(old), new_setup=str(new),
                          old_setup_sha256=sha(old), new_setup_sha256=sha(new),
                          full_handoff_exercised=False, real_game_or_desktop_used=False)
    result.save()
    if options.compile_only:
        return 0
    try:
        runtime(work, old, new, install, key, result, options.full_handoff)
    except Exception as error:
        result.check('HARNESS исключение', False, repr(error))
        raise
    failures = [x['name'] for x in result.checks if not x['passed']]
    print(f'Итог: {len(result.checks)} проверок, {len(failures)} ошибок. Песочница: {work}', flush=True)
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
