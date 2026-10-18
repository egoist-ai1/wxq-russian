"""Проверка установщика на фиктивной игре во временной папке. Реальная игра только читается.

    python -X utf8 -B installer/tests/run_tests.py --installer <exe из пакетов 1, 4, 10, 19> [--partial-installer <exe только с пакетом 1>] \
        [--previous-installer <прежний exe, например 0.2.0 из пакетов 1, 10, 19 (tex2)>] [--work <папка>] [--keep]

Сценарии: установка четырёх пакетов, повторный запуск, удаление (пустые заготовки пакетов 4 и 10 возвращаются
байт-в-байт), обновление поверх прежней нашей версии, чужие данные в каждом пакете, несовпавшая версия,
запущенная игра, игра не найдена, автопоиск через файл лаунчера, откат при переписанном игрой файле,
два установщика подряд, состав установщика без лаунчера и Node, русский лаунчер (U: сборка с --launcher-build: файлы, флаг, ярлыки,
удаление, переключатель выключен, оригинал не найден; сборка без лаунчера ничего не ставит; формат ресурса expected).
Крупные prime-файлы (около 4 ГБ) читаются из настоящей игры один раз в work/prime-template и кладутся в песочницы жёсткими ссылками.
Установщик запускается тихо с /CURRENTUSER (без UAC), /GAMEDIR и /DATADIR во временной папке.
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[2]
INSTALLER_DIR = PROJECT / 'installer'
FIXTURES = Path(__file__).resolve().parent / 'fixtures'
REAL_GAME = Path(r'C:\Program Files\WXQLauncher\WXQ(1)')
# Кандидаты финальной сборки: пакет 1 (тексты), пакет 4 (вывеска «Новости»), пакет 10 (Lua-перенос слов), пакет 19 (картинки).
CARRIER = PROJECT / 'build/carrier-2026-10-14-v7'
NEWS = PROJECT / 'build/pilot-2026-10-13-tex3-pkg4'
LUA = PROJECT / 'build/lua-wrap-2026-10-14-v9'
TEXTURES = PROJECT / 'build/pilot-2026-10-13-tex4-pkg19'
OLD_TEXTURES = PROJECT / 'build/pilot-2026-10-13-tex2'  # прежняя наша версия пакета 19 (сценарий «обновление»)
LAUNCHER_BUILD = PROJECT / 'build/launcher-ru-2026-10-14-218b7012'  # перевод лаунчера для сценариев U
LAUNCHER_EXE_REL = Path('26083.0.7.30004') / '王者万象棋.exe'  # где у настоящего лаунчера оригинал; в песочнице лежит заглушка
CANDIDATES = (CARRIER, NEWS, LUA, TEXTURES)
PACKAGES = ('1', '4', '10', '19')
CAND_BY_PACKAGE = dict(zip(PACKAGES, CANDIDATES))
BIG_FILE = 16 * 1024 * 1024  # prime крупнее: в песочницу кладётся жёсткая ссылка на копию в work (копия одна)
OLD_CARRIER = PROJECT / 'build/carrier-2026-10-13-42a4bb'  # прежняя наша версия пакета 1 (для сценария «обновление»)
KEY_PREFIX = 'QtsDataFolder\\QtsVFSCache\\'  # ключи снимка папки игры
FORBIDDEN = re.compile(r'(^node(\.exe)?$|\.(mjs|cjs|asar)$|^launcher-entry|^artifacts?$)', re.IGNORECASE)


def sha(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def read_manifest(directory: Path) -> dict:
    return json.loads((directory / 'manifest.json').read_text('utf-8-sig'))


def from_rollback(relative: str, prefix: str) -> Path:
    """Нетронутый override-каталог пакета из резервной копии прежнего развёртывания."""
    for path in sorted((PROJECT / 'rollback/game-layer').glob('deployment-*/receipt.json')):
        receipt = json.loads(path.read_text('utf-8-sig'))
        for row in receipt['operations']:
            if row['relative'] == relative and (row.get('before_sha256') or '').startswith(prefix):
                backup = path.parent / row['backup_relative']
                if backup.is_file() and sha(backup).startswith(prefix):
                    return backup
    raise SystemExit(f'не найден нетронутый {relative} в rollback/')


def pristine_catalog() -> Path:
    return from_rollback('packages/1/1.db', '215a300d')


def pristine_19() -> Path:
    return from_rollback('packages/19/19.db', '6b8beb48')


def placeholder_10() -> Path:
    """Пустая заготовка packages/10/10.db (114688 байт, создана игрой 10.10): предшественник файла пакета 10."""
    return FIXTURES / '10.db.placeholder'


def placeholder_4() -> Path:
    """Пустая заготовка packages/4/4.db (114688 байт, создана игрой 10.10; в настоящей игре у пакета 4 перевода нет)."""
    return FIXTURES / '4.db.placeholder'


class Sandbox:
    template: Path | None = None  # единственная копия крупных prime-файлов настоящей игры (реальная игра только читается)
    hashes: dict = {}  # кэш SHA крупных (неизменяемых) файлов: (путь, размер, mtime) -> SHA

    @classmethod
    def prepare_template(cls, base: Path) -> None:
        cls.template = base
        needed = set()
        for directory in CANDIDATES:
            needed.update(row['relative'] for row in read_manifest(directory)['source_copies'])
        for relative in sorted(needed):
            source = REAL_GAME / 'WXQ_Data/Package/builtin' / relative
            target = base / relative
            if source.stat().st_size > BIG_FILE and not (target.is_file() and target.stat().st_size == source.stat().st_size):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)

    def __init__(self, root: Path):
        self.root = root
        self.launcher = root / 'Launcher'
        self.game = self.launcher / 'WXQ(1)'
        self.data = root / 'data'
        self.app = root / 'app'  # {app} установщика: помощник и деинсталлятор (данные и копии — в data)
        self.log = root / 'inno.log'
        self.cache = self.game / 'QtsDataFolder/QtsVFSCache/packages'
        self.secparent = root.parent / (root.name + '-sec')  # куда установщик кладёт свою защищённую папку (тесты не трогают настоящий ProgramData)

    def build(self, ours: bool = False) -> None:
        """Чистая игра: пакет 1 с нетронутым каталогом, пакеты 4 и 10 с пустой заготовкой, пакет 19 с нетронутым каталогом.
        ours=True: пакет 1 уже содержит прежнюю нашу версию (carrier-42a4bb)."""
        shutil.rmtree(self.root, ignore_errors=True)
        game = self.game
        builtin = game / 'WXQ_Data/Package/builtin'
        prime = set()
        for directory in CANDIDATES:
            prime.update(row['relative'] for row in read_manifest(directory)['source_copies'])
        for relative in sorted(prime):
            (builtin / relative).parent.mkdir(parents=True, exist_ok=True)
            if self.template is not None and (self.template / relative).is_file():
                os.link(self.template / relative, builtin / relative)
            else:
                shutil.copyfile(REAL_GAME / 'WXQ_Data/Package/builtin' / relative, builtin / relative)
        (game / 'WXQ.exe').write_bytes(b'stub')
        plugins = game / 'WXQ_Data/Plugins/x86_64'
        plugins.mkdir(parents=True)
        for name in ('QtsVFS.dll', 'QtskDB.dll'):
            shutil.copyfile(REAL_GAME / 'WXQ_Data/Plugins/x86_64' / name, plugins / name)
        vfs = game / 'QtsDataFolder/QtsVFSCache'
        (vfs / 'packages/builtin/Globalindex').mkdir(parents=True)
        shutil.copyfile(REAL_GAME / 'QtsDataFolder/QtsVFSCache/packages/builtin/Globalindex/GlobalIndexPrime.data', vfs / 'packages/builtin/Globalindex/GlobalIndexPrime.data')
        lines = [l for l in (REAL_GAME / 'QtsDataFolder/QtsVFSCache/QtsVFS.log').read_text('utf-8', errors='replace').splitlines() if 'MountPackage' in l and ', prime,' in l]
        (vfs / 'QtsVFS.log').write_text('\n'.join(lines) + '\n', 'utf-8')
        real_cache = REAL_GAME / 'QtsDataFolder/QtsVFSCache/packages'
        for name in ('0', '10001', '1010000000'):  # посторонние пакеты: установщик не должен их трогать
            shutil.copytree(real_cache / name, self.cache / name)
        for package, directory in CAND_BY_PACKAGE.items():
            (self.cache / package).mkdir()
            shutil.copyfile(directory / f'packages/{package}/{package}.info', self.cache / f'{package}/{package}.info')
        shutil.copyfile(placeholder_4(), self.cache / '4/4.db')
        shutil.copyfile(placeholder_10(), self.cache / '10/10.db')
        shutil.copyfile(pristine_19(), self.cache / '19/19.db')
        if ours:
            shutil.copyfile(OLD_CARRIER / 'packages/1/1.db', self.cache / '1/1.db')
            shutil.copyfile(OLD_CARRIER / 'packages/1/1_0.db', self.cache / '1/1_0.db')
        else:
            shutil.copyfile(pristine_catalog(), self.cache / '1/1.db')

    def snapshot(self) -> dict[str, str]:
        result = {}
        for path in sorted(self.game.rglob('*')):
            if path.is_file():
                info = path.stat()
                if info.st_size > BIG_FILE:  # крупные prime в тестах не переписываются: хеш считаем один раз на (путь, размер, время)
                    token = (str(path), info.st_size, info.st_mtime_ns)
                    if token not in self.hashes:
                        self.hashes[token] = sha(path)
                    result[str(path.relative_to(self.game))] = self.hashes[token]
                else:
                    result[str(path.relative_to(self.game))] = sha(path)
        return result

    def outside_game(self) -> dict[str, str]:
        """Всё в папке лаунчера, кроме самой игры (ярлыки, файлы лаунчера)."""
        result = {}
        for path in sorted(self.launcher.rglob('*')):
            if path.is_file() and self.game not in path.parents:
                result[str(path.relative_to(self.launcher))] = sha(path)
        return result

    def run(self, installer: Path, extra: list[str] | None = None, env: dict | None = None, game: bool = True) -> subprocess.CompletedProcess:
        command = [str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', f'/DIR={self.app}', f'/DATADIR={self.data}', f'/LOG={self.log}', f'/SECPARENT={self.secparent}']
        if game:
            command.append(f'/GAMEDIR={self.game}')
        command += extra or []
        full = dict(os.environ)
        # Жёсткая изоляция: помощник не должен видеть настоящие профили Windows и настоящую игру.
        full['WXQ_RU_USERS_ROOT'] = str(self.root / 'no-users')
        full['WXQ_RU_TRUST_ROOT'] = str(self.launcher)   # тестовая сборка помощника считает «Program Files» песочницей
        full['WXQ_RU_TEST_CLOSE_ROOT'] = str(self.root)   # автозакрытие (0.6.1) видит только заглушки песочницы, настоящая игра на этом ПК не затрагивается
        full['MSYS2_ARG_CONV_EXCL'] = '*'
        full.update({k.upper(): v for k, v in (env or {}).items()})  # имена переменных в Windows без учёта регистра
        return subprocess.run(command, capture_output=True, env=full, timeout=300)

    def uninstall(self) -> subprocess.CompletedProcess:
        exe = self.app / 'unins000.exe'
        completed = subprocess.run([str(exe), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'], capture_output=True, timeout=300)
        # Деинсталлятор Inno отдаёт управление после запуска второй фазы: ждём исчезновения exe.
        for _ in range(120):
            if not exe.exists():
                break
            time.sleep(0.5)
        return completed

    def install_log(self) -> str:
        path = self.data / 'install.log'
        return path.read_text('utf-8-sig', errors='replace') if path.is_file() else ''

    def receipts(self) -> list[Path]:
        return sorted((self.data / 'backup').glob('*/receipt.json')) if (self.data / 'backup').is_dir() else []


results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = '') -> bool:
    results.append((name, bool(condition), detail))
    print(('  ok   ' if condition else '  FAIL ') + name + (('  [' + detail + ']') if detail and not condition else ''))
    return bool(condition)


def rows_ok(receipt: dict, relative: str, existed: bool) -> bool:
    return any(r['relative'] == relative and r['before_exists'] is existed for r in receipt['operations'])


def key(relative: str) -> str:
    return KEY_PREFIX + relative.replace('/', '\\')


def candidate_hashes(*directories: Path) -> dict[str, str]:
    result = {}
    for directory in directories:
        result.update({row['relative']: row['sha256'] for row in read_manifest(directory)['files']})
    return result


def flip(path: Path, offset: int) -> None:
    data = bytearray(path.read_bytes())
    data[offset] ^= 0xFF
    path.write_bytes(bytes(data))


def real_state() -> list[tuple[str, int, int]]:
    """Размеры и времена изменения файлов настоящей игры, которых касается перевод, и состав папки лаунчера."""
    rows = []
    cache = REAL_GAME / 'QtsDataFolder/QtsVFSCache/packages'
    for package in PACKAGES:
        for path in sorted((cache / package).glob('*')):
            info = path.stat()
            rows.append((str(path), info.st_size, info.st_mtime_ns))
    for path in sorted(REAL_GAME.parent.glob('*')):
        rows.append((str(path), 0, 0))
    return rows


TEST_MARK = 'тестовая сборка'.encode('utf-16-le')  # метка в ресурсе версии exe, которую ставит только /DTESTBUILD


def build_variant(work: Path, name: str, candidates: tuple[Path, ...], launcher: bool = False) -> Path:
    """Тестовая сборка установщика (build_installer.py --test-build): с ней /CURRENTUSER работает в командной строке."""
    output = work / 'test-installers' / f'{name}.exe'
    if output.is_file():
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-X', 'utf8', '-B', str(INSTALLER_DIR / 'build_installer.py'), '--test-build', '--version', '0.3.1',
               '--game-root', str(REAL_GAME), '--output', str(output)]
    for directory in candidates:
        command += ['--candidate', str(directory)]
    if launcher:
        command += ['--launcher-build', str(LAUNCHER_BUILD)]
    done = subprocess.run(command, capture_output=True, timeout=900)
    if done.returncode or not output.is_file():
        raise SystemExit('не собрался тестовый установщик ' + name + ':\n' + (done.stdout + done.stderr).decode('utf-8', errors='replace')[-1500:])
    return output


def build_dump(work: Path, name: str, test_build: bool) -> tuple[Path, str]:
    """Сборка pkg1 с --dump-script: (установщик, скрипт .iss после препроцессора ISPP). Релиз собирается только ради компиляции и статического анализа, не запускается."""
    folder = work / 'sec-dump'
    folder.mkdir(parents=True, exist_ok=True)
    output, dump = folder / f'{name}.exe', folder / f'{name}.iss'
    if not output.is_file():
        command = [sys.executable, '-X', 'utf8', '-B', str(INSTALLER_DIR / 'build_installer.py'), '--version', '0.3.1', '--game-root', str(REAL_GAME), '--output', str(output),
                   '--dump-script', str(dump), '--candidate', str(CARRIER)] + (['--test-build'] if test_build else [])
        done = subprocess.run(command, capture_output=True, timeout=900)
        if done.returncode or not output.is_file() or not dump.is_file():
            raise SystemExit('не собрался установщик ' + name + ':\n' + (done.stdout + done.stderr).decode('utf-8', errors='replace')[-1500:])
    return output, dump.read_text('utf-8-sig')


class Tool:
    """WXQTool.exe напрямую (без мастера): staging с данными перевода, команды backup/unpack/verify/restore/status."""

    def __init__(self, base: Path):
        import build_installer as bi
        install, files = bi.combine(list(CANDIDATES), 'tool', '2026-01-01')
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True)
        bi.make_staging(install, files, base, test_build=True)   # тестовый помощник: корень доверия лаунчера задаёт WXQ_RU_TRUST_ROOT
        for source, flat in files:
            shutil.copyfile(source, base / flat)
        self.dir = base
        self.env: dict[str, str] = {}   # переменные поверх обычных (корень доверия лаунчера для негативных проверок)
        self.exe = base / 'WXQTool.exe'

    def run(self, box: 'Sandbox', command: str, **extra: str) -> dict[str, str]:
        out = box.root / 'tool-out.txt'
        out.unlink(missing_ok=True)
        args = [str(self.exe), command, '--payload', str(self.dir), '--data', str(box.data), '--out', str(out), '--game', str(box.game)]
        for name, value in extra.items():
            args += ['--' + name, value]
        env = dict(os.environ, WXQ_RU_USERS_ROOT=str(box.root / 'no-users'), WXQ_RU_TRUST_ROOT=str(box.launcher))
        env.update(self.env)
        subprocess.run(args, capture_output=True, env=env, timeout=300)
        rows = {}
        if out.is_file():
            for line in out.read_text('utf-8-sig').splitlines():
                name, _, value = line.partition('=')
                rows[name] = value
        return rows


def tree(path: Path) -> dict[str, str]:
    """SHA всех файлов каталога без захода в ссылки."""
    result = {}
    for folder, _dirs, names in os.walk(path, followlinks=False):
        for name in names:
            file = Path(folder) / name
            result[str(file.relative_to(path))] = sha(file)
    return result


def junction(link: Path, target: Path) -> None:
    done = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True)
    if done.returncode:
        raise SystemExit('не создалась junction: ' + done.stdout.decode('cp866', errors='replace'))


def ru_flag():
    """Значение LauncherRU в HKCU\\SOFTWARE\\Egoist\\WXQ-Russian (64-битный вид) или None. Тестовая сборка с /CURRENTUSER пишет в HKCU (HKA), HKLM тесты не трогают."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'SOFTWARE\Egoist\WXQ-Russian', 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as handle:
            return winreg.QueryValueEx(handle, 'LauncherRU')
    except FileNotFoundError:
        return None


UNINSTALL_PATH = r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
TEST_UNINSTALL_KEY = '{B3E1D8A4-6F21-4C7B-9A0E-5D2C8F4E7A19}-TEST_is1'   # ключ тестовой сборки; ключ настоящего AppId тесты не трогают


def uninstall_keys() -> set[str]:
    """Имена подразделов Uninstall в HKCU (64- и 32-битный вид): тесты не должны менять этот набор."""
    import winreg
    names = set()
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_PATH, 0, winreg.KEY_READ | view) as handle:
                index = 0
                while True:
                    try:
                        names.add(f'{view}:{winreg.EnumKey(handle, index)}')
                    except OSError:
                        break
                    index += 1
        except FileNotFoundError:
            pass
    return names


def cleanup_test_uninstall_key() -> None:
    """Удаляет ТОЛЬКО ключ тестовой сборки (AppId с суффиксом -TEST); срабатывает и при аварийном выходе из тестов."""
    import winreg
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_PATH + '\\' + TEST_UNINSTALL_KEY, view, 0)
        except OSError:
            pass


atexit.register(cleanup_test_uninstall_key)


def check_uninstall_unchanged(before: set[str]) -> None:
    cleanup_test_uninstall_key()
    after = uninstall_keys()
    check('Z3 набор ключей Uninstall в HKCU не изменился за время тестов (тестовый ключ -TEST убран, настоящий AppId не тронут)',
          after == before, f'добавлено: {sorted(after - before)}; пропало: {sorted(before - after)}')


def acl_rows(path: Path) -> tuple[bool, list[tuple[str, str, str]]]:
    """(наследование отключено?, [(SID, права, унаследовано?)]) по Get-Acl; SID, а не имена: имена зависят от языка Windows."""
    script = ("$a = Get-Acl -LiteralPath '" + str(path).replace("'", "''") + "'; 'P=' + $a.AreAccessRulesProtected; "
              "$a.Access | ForEach-Object { 'R=' + $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value + '|' + $_.FileSystemRights + '|' + $_.IsInherited }")
    done = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True, timeout=60)
    lines = done.stdout.decode('utf-8', errors='replace').splitlines()
    protected = any(l.strip() == 'P=True' for l in lines)
    rows = [tuple(l.strip()[2:].split('|')) for l in lines if l.strip().startswith('R=')]
    return protected, rows


def run_secure_folder_checks(work: Path, box: 'Sandbox', pkg1_exe: Path, bi) -> None:
    """Защищённая папка установщика (ui\\PROTOCOL.md): код, релизный скрипт после препроцессора, отказы (хеш, ссылка, чужая папка), чистка остатков."""
    print('S. защищённая папка установщика: код, релизный скрипт, SHA-256 копий, ссылки, чистка остатков')
    iss = (INSTALLER_DIR / 'WXQRussian.iss').read_text('utf-8-sig')
    sid_pattern = r'\*S-\d+(?:-\d+)+'
    check('S1 iss: права выдаются по SID ровно двум субъектам (Администраторы S-1-5-32-544, SYSTEM S-1-5-18) с OI CI и полным доступом; наследование отключается и выдаётся заново',
          "SecGrantAdmins = '*S-1-5-32-544:(OI)(CI)F'" in iss and "SecGrantSystem = '*S-1-5-18:(OI)(CI)F'" in iss and "'\"' + SecDir + '\" /inheritance:r /grant:r ' + Grant" in iss and
          set(re.findall(sid_pattern, iss)) == {'*S-1-5-32-544', '*S-1-5-18'})
    grant_at = iss.index('GetUserNameString')
    check('S2 iss: выдача прав текущему пользователю (GetUserNameString) единственная и стоит только внутри #ifdef TestBuild',
          iss.count('GetUserNameString') == 1 and iss.rfind('#ifdef TestBuild', 0, grant_at) > iss.rfind('#endif', 0, grant_at))
    check('S3 iss: помощник, окно и файлы обмена запускаются и пишутся только в защищённой папке; {tmp} - лишь источник копирования',
          "Result := SecDir + '\\{#ToolExe}'" in iss and "Exe := SecDir + '\\WXQSetupUI.exe'" in iss and "Dir := SecDir;" in iss and iss.count("--payload \"' + SecDir +") == 2 and
          "OutFile := SecDir + '\\tool-out.txt'" in iss and "ProgFile := SecDir + '\\tool-progress.txt'" in iss and "--payload \"' + RemoveBackslashUnlessRoot(ExpandConstant('{tmp}'))" not in iss and
          iss.count("ExpandConstant('{tmp}\\tool-out.txt')") == 0 and iss.count("ExpandConstant('{tmp}\\{#ToolExe}')") == 1 and iss.count("ExpandConstant('{tmp}\\WXQSetupUI.exe')") == 1)
    check('S4 iss: сверка SHA-256 именно копии (GetSHA256OfFile от целевого пути), несовпадение - удаление копии и отказ',
          'Actual := GetSHA256OfFile(Target);' in iss and 'CompareText(Actual, Expected) <> 0' in iss and iss.count('SecCopyFile(') >= 5)
    check('S5 iss: родитель не ссылка, имя уникально (существующая папка или файл - отказ), папка создаётся CreateDir, а не ForceDirectories; ACL и владелец читаются обратно и проверяются; в папке до копирования пусто',
          'SecIsReparse(Parent)' in iss and 'DirExists(Dir) or FileExists(Dir)' in iss and 'CreateDir(Dir)' in iss and 'SecAclLocked(Sddl, SecExtra)' in iss and 'SecOwnerOk(SecDir)' in iss and 'SecIsEmptyDir(SecDir)' in iss)
    check('S5a iss: папка создаётся прямо в %ProgramData% ({commonappdata}), родитель мастер не создаёт (CreateDir только для самой папки), старого родителя Egoist в защищённом пути нет',
          "RemoveBackslashUnlessRoot(ExpandConstant('{commonappdata}'))" in iss and 'CreateDir(Parent)' not in iss and "ExpandConstant('{commonappdata}\\Egoist')" not in iss and iss.count('CreateDir(') == 1)
    check('S5b iss: проверка папки (ссылка, ACL, владелец Администраторы/SYSTEM) повторяется после создания, после копирования и перед каждым запуском помощника и окна',
          iss.count('SecVerify(') >= 6 and all(f"SecVerify('{w}')" in iss for w in ('после создания', 'после копирования', 'перед запуском помощника', 'перед запуском окна')) and
          'IsWellKnownSid(Owner, 26)' in iss and 'IsWellKnownSid(Owner, 22)' in iss)
    check('S6 iss: чистка остатков только внутри родителя, по точному имени WXQ-setup-<32 hex>, не ссылки, старше суток',
          'SecIsOurName(FR.Name)' in iss and 'not SecIsReparse(Path)' in iss and 'SecOwnerOk(Path)' in iss and 'SecIsOld(Path)' in iss and 'FindFirst(Parent + ' in iss)
    check('S7 iss: код возврата icacls проверяется, повышенный мастер при неудаче отказывается работать (InitializeSetup возвращает False)',
          'and (Code = 0)' in iss and 'if not SecPrepare then' in iss and 'Result := False;' in iss[iss.index('if not SecPrepare then'):][:900])

    release_exe, release = build_dump(work, 'release', False)
    test_exe, test_script = build_dump(work, 'test', True)
    check('S10 релизный скрипт (после препроцессора): SID только S-1-5-32-544 и S-1-5-18, обе записи выдачи на месте',
          set(re.findall(sid_pattern, release)) == {'*S-1-5-32-544', '*S-1-5-18'} and "SecGrantAdmins = '*S-1-5-32-544:(OI)(CI)F'" in release and "SecGrantSystem = '*S-1-5-18:(OI)(CI)F'" in release)
    forbidden = ('GetUserNameString', 'USERDOMAIN', 'SECPARENT', 'UISTATEDIR', 'UIEXE', 'TOOLSHA', 'SECOWNER', 'SecExtra := 1')
    code_only = '\n'.join(line for line in release.splitlines() if not line.lstrip().startswith((';', '//')))   # слова в комментариях не в счёт
    check('S11 релизный скрипт НЕ содержит выдачи прав текущему пользователю и тестовых переключателей (ни в коде, ни в #ifdef)', not [word for word in forbidden if word in code_only],
          str([word for word in forbidden if word in code_only]))
    check('S12 тестовый скрипт (контроль проверки) содержит выдачу прав пользователю и переключатели', all(word in test_script for word in ('GetUserNameString', 'SECPARENT', 'UISTATEDIR', 'SECOWNER', 'SecExtra := 1')))
    check('S13 релиз: подставлены эталонные SHA-256 помощника, install.json и окна (64 hex)',
          all(re.search(pattern, release) for pattern in (r"ToolSha := '[0-9a-f]{64}'", r"SecCopyFile\(ExpandConstant\('\{tmp\}\\install.json'\), 'install.json', '[0-9a-f]{64}'\)", r"Expected := '[0-9a-f]{64}'")))
    check('S14 релизная сборка не несёт метки тестовой (/CURRENTUSER не разрешён)', TEST_MARK not in release_exe.read_bytes() and TEST_MARK in test_exe.read_bytes())

    root = work / 'sec-cases'
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    box.build()

    def attempt(extra: list[str]) -> tuple[int, str]:
        box.log.unlink(missing_ok=True)
        done = box.run(pkg1_exe, extra)
        return done.returncode, box.log.read_text('utf-8-sig', errors='replace') if box.log.is_file() else ''

    before = box.snapshot()
    code, log = attempt([f'/UISTATEDIR={root / "tool-bad"}', '/TOOLSHA=' + '0' * 64])
    check('S20 чужой помощник (SHA-256 копии не совпал): установщик отказывается, помощник не запускался, папка удалена',
          code != 0 and 'не совпал с эталоном сборки' in log and 'Tool:' not in log and not (root / 'tool-bad').exists(), f'{code} {log[-400:]}')
    check('S20a игра не тронута', box.snapshot() == before)

    target = root / 'junction-target'
    target.mkdir()
    link = root / 'junction-parent'
    junction(link, target)
    code, log = attempt([f'/UISTATEDIR={link / "state"}'])
    check('S21 родитель - junction: отказ, в цель ссылки ничего не создано', code != 0 and 'является ссылкой' in log and not list(target.iterdir()) and 'Tool:' not in log, f'{code} {log[-300:]}')

    existing = root / 'existing'
    existing.mkdir()
    (existing / 'sentinel.txt').write_text('x')
    code, log = attempt([f'/UISTATEDIR={existing}'])
    check('S22 папка с таким именем уже есть: отказ, чужое содержимое не тронуто и не удалено',
          code != 0 and 'уже существует' in log and (existing / 'sentinel.txt').read_text() == 'x' and sorted(p.name for p in existing.iterdir()) == ['sentinel.txt'], f'{code} {log[-300:]}')

    empty_pre = root / 'precreated-empty'
    empty_pre.mkdir()
    code, log = attempt([f'/UISTATEDIR={empty_pre}'])
    check('S22a заранее созданная обычным пользователем ПУСТАЯ папка с тем же именем: отказ, она не принята и не перенастроена, помощник не запускался',
          code != 0 and 'уже существует' in log and empty_pre.is_dir() and not list(empty_pre.iterdir()) and 'Tool:' not in log, f'{code} {log[-300:]}')

    code, log = attempt([f'/UISTATEDIR={root / "owner-bad"}', '/SECOWNER=SY'])
    check('S24 владелец папки не Администраторы/SYSTEM (тест: допустим только SYSTEM): отказ, помощник не запускался, папка удалена',
          code != 0 and 'не Администраторы и не SYSTEM' in log and 'неверный владелец' in log and 'Tool:' not in log and not (root / 'owner-bad').exists(), f'{code} {log[-400:]}')

    target2 = root / 'junction-target2'
    target2.mkdir()
    named = root / 'named-junction'
    junction(named, target2)
    code, log = attempt([f'/UISTATEDIR={named}'])
    check('S23 имя защищённой папки занято ссылкой: отказ, цель ссылки пуста', code != 0 and 'уже существует' in log and not list(target2.iterdir()), f'{code} {log[-300:]}')

    clean = root / 'clean'
    clean.mkdir()
    victim, victim2 = root / 'victim', root / 'victim2'
    for folder in (victim, victim2):
        folder.mkdir()
        (folder / 'keep.txt').write_text('keep')
    old_time = time.time() - 3 * 86400
    old_ok = clean / ('WXQ-setup-' + 'a' * 32)         # наша старая папка, внутри ссылка на чужую папку
    old_ok.mkdir()
    (old_ok / 'state.json').write_text('{}')
    junction(old_ok / 'inner-link', victim)
    old_link = clean / ('WXQ-setup-' + 'b' * 32)       # имя наше, но это ссылка
    junction(old_link, victim2)
    short_name = clean / ('WXQ-setup-' + 'c' * 31)     # имя не по шаблону
    upper_name = clean / ('WXQ-setup-' + 'D' * 32)
    foreign = clean / ('Other-' + 'e' * 32)
    fresh = clean / ('WXQ-setup-' + 'f' * 32)
    for folder in (short_name, upper_name, foreign, fresh):
        folder.mkdir()
        (folder / 'x.txt').write_text('x')
    for folder in (old_ok, short_name, upper_name, foreign):
        os.utime(folder, (old_time, old_time))
    code, log = attempt([f'/UISTATEDIR={clean / ("WXQ-setup-" + "1" * 32)}'])
    check('S30 остатки старше суток с точным именем удалены, ссылка внутри них не разыменована (чужая папка цела)',
          not old_ok.exists() and (victim / 'keep.txt').read_text() == 'keep' and 'Удаляю остатки прежней установки' in log, f'{code} {log[-300:]}')
    check('S31 имя-ссылка, имя не по шаблону, чужое имя и свежая папка не тронуты',
          old_link.exists() and (victim2 / 'keep.txt').read_text() == 'keep' and short_name.is_dir() and upper_name.is_dir() and foreign.is_dir() and (fresh / 'x.txt').is_file())
    check('S32 своя защищённая папка после завершения удалена', not (clean / ('WXQ-setup-' + '1' * 32)).exists() and 'Защищённая папка удалена' in log)
    owner_clean = root / 'owner-clean'
    owner_clean.mkdir()
    foreign_owner = owner_clean / ('WXQ-setup-' + 'a' * 32)
    foreign_owner.mkdir()
    (foreign_owner / 'x.txt').write_text('x')
    os.utime(foreign_owner, (old_time, old_time))
    code, log = attempt([f'/UISTATEDIR={owner_clean / ("WXQ-setup-" + "2" * 32)}', '/SECOWNER=SY'])
    check('S34 старая папка с нужным именем, но владелец не Администраторы/SYSTEM: чистка её не трогает', foreign_owner.is_dir() and (foreign_owner / 'x.txt').is_file() and 'Удаляю остатки' not in log, f'{code} {log[-300:]}')
    for junction_path in (link, named, old_link, old_ok / 'inner-link'):
        if junction_path.exists():
            os.rmdir(junction_path)
    left = sorted(p.name for p in box.secparent.glob('WXQ-setup-*')) if box.secparent.is_dir() else []
    check('S33 после всех запусков тестов в родителе защищённых папок не осталось ни одной', not left, str(left))


def run_review_checks(work: Path, box: 'Sandbox') -> None:
    """Правки повторного ревью: место на диске, владелец и права каталога данных, отмена, очистка, свойства релизного скрипта."""
    print('V. повторное ревью: место, права каталога данных, отмена, очистка, релизный скрипт')
    iss = (INSTALLER_DIR / 'WXQRussian.iss').read_text('utf-8-sig')
    _release_exe, release = build_dump(work, 'release', False)
    code_only = '\n'.join(line for line in release.splitlines() if not line.lstrip().startswith((';', '//')))
    leaked = [word for word in ('DATADIR', 'FREEBYTES') if word in code_only]
    check('V1 релиз: DATADIR и FREEBYTES не читаются (только под #ifdef TestBuild)', not leaked, str(leaked))
    check('V2 SetupMutex общий для сеансов (Global\\)', 'SetupMutex=Egoist.WXQRussian.Setup,Global\\Egoist.WXQRussian.Setup' in release)
    check('V3 релиз: папка установки проверяется (AppDirOk в DoInstall и InitializeUninstall)', release.count('AppDirOk') >= 3 and 'AppDirRefusal' in release)
    check('V4 restore-out.txt и purge-out.txt лежат в {app}, а не в {tmp} и не в каталоге данных', "ExpandConstant('{app}') + '\\restore-out.txt'" in iss and "UninstDataDir + '\\restore-out.txt'" not in iss
          and "UninstDataDir + '\\purge-out.txt'" not in iss and '{tmp}\\restore-out.txt' not in iss)
    check('V5 релиз: PayloadBytes задан', 'PayloadSpaceOk' in release and (INSTALLER_DIR / 'build_installer.py').read_text('utf-8-sig').count('PayloadBytes') >= 1)
    check('V6 остановка помощника по тайм-ауту без принудительного завершения: ToolStopped и флаг .cancel, без TerminateProcess', 'ToolStopped(' in iss and '.cancel' in iss and 'TerminateProcess' not in iss)
    check('V7 очистка данных при удалении программы идёт через помощника (purge)', 'purge' in iss)

    tool = Tool(work / 'tool-review')
    box.build()
    before = box.snapshot()
    tool.env = {'WXQ_RU_TEST_FREE_BYTES': '1000'}
    made = tool.run(box, 'backup')
    check('V10 backup при нехватке места: disk_space, «ничего не записано», расписок нет',
          made.get('code') == 'disk_space' and 'ничего не записано' in made.get('message', '').lower() and not box.receipts(), str(made))
    check('V10a игра не тронута', box.snapshot() == before)
    tool.env = {}
    made = tool.run(box, 'backup')
    label = made.get('label', '')
    check('V11 backup при достаточном месте проходит', made.get('status') == 'ok' and label != '', str(made))
    tool.env = {'WXQ_RU_TEST_FREE_BYTES': '1000'}
    failed = tool.run(box, 'unpack', label=label)
    check('V12 unpack при нехватке места: disk_space до первой записи, игра не тронута', failed.get('code') == 'disk_space' and box.snapshot() == before, str(failed))
    tool.env = {}
    removed = tool.run(box, 'restore')
    check('V13 расписка без записи откатывается', removed.get('status') == 'ok' and box.snapshot() == before, str(removed))

    # Отмена: файл <progress>.cancel останавливает помощника кодом cancelled, расписки нет.
    progress = box.root / 'cancel-progress.txt'
    flag = progress.with_name(progress.name + '.cancel')
    receipts_before = len(box.receipts())
    flag.write_text('1')
    cancelled = tool.run(box, 'backup', progress=str(progress))
    check('V14 флаг .cancel: backup останавливается кодом cancelled, ничего не записано', cancelled.get('code') == 'cancelled' and box.snapshot() == before and len(box.receipts()) == receipts_before, str(cancelled))
    flag.unlink(missing_ok=True)

    # Права: чужое разрешение на запись на файле внутри каталога данных - отказ acl, а не молчаливое исправление.
    made = tool.run(box, 'backup')
    check('V20 backup для проверки прав', made.get('status') == 'ok', str(made))
    receipts = box.receipts()
    if receipts:
        done = subprocess.run(['icacls', str(receipts[0]), '/grant', '*S-1-5-32-545:(M)'], capture_output=True, timeout=60)
        refused = tool.run(box, 'backup')
        check('V21 чужое право на изменение расписки (Users:M): отказ acl', done.returncode == 0 and refused.get('code') == 'acl', str(refused))
        subprocess.run(['icacls', str(receipts[0]), '/remove:g', '*S-1-5-32-545'], capture_output=True, timeout=60)
    for scope, name in (('dir', 'каталога данных'), ('parent', 'родителя')):
        tool.env = {'WXQ_RU_TEST_STRICT_OWNER': scope}
        refused = tool.run(box, 'backup')
        check(f'V22 владелец {name} не доверенный (область {scope}): отказ acl', refused.get('code') == 'acl', str(refused))
    tool.env = {}
    tool.run(box, 'restore')

    # Чужой владелец пустого каталога данных: отказ acl, владение не забирается, в каталог ничего не пишется (ни журнал, ни --out).
    for variant in ('out-outside', 'out-inside'):
        foreign = box.root / ('data-foreign-' + variant)
        shutil.rmtree(foreign, ignore_errors=True)
        foreign.mkdir(parents=True)
        out = box.root / 'tool-out-foreign.txt' if variant == 'out-outside' else foreign / 'out.txt'
        out.unlink(missing_ok=True)
        acl_before = subprocess.run(['icacls', str(foreign)], capture_output=True, timeout=60).stdout
        env = dict(os.environ, WXQ_RU_USERS_ROOT=str(box.root / 'no-users'), WXQ_RU_TRUST_ROOT=str(box.launcher), WXQ_RU_TEST_STRICT_OWNER='dir')
        subprocess.run([str(tool.exe), 'backup', '--payload', str(tool.dir), '--data', str(foreign), '--out', str(out), '--game', str(box.game)], capture_output=True, env=env, timeout=300)
        rows = dict(line.partition('=')[::2] for line in out.read_text('utf-8-sig').splitlines()) if out.is_file() and variant == 'out-outside' else {}
        acl_after = subprocess.run(['icacls', str(foreign)], capture_output=True, timeout=60).stdout
        check(f'V25 пустой каталог данных с чужим владельцем ({variant}): каталог остался пустым (нет install.log, state, backup)', not list(foreign.iterdir()), str(list(foreign.iterdir())))
        check(f'V25a ({variant}): права каталога не менялись, владение не забрано', acl_before == acl_after)
        if variant == 'out-outside':
            check('V25b --out вне каталога данных: отказ code=acl', rows.get('code') == 'acl' and rows.get('status') == 'stop', str(rows))
        else:
            check('V25c --out внутри недоверенного каталога данных после отказа не пишется', not out.exists())
        shutil.rmtree(foreign, ignore_errors=True)
    check('V24 после откатов игра в исходном состоянии', box.snapshot() == before)

    # purge: --keep 1 оставляет резервные копии, без него убирает.
    box.build()
    tool.run(box, 'backup')
    tool.run(box, 'restore')
    kept = tool.run(box, 'purge', keep='1')
    left_kept = (box.data / 'backup').exists()
    check('V30 purge --keep 1: ok, резервные копии остались', kept.get('status') == 'ok' and left_kept, str(kept))
    purged = tool.run(box, 'purge')
    check('V31 purge: ok, резервных копий нет', purged.get('status') == 'ok' and not (box.data / 'backup').exists(), str(purged))
    shutil.rmtree(work / 'tool-review', ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--installer', type=Path, help='готовая ТЕСТОВАЯ сборка (build_installer.py --test-build) из пакетов 1, 4, 10, 19; без параметра тесты собирают её сами')
    parser.add_argument('--release-installer', type=Path, help='релизная сборка: проверяется, что в ней нет метки тестовой сборки (то есть /CURRENTUSER и /ALLUSERS в командной строке не разрешены)')
    parser.add_argument('--partial-installer', type=Path, help='тестовая сборка только с пакетом 1 (сценарий K); без параметра собирается сама')
    parser.add_argument('--previous-installer', type=Path, help='прежний установщик (0.2.0: пакеты 1, 10, 19 из tex2): включает сценарий M')
    parser.add_argument('--work', type=Path)
    parser.add_argument('--keep', action='store_true')
    args = parser.parse_args()
    work = (args.work or Path(tempfile.mkdtemp(prefix='wxq-ru-test-'))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(INSTALLER_DIR))
    import build_installer as bi  # noqa: E402
    bi.GAME_ROOT = REAL_GAME  # повторная сборка Lua-нагрузок читает оригиналы из игры (только чтение)
    installer = (args.installer or build_variant(work, 'full', CANDIDATES)).resolve(strict=True)
    partial_exe = (args.partial_installer or build_variant(work, 'pkg1', (CARRIER,))).resolve(strict=True)
    old_pkg1_exe = build_variant(work, 'pkg1-old', (OLD_CARRIER,)).resolve(strict=True) if OLD_CARRIER.is_dir() else None
    Sandbox.prepare_template(work / 'prime-template')
    cand = candidate_hashes(*CANDIDATES)
    cand_1 = candidate_hashes(CARRIER)
    check('P0 заготовка пакета 10 для тестов на месте (114688 байт)', placeholder_10().stat().st_size == 114688)
    check('P0a заготовка пакета 4 для тестов на месте (114688 байт)', placeholder_4().stat().st_size == 114688)
    real_pkg4 = REAL_GAME / 'QtsDataFolder/QtsVFSCache/packages/4/4.db'
    real_pkg4_sha = sha(real_pkg4)
    news_sha = candidate_hashes(NEWS)
    real_pkg4_data = real_pkg4.with_name('4_0.db')
    pkg4_clean = real_pkg4_sha == sha(placeholder_4()) and not real_pkg4_data.exists()
    pkg4_ours = (real_pkg4_sha == news_sha['packages/4/4.db'] and real_pkg4_data.is_file()
                 and sha(real_pkg4_data) == news_sha['packages/4/4_0.db'])
    check('P0b в настоящей игре пакет 4: заготовка без 4_0.db ИЛИ оба файла нашего перевода (NEWS)', pkg4_clean or pkg4_ours,
          'заготовка' if pkg4_clean else 'наш перевод' if pkg4_ours else 'неизвестное состояние')
    pristine_sha = sha(pristine_catalog())
    placeholder_sha = sha(placeholder_10())
    placeholder4_sha = sha(placeholder_4())
    box = Sandbox(work / 'box')
    state_before = real_state()
    uninstall_before = uninstall_keys()
    real_pkg10 = REAL_GAME / 'QtsDataFolder/QtsVFSCache/packages/10/10.db'
    real_pkg10_sha = sha(real_pkg10)

    print('A. чистая игра: установка четырёх пакетов (1, 4, 10, 19)')
    box.build()
    before = box.snapshot()
    outside = box.outside_game()
    done = box.run(installer)
    check('A1 код выхода 0', done.returncode == 0, f'{done.returncode} ' + box.install_log()[-400:])
    after = box.snapshot()
    for relative, value in cand.items():
        check(f'A2 {relative} совпал с кандидатом', after.get(key(relative)) == value)
    changed = {k for k in after if before.get(k) != after[k]} | {k for k in before if k not in after}
    expected = {key(f'packages/{p}/{p}{s}') for p in PACKAGES for s in ('.db', '_0.db')}
    check('A3 изменены только каталоги и хранилища пакетов 1, 4, 10, 19 (.info не тронуты)', changed == expected, str(changed ^ expected))
    check('A4 prime не тронут', all(before[k] == after[k] for k in before if 'WXQ_Data' in k))
    check('A4a чужие пакеты (0, 10001, 1010000000) не тронуты', all(before[k] == after[k] for k in before if '\\packages\\0\\' in k or '10001' in k or '1010000000' in k))
    receipts = box.receipts()
    check('A5 одна резервная копия с receipt', len(receipts) == 1)
    if receipts:
        receipt = json.loads(receipts[0].read_text('utf-8-sig'))
        check('A6 receipt в статусе applied', receipt['status'] == 'applied')
        backup = receipts[0].parent / 'packages/1/1.db'
        check('A7 прежний 1.db сохранён байт-в-байт', backup.is_file() and sha(backup) == pristine_sha)
        backup10 = receipts[0].parent / 'packages/10/10.db'
        check('A7a заготовка 10.db (предшественник) сохранена байт-в-байт', backup10.is_file() and sha(backup10) == placeholder_sha)
        backup4 = receipts[0].parent / 'packages/4/4.db'
        check('A7e заготовка 4.db (предшественник) сохранена байт-в-байт', backup4.is_file() and sha(backup4) == placeholder4_sha)
        check('A7f в receipt: 4.db был, 4_0.db не было, резервной копии 4_0.db нет',
              rows_ok(receipt, 'packages/4/4.db', True) and rows_ok(receipt, 'packages/4/4_0.db', False) and not (receipts[0].parent / 'packages/4/4_0.db').exists())
        rows = {r['relative']: r for r in receipt['operations']}
        check('A7b в receipt: 10.db был, 10_0.db не было', rows['packages/10/10.db']['before_exists'] is True and rows['packages/10/10_0.db']['before_exists'] is False)
        check('A7c резервные копии только у файлов, которые были', not (receipts[0].parent / 'packages/10/10_0.db').exists() and not (receipts[0].parent / 'packages/1/1_0.db').exists())
        check('A7d .info пакетов не заменялись', all(rows[f'packages/{p}/{p}.info']['unchanged'] is True for p in PACKAGES))
    check('A8 state.json и install.log есть', (box.data / 'state.json').is_file() and (box.data / 'install.log').is_file())
    state = json.loads((box.data / 'state.json').read_text('utf-8-sig'))
    check('A8a в state.json все четыре пакета', sorted(state['packages'], key=int) == list(PACKAGES))
    check('A9 запись об удалении (unins000.exe)', (box.app / 'unins000.exe').is_file())
    log_text = box.install_log()
    check('A10 журнал без путей профиля пользователя и ключей', 'token' not in log_text.lower() and 'password' not in log_text.lower())
    check('A11 пакет 10 не помечен чужим', 'чужие' not in log_text and 'остановка [foreign' not in log_text)
    check('A12 в песочнице нет файлов лаунчера и Node', not [p.name for p in box.root.rglob('*') if FORBIDDEN.search(p.name)])
    check('A13 за пределами папки игры (папка лаунчера) ничего не появилось и не изменилось', box.outside_game() == outside)

    print('B. повторный запуск (идемпотентность)')
    done = box.run(installer)
    check('B1 код выхода 0', done.returncode == 0, f'{done.returncode}')
    check('B2 файлы не менялись', box.snapshot() == after)
    check('B3 новых резервных копий нет', len(box.receipts()) == 1)

    print('C. удаление перевода')
    done = box.uninstall()
    check('C1 деинсталлятор отработал', done.returncode == 0, f'{done.returncode}')
    restored = box.snapshot()
    check('C2 игра вернулась к исходному состоянию байт-в-байт', restored == before, str({k for k in set(restored) | set(before) if restored.get(k) != before.get(k)}))
    check('C2a заготовка пакета 10 вернулась байт-в-байт, 10_0.db удалён',
          sha(box.cache / '10/10.db') == placeholder_sha and not (box.cache / '10/10_0.db').exists())
    check('C2b заготовка пакета 4 вернулась байт-в-байт, 4_0.db удалён',
          sha(box.cache / '4/4.db') == placeholder4_sha and not (box.cache / '4/4_0.db').exists())
    check('C3 резервные копии и state удалены', not (box.data / 'backup').exists() and not (box.data / 'state.json').exists())

    print('D. обновление поверх прежней нашей версии пакета 1 (carrier-42a4bb)')
    box.build(ours=True)
    before_d = box.snapshot()
    done = box.run(installer)
    check('D1 код выхода 0', done.returncode == 0, f'{done.returncode} ' + box.install_log()[-300:])
    after_d = box.snapshot()
    check('D2 файлы заменены на кандидат', all(after_d.get(key(r)) == v for r, v in cand.items()))
    done = box.uninstall()
    check('D3 после удаления вернулась прежняя наша версия и заготовка 10', box.snapshot() == before_d)
    if OLD_TEXTURES.is_dir():
        box.build()
        shutil.copyfile(OLD_TEXTURES / 'packages/19/19.db', box.cache / '19/19.db')
        shutil.copyfile(OLD_TEXTURES / 'packages/19/19_0.db', box.cache / '19/19_0.db')
        before_d19 = box.snapshot()
        done = box.run(installer)
        check('D4 поверх прежнего носителя пакета 19 (tex2): код 0 и файлы кандидата', done.returncode == 0 and all(box.snapshot().get(key(r)) == v for r, v in cand.items()), f'{done.returncode} ' + box.install_log()[-300:])
        box.uninstall()
        check('D5 после удаления вернулся прежний носитель пакета 19 байт-в-байт', box.snapshot() == before_d19)

    print('E. чужие данные в override: отказ, ничего не записано')
    candidate_catalog = CARRIER / 'packages/1/1.db'
    box.build()
    data = bytearray(candidate_catalog.read_bytes())
    data[100000] ^= 0xFF  # похоже на наш каталог, но SHA неизвестен: записи кроме заголовка
    (box.cache / '1/1.db').write_bytes(bytes(data))
    before_e = box.snapshot()
    done = box.run(installer)
    check('E1 ненулевой код выхода', done.returncode != 0, f'{done.returncode}')
    check('E2 файлы игры не изменены', box.snapshot() == before_e)
    check('E3 в журнале понятная причина', 'чужие или неизвестные данные' in box.install_log(), box.install_log()[-400:])
    check('E4 резервных копий и записи об установке нет', not box.receipts() and not (box.app / 'unins000.exe').exists())
    box.build()
    header = bytes.fromhex(read_manifest(CARRIER)['override_package_headers_hex']['1'])
    data = bytearray((box.cache / '1/1.db').read_bytes())
    at = data.find(header)
    check('E4a заголовок найден в нетронутом каталоге (проверка самого теста)', at > 0)
    data[at + 85] ^= 0x01  # заголовок другой сборки игры
    (box.cache / '1/1.db').write_bytes(bytes(data))
    before_e1 = box.snapshot()
    done = box.run(installer)
    check('E4b заголовок другой сборки: отказ и файлы целы', done.returncode != 0 and box.snapshot() == before_e1 and 'заголовок' in box.install_log(), f'{done.returncode}')
    box.build()
    (box.cache / '1/1_0.db').write_bytes(b'foreign storage')
    before_e3 = box.snapshot()
    done = box.run(installer)
    check('E6 чужой файл данных рядом с чистым каталогом пакета 1: отказ и файлы целы', done.returncode != 0 and box.snapshot() == before_e3, f'{done.returncode}')
    # Пакет 10: свои заготовки возвращаются при удалении, чужое содержимое — отказ.
    box.build()
    shutil.copyfile(candidate_catalog, box.cache / '10/10.db')  # в каталоге пакета 10 записи не нашего перевода
    before_e10a = box.snapshot()
    done = box.run(installer)
    check('E7 пакет 10: чужое содержимое каталога — отказ, файлы целы', done.returncode != 0 and box.snapshot() == before_e10a and 'пакета 10' in box.install_log(), f'{done.returncode}')
    check('E7a резервных копий и записи об установке нет', not box.receipts() and not (box.app / 'unins000.exe').exists())
    box.build()
    lua_header = bytes.fromhex(read_manifest(LUA)['override_package_headers_hex']['10'])
    data = bytearray(placeholder_10().read_bytes())
    at = data.find(lua_header)
    check('E7b заголовок найден в заготовке пакета 10 (проверка самого теста)', at > 0)
    data[at + 85] ^= 0x01
    (box.cache / '10/10.db').write_bytes(bytes(data))
    before_e10b = box.snapshot()
    done = box.run(installer)
    check('E7c пакет 10: заголовок другой сборки — отказ, файлы целы', done.returncode != 0 and box.snapshot() == before_e10b and 'заголовок' in box.install_log(), f'{done.returncode}')
    box.build()
    (box.cache / '10/10_0.db').write_bytes(b'foreign lua storage')
    before_e10c = box.snapshot()
    done = box.run(installer)
    check('E7d пакет 10: чужой файл данных рядом с заготовкой — отказ, файлы целы', done.returncode != 0 and box.snapshot() == before_e10c, f'{done.returncode}')
    box.build()
    shutil.copyfile(LUA / 'packages/10/10.db', box.cache / '10/10.db')
    (box.cache / '10/10_0.db').write_bytes(b'not our storage')
    before_e10d = box.snapshot()
    done = box.run(installer)
    check('E7e пакет 10: наш каталог и чужое хранилище — отказ, файлы целы', done.returncode != 0 and box.snapshot() == before_e10d, f'{done.returncode}')
    for pkg, placeholder in (('4', placeholder_4()), ('19', pristine_19())):
        header_hex = read_manifest(CAND_BY_PACKAGE[pkg])['override_package_headers_hex'][pkg]
        word = f'пакет {pkg}'
        box.build()
        shutil.copyfile(candidate_catalog, box.cache / f'{pkg}/{pkg}.db')
        before_x = box.snapshot()
        done = box.run(installer)
        check(f'E8-{pkg} {word}: чужое содержимое каталога — отказ, файлы целы, копий нет',
              done.returncode != 0 and box.snapshot() == before_x and not box.receipts() and not (box.app / 'unins000.exe').exists() and f'пакета {pkg}' in box.install_log(), f'{done.returncode}')
        box.build()
        data = bytearray(placeholder.read_bytes())
        at = data.find(bytes.fromhex(header_hex))
        check(f'E9-{pkg} {word}: заголовок найден в заготовке (проверка самого теста)', at > 0)
        data[at + 85] ^= 0x01
        (box.cache / f'{pkg}/{pkg}.db').write_bytes(bytes(data))
        before_x = box.snapshot()
        done = box.run(installer)
        check(f'E10-{pkg} {word}: заголовок другой сборки — отказ, файлы целы', done.returncode != 0 and box.snapshot() == before_x and 'заголовок' in box.install_log(), f'{done.returncode}')
        box.build()
        (box.cache / f'{pkg}/{pkg}_0.db').write_bytes(b'foreign storage')
        before_x = box.snapshot()
        done = box.run(installer)
        check(f'E11-{pkg} {word}: чужой файл данных рядом с заготовкой — отказ, файлы целы', done.returncode != 0 and box.snapshot() == before_x, f'{done.returncode}')
        box.build()
        shutil.copyfile(CAND_BY_PACKAGE[pkg] / f'packages/{pkg}/{pkg}.db', box.cache / f'{pkg}/{pkg}.db')
        (box.cache / f'{pkg}/{pkg}_0.db').write_bytes(b'not our storage')
        before_x = box.snapshot()
        done = box.run(installer)
        check(f'E12-{pkg} {word}: наш каталог и чужое хранилище — отказ, файлы целы', done.returncode != 0 and box.snapshot() == before_x, f'{done.returncode}')
        box.build()
        (box.cache / f'{pkg}/{pkg}.db').unlink()
        before_x = box.snapshot()
        done = box.run(installer)
        check(f'E13-{pkg} {word}: нет каталога в override (игра не создала кэш) — отказ, файлы целы', done.returncode != 0 and box.snapshot() == before_x and 'cache_missing' in box.install_log(), f'{done.returncode} ' + box.install_log()[-200:])
    box.build()
    shutil.copyfile(candidate_catalog, box.cache / '10001/10001.db')  # чужой пакет, которого в установщике нет
    before_e5 = box.snapshot()
    done = box.run(installer)
    check('E5 чужой каталог постороннего пакета: отказ и файлы целы', done.returncode != 0 and box.snapshot() == before_e5, f'{done.returncode}')

    print('F. версия игры не совпала: отказ')
    box.build()
    flip(box.game / 'WXQ_Data/Package/builtin/1/1.db', 1000)
    before_f = box.snapshot()
    done = box.run(installer)
    check('F1 другое содержимое prime: отказ, файлы целы', done.returncode != 0 and box.snapshot() == before_f, f'{done.returncode}')
    check('F2 причина названа', 'не совпадает с ожидаемым' in box.install_log(), box.install_log()[-300:])
    box.build()
    with (box.game / 'WXQ_Data/Package/builtin/1/1.db').open('ab') as handle:
        handle.write(b'x')
    before_f2 = box.snapshot()
    done = box.run(installer)
    check('F3 другой размер prime: отказ, файлы целы', done.returncode != 0 and box.snapshot() == before_f2)
    box.build()
    (box.cache / '1/1.info').write_text('{"version":"9.9.9.9","revision":"0"}', 'utf-8')
    before_f3 = box.snapshot()
    done = box.run(installer)
    check('F4 другая версия в .info: отказ, файлы целы', done.returncode != 0 and box.snapshot() == before_f3)
    # Другая версия включает путь пересадки: в песочнице нет чистого скелета с журналом игры, поэтому отказ cache_missing (подробно - test_transplant.py).
    check('F5 в журнале причина отказа пути пересадки', 'cache_missing' in box.install_log(), box.install_log()[-300:])
    box.build()
    flip(box.game / 'WXQ_Data/Package/builtin/10/10.db', 5000)
    before_f4 = box.snapshot()
    done = box.run(installer)
    check('F6 prime пакета 10 другой: отказ, файлы целы', done.returncode != 0 and box.snapshot() == before_f4, f'{done.returncode}')
    box.build()
    (box.cache / '10/10.info').write_text('{"version":"9.9.9.9","revision":"0"}', 'utf-8')
    before_f5 = box.snapshot()
    done = box.run(installer)
    check('F7 другая версия в .info пакета 10: отказ, файлы целы', done.returncode != 0 and box.snapshot() == before_f5)

    box.build()
    flip(box.game / 'WXQ_Data/Package/builtin/4/4.db', 5000)
    before_f6 = box.snapshot()
    done = box.run(installer)
    check('F8 prime пакета 4 другой: отказ, файлы целы', done.returncode != 0 and box.snapshot() == before_f6, f'{done.returncode}')
    box.build()
    (box.cache / '4/4.info').write_text('{"version":"9.9.9.9","revision":"0"}', 'utf-8')
    before_f7 = box.snapshot()
    done = box.run(installer)
    check('F9 другая версия в .info пакета 4: отказ, файлы целы', done.returncode != 0 and box.snapshot() == before_f7)
    box.build()
    flip(box.game / 'WXQ_Data/Package/builtin/19/19.db', 5000)
    before_f8 = box.snapshot()
    done = box.run(installer)
    check('F10 prime пакета 19 другой: отказ, файлы целы', done.returncode != 0 and box.snapshot() == before_f8, f'{done.returncode}')
    box.build()
    (box.cache / '19/19.info').write_text('{"version":"9.9.9.9","revision":"0"}', 'utf-8')
    before_f9 = box.snapshot()
    done = box.run(installer)
    check('F11 другая версия в .info пакета 19: отказ, файлы целы', done.returncode != 0 and box.snapshot() == before_f9)

    print('G. игра запущена: установщик сам её закрывает (0.6.1)')
    box.build()
    stub = box.game / 'WXQ.exe'
    shutil.copyfile(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/ping.exe', stub)
    process = subprocess.Popen([str(stub), '-n', '60', '127.0.0.1'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        done = box.run(installer)
        log_g = box.install_log()
        check('G1 игра закрыта установщиком, установка прошла', done.returncode == 0 and process.poll() is not None, f'{done.returncode}')
        check('G2 в журнале: что закрыто (имя, PID, способ)', 'закрываю игру и лаунчер' in log_g and 'WXQ.exe, PID %d' % process.pid in log_g and 'принудительно' in log_g, log_g[-400:])
        box.uninstall()
    finally:
        process.kill()
        process.wait()

    print('H. игра не найдена')
    box.build()
    empty = work / 'empty-appdata'
    shutil.rmtree(empty, ignore_errors=True)
    empty.mkdir()
    done = box.run(installer, env={'APPDATA': str(empty), 'ProgramFiles': str(work / 'nope'), 'ProgramFiles(x86)': str(work / 'nope')}, game=False)
    check('H1 ненулевой код выхода', done.returncode != 0, f'{done.returncode}')
    check('H2 в журнале «Не удалось найти установленную игру» или список профилей', 'остановка [game_' in box.install_log() or 'остановка [cache_' in box.install_log(), box.install_log()[-300:])

    print('I. автопоиск игры через файл лаунчера')
    box.build()
    appdata = work / 'appdata'
    shutil.rmtree(appdata, ignore_errors=True)
    (appdata / 'os-launcher/Launcher').mkdir(parents=True)
    (appdata / 'os-launcher/Launcher/game-install-path.json').write_text(json.dumps({'1': {'x-normal': str(box.game)}}), 'utf-8')
    done = box.run(installer, env={'APPDATA': str(appdata)}, game=False)
    check('I1 установка по найденному пути, код 0', done.returncode == 0, f'{done.returncode}')
    check('I2 перевод лёг на место', sha(box.cache / '1/1_0.db') == cand['packages/1/1_0.db'])
    box.uninstall()
    box.build()
    stale = work / 'stale-game-folder'  # путь из файла лаунчера, где игры уже нет (нет WXQ.exe)
    stale.mkdir(exist_ok=True)
    (appdata / 'os-launcher/Launcher/game-install-path.json').write_text(json.dumps({'1': {'x-normal': str(box.game)}, '2': {'x-normal': str(stale)}}), 'utf-8')
    done = box.run(installer, env={'APPDATA': str(appdata)}, game=False)
    check('I3 лишний путь без WXQ.exe не даёт «несколько папок»: установка по единственной игре', done.returncode == 0 and sha(box.cache / '1/1_0.db') == cand['packages/1/1_0.db'], f'{done.returncode} ' + box.install_log()[-300:])
    box.uninstall()

    print('J. удаление, когда файл уже переписан игрой: чужое не трогаем')
    box.build()
    box.run(installer)
    (box.cache / '1/1_0.db').write_bytes(b'rewritten by game update')
    (box.cache / '10/10_0.db').write_bytes(b'rewritten lua storage')
    (box.cache / '4/4_0.db').write_bytes(b'rewritten news storage')
    (box.cache / '19/19_0.db').write_bytes(b'rewritten textures storage')
    done = box.uninstall()
    check('J1 удаление завершилось, чужие файлы остались нетронутыми',
          (box.cache / '1/1_0.db').read_bytes() == b'rewritten by game update' and (box.cache / '10/10_0.db').read_bytes() == b'rewritten lua storage'
          and (box.cache / '4/4_0.db').read_bytes() == b'rewritten news storage' and (box.cache / '19/19_0.db').read_bytes() == b'rewritten textures storage', f'{done.returncode}')
    check('J2 каталоги возвращены (пакет 1, заготовки пакетов 4 и 10, нетронутый пакет 19)',
          sha(box.cache / '1/1.db') == pristine_sha and sha(box.cache / '10/10.db') == placeholder_sha
          and sha(box.cache / '4/4.db') == placeholder4_sha and sha(box.cache / '19/19.db') == sha(pristine_19()))

    if partial_exe:
        print('K. два установщика подряд: сначала только пакет 1, потом все четыре')
        partial = partial_exe
        box.build()
        before_k = box.snapshot()
        done = box.run(partial)
        check('K1 установщик с одним пакетом, код 0', done.returncode == 0, f'{done.returncode} ' + box.install_log()[-300:])
        mid = box.snapshot()
        check('K2 изменён только пакет 1', {k for k in mid if before_k.get(k) != mid[k]} == {key('packages/1/1.db'), key('packages/1/1_0.db')})
        done = box.run(installer)
        check('K3 установщик с четырьмя пакетами поверх, код 0', done.returncode == 0, f'{done.returncode} ' + box.install_log()[-300:])
        after_k = box.snapshot()
        check('K4 все файлы четырёх кандидатов на месте', all(after_k.get(key(r)) == v for r, v in cand.items()))
        check('K5 две резервные копии, вторая без повторной записи пакета 1', len(box.receipts()) == 2)
        done = box.uninstall()
        check('K6 удаление вернуло игру к исходному состоянию (порядок отката: сначала новая копия, потом старая)', box.snapshot() == before_k,
              str({k for k in set(box.snapshot()) | set(before_k) if box.snapshot().get(k) != before_k.get(k)}))
        check('K7 заготовки пакетов 4 и 10 на месте', sha(box.cache / '10/10.db') == placeholder_sha and not (box.cache / '10/10_0.db').exists()
              and sha(box.cache / '4/4.db') == placeholder4_sha and not (box.cache / '4/4_0.db').exists())

    if args.previous_installer:
        print('M. обновление с прежнего установщика (0.2.0: пакеты 1, 10, 19 из tex2) на новый (пакеты 1, 4, 10, 19)')
        previous = args.previous_installer.resolve(strict=True)
        box.build()
        before_m = box.snapshot()
        done = box.run(previous)
        check('M1 прежний установщик, код 0', done.returncode == 0, f'{done.returncode} ' + box.install_log()[-300:])
        mid = box.snapshot()
        check('M2 пакет 4 прежним установщиком не тронут', all(mid[k] == before_m[k] for k in before_m if '\\packages\\4\\' in k))
        done = box.run(installer)
        check('M3 новый установщик поверх, код 0', done.returncode == 0, f'{done.returncode} ' + box.install_log()[-300:])
        after_m = box.snapshot()
        check('M4 все файлы четырёх кандидатов на месте', all(after_m.get(key(r)) == v for r, v in cand.items()))
        check('M5 две резервные копии', len(box.receipts()) == 2)
        done = box.uninstall()
        check('M6 удаление вернуло игру к исходному состоянию', box.snapshot() == before_m,
              str({k for k in set(box.snapshot()) | set(before_m) if box.snapshot().get(k) != before_m.get(k)}))

    print('N. прерванная установка: тупика нет («Удалить перевод» откатывает частичную запись)')
    tool = Tool(work / 'tool')
    ping = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/ping.exe'
    box.build()
    before_n = box.snapshot()
    made = tool.run(box, 'backup')
    label = made.get('label', '')
    check('N0 резервная копия создана', made.get('status') == 'ok' and label != '', str(made))
    (box.cache / '10' / f'10.db.ru-stage-{label}').mkdir()  # запись третьего каталога не пройдёт
    (box.cache / '1' / '1.db.ru-restore').mkdir()  # и откат не сможет вернуть 1.db
    failed = tool.run(box, 'unpack', label=label)
    check('N1 запись и откат оборваны: код apply_failed с подсказкой «Удалить перевод»',
          failed.get('code') == 'apply_failed' and 'Удалить перевод' in failed.get('message', ''), str(failed))
    partial_snapshot = box.snapshot()
    check('N2 в игре частичная запись', partial_snapshot != before_n)
    state = tool.run(box, 'status')
    check('N3 status: installed=no, pending=yes, interrupted=yes', (state.get('installed'), state.get('pending'), state.get('interrupted')) == ('no', 'yes', 'yes'), str(state))
    done = box.run(installer)
    check('N4 повторная установка отказывает кодом pending и ничего не трогает',
          done.returncode != 0 and 'остановка [pending]' in box.install_log() and box.snapshot() == partial_snapshot, f'{done.returncode}')
    (box.cache / '10' / f'10.db.ru-stage-{label}').rmdir()
    (box.cache / '1' / '1.db.ru-restore').rmdir()
    removed = tool.run(box, 'restore')
    check('N5 «Удалить перевод» откатывает частичную запись байт-в-байт', removed.get('status') == 'ok' and box.snapshot() == before_n, str(removed))
    state = tool.run(box, 'status')
    check('N6 после отката pending=no', (state.get('pending'), state.get('interrupted')) == ('no', 'no'), str(state))
    done = box.run(installer)
    check('N7 после отката обычная установка проходит', done.returncode == 0 and all(box.snapshot().get(key(r)) == v for r, v in cand.items()), f'{done.returncode} ' + box.install_log()[-300:])
    box.uninstall()
    check('N8 и удаляется до исходного состояния', box.snapshot() == before_n)

    print('N2. то же с запущенной игрой: запись оборвалась на середине, снять процесс и нажать «Удалить перевод»')
    stage_done = False
    for attempt in range(4):
        box.build()
        stub = box.game / 'WXQ.exe'
        shutil.copyfile(ping, stub)
        before_r = box.snapshot()
        made = tool.run(box, 'backup')
        label = made.get('label', '')
        (box.cache / '1' / '1.db.ru-restore').mkdir()  # откат дополнительно заблокирован: независимо от момента запуска игры
        started: list[subprocess.Popen] = []

        def launch() -> None:
            end = time.time() + 120
            while time.time() < end:
                if (box.cache / '1' / '1_0.db').exists():  # первый файл перевода записан: запускаем «игру»
                    started.append(subprocess.Popen([str(stub), '-n', '120', '127.0.0.1'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                    return
                time.sleep(0.001)

        import threading
        watcher = threading.Thread(target=launch)
        watcher.start()
        failed = tool.run(box, 'unpack', label=label)
        watcher.join()
        partial_now = box.snapshot() != before_r
        if failed.get('code') == 'apply_failed' and partial_now and started and started[0].poll() is None:
            stage_done = True
            break
        for process in started:
            process.kill()
            process.wait()
    check('N9 игра запущена посреди записи: apply_failed с подсказкой, файлы частично записаны', stage_done, str(failed))
    if stage_done:
        state = tool.run(box, 'status')
        check('N10 status видит прерванную установку', (state.get('installed'), state.get('pending')) == ('no', 'yes'), str(state))
        refused = tool.run(box, 'restore')
        check('N11 пока игра запущена, откат отказывает кодом running и ничего не ломает', refused.get('code') == 'running', str(refused))
        for process in started:
            process.kill()
            process.wait()
        time.sleep(0.5)
        (box.cache / '1' / '1.db.ru-restore').rmdir()
        removed = tool.run(box, 'restore')
        check('N12 после закрытия игры «Удалить перевод» возвращает всё байт-в-байт', removed.get('status') == 'ok' and box.snapshot() == before_r, str(removed))

    print('N3. процесс убит посреди записи: receipt в состоянии applying, повторный запуск не застревает')
    killed = False
    for attempt in range(5):
        box.build()
        before_k9 = box.snapshot()
        made = tool.run(box, 'backup')
        label = made.get('label', '')
        process = subprocess.Popen([str(tool.exe), 'unpack', '--payload', str(tool.dir), '--data', str(box.data), '--out', str(box.root / 'killed-out.txt'),
                                    '--game', str(box.game), '--label', label], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env=dict(os.environ, WXQ_RU_USERS_ROOT=str(box.root / 'no-users')))
        end = time.time() + 60
        while time.time() < end and process.poll() is None and not (box.cache / '1' / '1_0.db').exists():
            time.sleep(0.001)
        process.kill()
        process.wait()
        receipt_k = json.loads((box.data / 'backup' / label / 'receipt.json').read_text('utf-8-sig'))
        if receipt_k['status'] == 'applying' and box.snapshot() != before_k9:
            killed = True
            break
    check('N13 после убитого процесса receipt в статусе applying, файлы частично записаны', killed, receipt_k['status'])
    if killed:
        state = tool.run(box, 'status')
        check('N14 status: pending=yes, interrupted=yes, installed=no', (state.get('installed'), state.get('pending'), state.get('interrupted')) == ('no', 'yes', 'yes'), str(state))
        stuck = box.snapshot()
        done = box.run(installer)
        check('N15 повторный запуск установщика: отказ pending, ничего не затронуто', done.returncode != 0 and 'остановка [pending]' in box.install_log() and box.snapshot() == stuck, f'{done.returncode}')
        removed = tool.run(box, 'restore')
        check('N16 «Удалить перевод» после убитого процесса возвращает исходное состояние', removed.get('status') == 'ok' and box.snapshot() == before_k9, str(removed))
        done = box.run(installer)
        check('N17 и установка после этого проходит', done.returncode == 0 and all(box.snapshot().get(key(r)) == v for r, v in cand.items()), f'{done.returncode}')
        box.uninstall()
        check('N18 удаление после повторной установки: исходное состояние', box.snapshot() == before_k9)
    box.build()
    made = tool.run(box, 'backup')  # оборвано до записи: receipt остаётся в prepared и установке не мешает
    done = box.run(installer)
    check('N19 остался только prepared-receipt (процесс убит до записи): установка проходит', made.get('status') == 'ok' and done.returncode == 0, f'{done.returncode}')
    box.uninstall()

    print('N4. receipt другой папки игры не пускает запись; брошенный мьютекс не мешает')
    box.build()
    before_o4 = box.snapshot()
    made = tool.run(box, 'backup')
    label = made.get('label', '')
    receipt_file = box.data / 'backup' / label / 'receipt.json'
    tampered = json.loads(receipt_file.read_text('utf-8-sig'))
    tampered['game_root'] = str(work / 'other-game')
    receipt_file.write_text(json.dumps(tampered), 'utf-8')
    refused = tool.run(box, 'unpack', label=label)
    check('N20 unpack с receipt чужой игры отказывает кодом restore, файлы целы', refused.get('code') == 'restore' and box.snapshot() == before_o4, str(refused))
    import ctypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    mutex_name = 'Global\\Egoist.WXQRussian.Tool'
    owner = subprocess.Popen([sys.executable, '-c', 'import ctypes,time\nh=ctypes.windll.kernel32.CreateMutexW(None,True,"Global\\\\Egoist.WXQRussian.Tool")\nprint("ready",flush=True)\ntime.sleep(120)'],
                             stdout=subprocess.PIPE)
    owner.stdout.readline()
    held = kernel32.CreateMutexW(None, 0, mutex_name)  # свой дескриптор держит мьютекс живым после смерти владельца
    busy = tool.run(box, 'backup')
    check('N21 пока мьютекс занят живым процессом: отказ busy', busy.get('code') == 'busy', str(busy))
    owner.kill()
    owner.wait()
    box.build()
    abandoned = tool.run(box, 'backup')
    kernel32.CloseHandle(held)
    check('N22 мьютекс брошен убитым процессом (AbandonedMutexException): команда выполняется', abandoned.get('status') == 'ok' and abandoned.get('label'), str(abandoned))

    print('O. receipt чужой папки игры не мешает удалению')
    box.build()
    before_o = box.snapshot()
    done = box.run(installer)
    check('O0 установка, код 0', done.returncode == 0, f'{done.returncode}')
    other_game = work / 'other-game'
    other = box.data / 'backup' / '29990101-000000'
    other.mkdir(parents=True)
    (other / 'receipt.json').write_text(json.dumps({
        'schema': 1, 'status': 'applied', 'label': '29990101-000000', 'game_root': str(other_game),
        'cache_root': str(other_game / 'QtsDataFolder' / 'QtsVFSCache' / 'packages'), 'created_utc': '2999-01-01T00:00:00.0000000Z', 'operations': []}), 'utf-8')
    done = box.uninstall()
    check('O1 удаление завершилось, игра возвращена байт-в-байт', done.returncode == 0 and box.snapshot() == before_o, f'{done.returncode}')
    check('O2 чужой receipt оставлен на месте, как был', (other / 'receipt.json').is_file() and json.loads((other / 'receipt.json').read_text('utf-8'))['status'] == 'applied')
    check('O3 в журнале сказано, что копия другой папки игры пропущена', 'относится к другой папке игры' in box.install_log())
    check('O4 копии своей игры помечены restored', all(json.loads(r.read_text('utf-8-sig'))['status'] == 'restored' for r in box.receipts() if r.parent != other))

    if old_pkg1_exe:
        print('Q. порядок отката: по created_utc, а не по метке')
        box.build()
        before_q = box.snapshot()
        done = box.run(old_pkg1_exe)
        check('Q1 сначала установщик со старым носителем пакета 1', done.returncode == 0, f'{done.returncode} ' + box.install_log()[-300:])
        done = box.run(installer)
        check('Q2 затем полный установщик поверх', done.returncode == 0, f'{done.returncode}')
        dirs = sorted(r.parent for r in box.receipts())
        check('Q3 две резервные копии', len(dirs) == 2, str(dirs))
        if len(dirs) == 2:
            older, newer = dirs  # по метке: older раньше
            older.rename(older.with_name('29990101-000001'))   # старая копия получает «самую позднюю» метку
            newer.rename(newer.with_name('20200101-000001'))   # новая — «самую раннюю»
            done = box.uninstall()
            check('Q4 откат идёт от новой установки к старой по created_utc: игра вернулась байт-в-байт', done.returncode == 0 and box.snapshot() == before_q,
                  str({k for k in set(box.snapshot()) | set(before_q) if box.snapshot().get(k) != before_q.get(k)}))

    print('P. ссылки (junction) на пути записи: отказ')
    box.build()
    real_cache = box.game / 'QtsDataFolder/QtsVFSCache/packages'
    hidden = box.game / 'QtsDataFolder/QtsVFSCache/packages-real'
    real_cache.rename(hidden)
    junction(real_cache, hidden)
    try:
        before_hidden = tree(hidden)
        done = box.run(installer)
        check('P1 packages — ссылка: установка отказывает кодом reparse', done.returncode != 0 and 'остановка [reparse]' in box.install_log(), f'{done.returncode}')
        check('P2 по ссылке ничего не записано, резервных копий нет', tree(hidden) == before_hidden and not box.receipts())
    finally:
        os.rmdir(real_cache)
        hidden.rename(real_cache)
    box.build()
    moved = work / 'pkg10-real'
    shutil.rmtree(moved, ignore_errors=True)
    shutil.move(str(box.cache / '10'), str(moved))
    junction(box.cache / '10', moved)
    try:
        before_moved = tree(moved)
        done = box.run(installer)
        check('P3 packages\\10 — ссылка: установка отказывает кодом reparse, по ссылке ничего не записано',
              done.returncode != 0 and 'остановка [reparse]' in box.install_log() and tree(moved) == before_moved and not box.receipts(), f'{done.returncode}')
    finally:
        os.rmdir(box.cache / '10')
        shutil.move(str(moved), str(box.cache / '10'))
    box.build()
    before_p = box.snapshot()
    done = box.run(installer)
    check('P4 установка без ссылок проходит', done.returncode == 0, f'{done.returncode}')
    moved = work / 'pkg10-moved'
    shutil.rmtree(moved, ignore_errors=True)
    shutil.move(str(box.cache / '10'), str(moved))
    junction(box.cache / '10', moved)
    try:
        before_moved = tree(moved)
        refused = tool.run(box, 'restore')
        check('P5 packages\\10 стал ссылкой: откат отказывает кодом reparse и не пишет по ссылке', refused.get('code') == 'reparse' and tree(moved) == before_moved, str(refused))
    finally:
        os.rmdir(box.cache / '10')
        shutil.move(str(moved), str(box.cache / '10'))
    removed = tool.run(box, 'restore')
    check('P6 ссылку убрали: откат проходит и возвращает исходное состояние', removed.get('status') == 'ok' and box.snapshot() == before_p, str(removed))

    print('R. каталог данных: права и расположение помощника')
    box.build()
    box.data.mkdir(parents=True)  # заранее созданный каталог с унаследованными (широкими) правами
    done = box.run(installer)
    check('R0 установка в уже существующий каталог данных, код 0', done.returncode == 0, f'{done.returncode}')
    protected, rows = acl_rows(box.data)
    sids = {sid: rights for sid, rights, inherited in rows}
    check('R1 наследование от родителя отключено, нет унаследованных записей', protected and not any(i == 'True' for _, _, i in rows), str(rows))
    check('R2 Администраторы и SYSTEM — полный доступ', 'FullControl' in sids.get('S-1-5-32-544', '') and 'FullControl' in sids.get('S-1-5-18', ''), str(rows))
    users_rights = sids.get('S-1-5-32-545', '')
    check('R3 обычным пользователям — только чтение', 'ReadAndExecute' in users_rights and not any(w in users_rights for w in ('Write', 'Modify', 'FullControl', 'Delete', 'CreateFiles', 'AppendData')), users_rights)
    check('R4 нет записей Everyone и Authenticated Users', 'S-1-1-0' not in sids and 'S-1-5-11' not in sids, str(rows))
    protected_b, rows_b = acl_rows(box.data / 'backup')
    check('R5 вложенные каталоги и файлы наследуют эти права (backup)', all(i == 'True' for _, _, i in rows_b) and rows_b, str(rows_b))
    check('R6 помощник и деинсталлятор лежат в каталоге программы, а не в данных',
          (box.app / 'WXQTool.exe').is_file() and (box.app / 'unins000.exe').is_file() and not (box.data / 'WXQTool.exe').exists() and not (box.data / 'unins000.exe').exists())
    check('R7 каталог данных запомнен для деинсталлятора', (box.app / 'datadir.txt').read_text('utf-8-sig').strip() == str(box.data))
    box.uninstall()
    check('R8 после удаления копии и state убраны, каталог программы очищен', not (box.data / 'backup').exists() and not (box.data / 'state.json').exists() and not (box.app / 'WXQTool.exe').exists())

    print('F. релизная сборка без /CURRENTUSER, тестовая — с ним')
    script_text = (INSTALLER_DIR / 'WXQRussian.iss').read_text('utf-8-sig')
    override_at = script_text.index('PrivilegesRequiredOverridesAllowed=commandline')
    check('F20 в iss строка PrivilegesRequiredOverridesAllowed стоит только внутри #ifdef TestBuild',
          script_text.rfind('#ifdef TestBuild', 0, override_at) > script_text.rfind('#endif', 0, override_at) and script_text.count('PrivilegesRequiredOverridesAllowed') == 1)
    check('F21 каталог программы по умолчанию — Program Files\\Egoist\\WXQ-Russian', 'DefaultDirName={commonpf64}\\Egoist\\WXQ-Russian' in script_text)
    release_defines = bi.iscc_defines(Path('s'), Path('o/x.exe'), '0', 'd', 'g', 'p', False)
    check('F22 build_installer: /DTESTBUILD только при --test-build', '/DTESTBUILD' not in release_defines and '/DTESTBUILD' in bi.iscc_defines(Path('s'), Path('o/x.exe'), '0', 'd', 'g', 'p', True))
    check('F23 тестовая сборка помечена в ресурсе версии', TEST_MARK in installer.read_bytes() and TEST_MARK in partial_exe.read_bytes())
    if args.release_installer:
        check('F24 в релизной сборке метки тестовой сборки нет (/CURRENTUSER не разрешён)', TEST_MARK not in args.release_installer.resolve(strict=True).read_bytes())

    print('U. русский лаунчер: сборка с --launcher-build, переключатель, ярлыки, флаг, удаление')
    launcher_manifest = json.loads((LAUNCHER_BUILD / 'manifest.json').read_text('utf-8-sig'))
    launcher_exe_setup = build_variant(work, 'full-launcher', CANDIDATES, launcher=True).resolve(strict=True)
    desktop, startmenu = box.root.parent / 'u-desktop', box.root.parent / 'u-start'
    base_extra = [f'/DESKTOPDIR={desktop}', f'/STARTDIR={startmenu}']
    extra = base_extra   # по умолчанию русский лаунчер включён (решение владельца 14.10.2026), тихий режим выключает /LAUNCHERRU=0
    ru_name = 'Honor of Kings ACE (RU).lnk'

    def prepare_u(with_original: bool = True) -> None:
        box.build()
        shutil.rmtree(desktop, ignore_errors=True)
        shutil.rmtree(startmenu, ignore_errors=True)
        desktop.mkdir(parents=True)
        if with_original:
            (box.launcher / LAUNCHER_EXE_REL).parent.mkdir(parents=True)
            (box.launcher / LAUNCHER_EXE_REL).write_bytes(b'stub-original-launcher')

    check('U0 перед тестами флага русского лаунчера в HKCU нет', ru_flag() is None)
    prepare_u()
    before_u = box.snapshot()
    outside_u = box.outside_game()
    done = box.run(launcher_exe_setup, extra)
    check('U1 установка с лаунчером: код 0', done.returncode == 0, f'{done.returncode} ' + box.install_log()[-400:])
    check('U2 перевод игры поставлен как обычно', all(box.snapshot().get(key(r)) == v for r, v in cand.items()))
    app_launcher = box.app / 'launcher'
    on_disk = sorted(f.name for f in app_launcher.iterdir()) if app_launcher.is_dir() else []
    check('U3 в папке launcher активатор, шесть файлов лаунчера и target.txt',
          on_disk == sorted(['WXQLauncherRU.exe', 'target.txt', *bi.LAUNCHER_FILES]), str(on_disk))
    check('U4 SHA файлов совпадают с manifest.json сборки лаунчера',
          all((app_launcher / n).is_file() and sha(app_launcher / n) == launcher_manifest['files'][n] for n in bi.LAUNCHER_FILES))
    runtime_text = (app_launcher / 'launcher-ru-runtime.cjs').read_text('utf-8') if (app_launcher / 'launcher-ru-runtime.cjs').is_file() else ''
    runtime_reads = set(re.findall(r"\bread\('([^']+)'\)", runtime_text)) | set(re.findall(r"const BRAND_PNG = '([^']+)'", runtime_text))
    check('U4a установленный runtime читает только файлы, которые лежат рядом с ним в launcher\\',
          len(runtime_reads) == 4 and all((app_launcher / n).is_file() for n in runtime_reads), str(runtime_reads))
    target_line = (app_launcher / 'target.txt').read_text('utf-8-sig').splitlines()[0] if (app_launcher / 'target.txt').is_file() else ''
    check('U5 target.txt указывает на оригинальный 王者万象棋.exe', Path(target_line) == box.launcher / LAUNCHER_EXE_REL, target_line)
    check('U6 активатор WXQLauncherRU.exe лежит в launcher\\ рядом с target.txt (он ищет target.txt в папке своего exe), не в корне {app}',
          (app_launcher / 'WXQLauncherRU.exe').is_file() and (app_launcher / 'target.txt').is_file() and not (box.app / 'WXQLauncherRU.exe').exists() and (box.app / 'WXQTool.exe').is_file())
    check('U7 флаг LauncherRU=1 записан', ru_flag() == (1, 4), str(ru_flag()))
    check('U8 ярлыки на рабочем столе и в меню Пуск', (desktop / ru_name).is_file() and (startmenu / ru_name).is_file(), str(list(desktop.iterdir()) + list(startmenu.iterdir()) if startmenu.is_dir() else []))
    check('U9 оригинальный лаунчер и всё за пределами игры не изменены', box.outside_game() == outside_u)
    activator = (app_launcher / 'WXQLauncherRU.exe').read_bytes()
    shell = subprocess.run(['powershell', '-NoProfile', '-Command', '$s=New-Object -ComObject WScript.Shell; foreach($p in @(\'' + str(desktop / ru_name) + '\',\'' + str(startmenu / ru_name) +
                           '\')){$l=$s.CreateShortcut($p); $l.TargetPath; $l.WorkingDirectory}'], capture_output=True, text=True)
    link_rows = [x.strip() for x in shell.stdout.splitlines() if x.strip()]
    check('U8a ярлыки указывают на активатор в launcher\\', link_rows == [str(app_launcher / 'WXQLauncherRU.exe'), str(app_launcher)] * 2, str(link_rows))
    check('U10 в активатор зашит ресурс expected в формате активатора: runtime_sha256= и dictionary_sha256=',
          f'runtime_sha256={launcher_manifest["module_sha256"]}\ndictionary_sha256={launcher_manifest["dictionary_sha256"]}\n'.encode() in activator)
    check('U10a и нет формата сборки перевода (module=, dictionary=)', b'module=' + launcher_manifest['module_sha256'].encode() not in activator)
    check('U10b манифест активатора требует прав администратора', b'requireAdministrator' in activator)
    log_u = box.install_log()
    check('U11 журнал без путей профиля и без лишних данных (токены, пароли)', 'token' not in log_u.lower() and 'password' not in log_u.lower())
    done = box.run(launcher_exe_setup, extra)
    check('U12 повторная установка с лаунчером: код 0, файлы те же', done.returncode == 0 and sorted(f.name for f in app_launcher.iterdir()) == on_disk and ru_flag() == (1, 4), f'{done.returncode}')
    done = box.uninstall()
    check('U13 удаление: код 0, игра вернулась байт-в-байт', done.returncode == 0 and box.snapshot() == before_u, f'{done.returncode}')
    check('U14 удаление убрало launcher, активатор, ярлыки', not app_launcher.exists() and not (box.app / 'WXQLauncherRU.exe').exists()
          and not (desktop / ru_name).exists() and not (startmenu / ru_name).exists(), str(list(box.app.rglob('*'))))
    check('U15 удаление убрало флаг', ru_flag() is None, str(ru_flag()))
    check('U16 оригинальный лаунчер после удаления на месте', box.outside_game() == outside_u)

    prepare_u()
    done = box.run(launcher_exe_setup, base_extra + ['/LAUNCHERRU=0'])   # /LAUNCHERRU=0: тихое выключение
    check('U20 /LAUNCHERRU=0: перевод игры поставлен, код 0', done.returncode == 0 and all(box.snapshot().get(key(r)) == v for r, v in cand.items()), f'{done.returncode}')
    check('U21 лаунчер не поставлен: ни файлов, ни активатора, ни флага, ни ярлыков',
          not (box.app / 'launcher').exists() and not (box.app / 'WXQLauncherRU.exe').exists() and ru_flag() is None and not any(desktop.iterdir()) and not startmenu.exists())
    done = box.run(launcher_exe_setup, extra)
    check('U22 затем без параметра (по умолчанию включён): ставится поверх', done.returncode == 0 and ru_flag() == (1, 4) and (box.app / 'launcher' / 'target.txt').is_file(), f'{done.returncode}')
    (box.app / 'WXQLauncherRU.exe').write_bytes(b'stale')   # активатор прежней раскладки (в корне {app}) тоже убирается
    done = box.run(launcher_exe_setup, base_extra + ['/LAUNCHERRU=0'])
    check('U23 переустановка с выключенным переключателем убирает прежний лаунчер, флаг и ярлыки',
          done.returncode == 0 and not (box.app / 'launcher').exists() and not (box.app / 'WXQLauncherRU.exe').exists() and ru_flag() is None
          and not (desktop / ru_name).exists() and not (startmenu / ru_name).exists(), f'{done.returncode} ' + box.install_log()[-300:])
    box.uninstall()
    check('U24 удаление после этого: игра чистая, флага нет', box.snapshot() == before_u and ru_flag() is None)

    prepare_u()
    junction_target = box.root / 'real-build'
    shutil.rmtree(junction_target, ignore_errors=True)
    junction_target.mkdir()
    build_dir = (box.launcher / LAUNCHER_EXE_REL).parent
    (junction_target / LAUNCHER_EXE_REL.name).write_bytes(b'stub-original-launcher')
    shutil.rmtree(build_dir)
    subprocess.run(['cmd', '/c', 'mklink', '/J', str(build_dir), str(junction_target)], check=True, capture_output=True)
    done = box.run(launcher_exe_setup, extra)
    check('U35 оригинал за junction в Program Files: перевод игры ставится, русский лаунчер нет, target.txt не пишется',
          done.returncode == 0 and all(box.snapshot().get(key(r)) == v for r, v in cand.items()) and not (box.app / 'launcher').exists() and ru_flag() is None
          and not any(desktop.iterdir()), f'{done.returncode}')
    os.rmdir(build_dir)   # убираем junction, а не его содержимое
    box.uninstall()

    prepare_u()
    done = box.run(launcher_exe_setup, extra, env={'wxq_ru_trust_root': str(box.root / 'not-program-files')})
    check('U36 оригинал вне «Program Files» (путь из профиля пользователя): перевод ставится, target.txt не пишется, русский лаунчер нет',
          done.returncode == 0 and all(box.snapshot().get(key(r)) == v for r, v in cand.items()) and not (box.app / 'launcher').exists() and ru_flag() is None
          and not any(desktop.iterdir()), f'{done.returncode}')
    check('U37 в журнале сказано почему', 'оригинальный лаунчер не найден' in (box.log.read_text('utf-8-sig', errors='replace') if box.log.is_file() else ''))
    box.uninstall()

    prepare_u(with_original=False)
    done = box.run(launcher_exe_setup, extra)
    check('U30 оригинального лаунчера нет: перевод игры ставится, код 0', done.returncode == 0 and all(box.snapshot().get(key(r)) == v for r, v in cand.items()), f'{done.returncode}')
    check('U31 русский лаунчер не ставится: нет файлов, флага, ярлыков', not (box.app / 'launcher').exists() and not (box.app / 'WXQLauncherRU.exe').exists()
          and ru_flag() is None and not any(desktop.iterdir()))
    inno_log = box.log.read_text('utf-8-sig', errors='replace') if box.log.is_file() else ''
    check('U32 в журнале установщика сказано почему', 'оригинальный лаунчер не найден' in inno_log, inno_log[-300:])
    box.uninstall()

    prepare_u()
    done = box.run(installer, extra)
    check('U40 сборка без лаунчера: код 0 и перевод поставлен', done.returncode == 0 and all(box.snapshot().get(key(r)) == v for r, v in cand.items()), f'{done.returncode}')
    check('U41 сборка без лаунчера: нет launcher, активатора, флага, ярлыков',
          not (box.app / 'launcher').exists() and not (box.app / 'WXQLauncherRU.exe').exists() and ru_flag() is None and not any(desktop.iterdir()) and not startmenu.exists())
    box.uninstall()

    # помощник: launcher_exe из списка сборок, который лежит в payload
    prepare_u()
    found = tool.run(box, 'find')
    check('U50 find без списка сборок: launcher_exe пуст', found.get('status') == 'ok' and found.get('launcher_exe') == '', str(found))
    shutil.copyfile(LAUNCHER_BUILD / 'profiles.json', tool.dir / 'launcher-profiles.json')
    found = tool.run(box, 'find')
    check('U51 find со списком сборок: launcher_exe и launcher_build', found.get('launcher_exe') == str(box.launcher / LAUNCHER_EXE_REL) and found.get('launcher_build') == '26083.0.7.30004', str(found))
    (box.launcher / LAUNCHER_EXE_REL).unlink()
    found = tool.run(box, 'find')
    check('U52 find, когда оригинала нет: launcher_exe пуст', found.get('status') == 'ok' and found.get('launcher_exe') == '', str(found))
    (box.launcher / LAUNCHER_EXE_REL).write_bytes(b'x')
    for bad in ('..\\Launcher\\' + LAUNCHER_EXE_REL.as_posix().replace('/', '\\'), 'C:\\Windows\\System32\\cmd.exe', '..\\..\\x.exe', 'dir\\not-exe.txt'):
        profiles_bad = json.loads((LAUNCHER_BUILD / 'profiles.json').read_text('utf-8-sig'))
        profiles_bad['builds'][0]['exe_rel'] = bad
        (tool.dir / 'launcher-profiles.json').write_text(json.dumps(profiles_bad), 'utf-8')
        found = tool.run(box, 'find')
        check(f'U53 find отвергает путь {bad!r} вне папки лаунчера', found.get('status') == 'ok' and found.get('launcher_exe') == '', str(found))
    (tool.dir / 'launcher-profiles.json').write_text('не json', 'utf-8')
    found = tool.run(box, 'find')
    check('U54 find с повреждённым списком: игра найдена, launcher_exe пуст', found.get('status') == 'ok' and found.get('game') == str(box.game) and found.get('launcher_exe') == '', str(found))
    (tool.dir / 'launcher-profiles.json').unlink()
    shutil.copyfile(LAUNCHER_BUILD / 'profiles.json', tool.dir / 'launcher-profiles.json')
    tool.env = {'WXQ_RU_TRUST_ROOT': str(box.root / 'elsewhere')}
    found = tool.run(box, 'find')
    check('U55 find: оригинал вне Program Files (профиль пользователя) отвергается, игра найдена', found.get('status') == 'ok' and found.get('game') == str(box.game) and found.get('launcher_exe') == '', str(found))
    tool.env = {'WXQ_RU_TRUST_ROOT': ''}
    found = tool.run(box, 'find')
    check('U55a find: без корня доверия (как с пустыми Program Files) тоже пусто', found.get('launcher_exe') == '', str(found))
    # ярлык рядом с игрой: используется только внутри Program Files (корень доверия), иначе launcher_lnk пуст
    lnk = box.launcher / 'Honor of Kings ACE.lnk'
    lnk.write_bytes(b'x')
    tool.env = {}
    found = tool.run(box, 'find')
    check('U56 find: ярлык внутри доверенного корня (Program Files) возвращается', found.get('launcher_lnk') == str(lnk), str(found))
    tool.env = {'WXQ_RU_TRUST_ROOT': str(box.root / 'elsewhere')}
    found = tool.run(box, 'find')
    check('U57 find: ярлык вне Program Files (профиль, чужая папка) не используется', found.get('status') == 'ok' and found.get('launcher_lnk') == '', str(found))
    lnk.unlink()
    tool.env = {}
    (tool.dir / 'launcher-profiles.json').unlink()

    # сборщик: белый список, проверка SHA, формат expected
    import shutil as _sh
    expected_line = bi.expected_text(launcher_manifest)
    check('U60 формат expected: ровно runtime_sha256= и dictionary_sha256=', expected_line == f'runtime_sha256={launcher_manifest["module_sha256"]}\ndictionary_sha256={launcher_manifest["dictionary_sha256"]}\n')
    install_u, files_u = bi.combine(list(CANDIDATES), 'test', '2026-01-01')
    staging_u = work / 'staging-launcher'
    shutil.rmtree(staging_u, ignore_errors=True)
    staging_u.mkdir()
    bi.make_staging(install_u, files_u, staging_u, with_tool=False, launcher_build=LAUNCHER_BUILD)
    names_u = sorted(f.relative_to(staging_u).as_posix() for f in staging_u.rglob('*') if f.is_file())
    check('U61 staging с лаунчером: ровно белый список (активатор и шесть файлов в launcher/) сверх обычного',
          set(names_u) - ({'install.json', 'wxq-ru.ico', 'payload-files.iss', 'payload-extract.iss', 'payload-copy.iss'} | set(bi.ui_asset_names())) == bi.LAUNCHER_STAGED, str(names_u))
    check('U62 в staging нет Node, *.mjs, artifacts, launcher-entry, кроме белого списка', not [n for n in names_u if n not in bi.LAUNCHER_STAGED and re.search(r'(^|/)(node(\.exe)?|artifacts?)$|\.mjs$|launcher-entry|\.asar$', n, re.IGNORECASE)])
    include_u = (staging_u / 'payload-files.iss').read_text('utf-8-sig')
    check('U63 список Inno: профили лаунчера только как dontcopy для помощника', 'DestName: "launcher-profiles.json"; Flags: dontcopy' in include_u and 'launcher-ru-runtime' not in include_u)
    for extra_name in ('node.exe', 'launcher-entry-x.mjs', 'x.asar', 'artifacts'):
        (staging_u / 'launcher' / extra_name).write_bytes(b'x')
        try:
            bi.assert_clean(install_u, files_u, staging_u, True)
            refused_u = False
        except SystemExit:
            refused_u = True
        check(f'U64 лишний файл лаунчера {extra_name} в белом списке отвергается', refused_u)
        (staging_u / 'launcher' / extra_name).unlink()
    try:
        bi.assert_clean(install_u, files_u, staging_u, False)
        refused_u = False
    except SystemExit:
        refused_u = True
    check('U65 те же файлы без --launcher-build отвергаются', refused_u)
    (staging_u / 'launcher' / 'launcher-ru.json').unlink()
    try:
        bi.assert_clean(install_u, files_u, staging_u, True)
        refused_u = False
    except SystemExit:
        refused_u = True
    check('U66 не хватает файла белого списка: сборка отказывается', refused_u)
    fake_root = work / 'fake-build'
    shutil.rmtree(fake_root, ignore_errors=True)
    fake_root.mkdir()
    fake_build = fake_root / LAUNCHER_BUILD.name
    shutil.copytree(LAUNCHER_BUILD, fake_build)
    real_root = bi.BUILD_ROOT
    bi.BUILD_ROOT = fake_root.resolve()
    try:
        check('U70 копия сборки лаунчера проходит проверку', bool(bi.load_launcher_build(fake_build)))
        for broken in bi.LAUNCHER_FILES:
            original = (fake_build / broken).read_bytes()
            (fake_build / broken).write_bytes(original + b' ')
            try:
                bi.load_launcher_build(fake_build)
                rejected = False
            except SystemExit as error:
                rejected = broken in str(error)
            (fake_build / broken).write_bytes(original)
            check(f'U71 изменённый {broken}: сборка отказывается (SHA не совпал с manifest.json)', rejected)
    finally:
        bi.BUILD_ROOT = real_root
    try:
        bi.load_launcher_build(PROJECT / 'build' / LUA.name)
        rejected = False
    except SystemExit:
        rejected = True
    check('U72 каталог не build/launcher-ru-*: сборка отказывается', rejected)
    iss_text = (INSTALLER_DIR / 'WXQRussian.iss').read_text('utf-8-sig')
    check('U80 iss: файлы лаунчера, ярлыки и флаг — только под #ifdef WithLauncher и Check: LauncherWanted',
          iss_text.count('Check: LauncherWanted') >= 7 and iss_text.index('#ifdef WithLauncher') < iss_text.index('WXQLauncherRU.exe'))
    check('U81 iss: каталоги ярлыков DESKTOPDIR и STARTDIR работают только в тестовой сборке',
          iss_text.index("{param:DESKTOPDIR|}") > iss_text.rfind('#ifdef TestBuild', 0, iss_text.index("{param:DESKTOPDIR|}")) > iss_text.rfind('#endif', 0, iss_text.index("{param:DESKTOPDIR|}")))
    for phrase in ('Русский лаунчер', 'отладочный порт', 'права администратора', 'обычный лаунчер на китайском'):
        check(f'U82 в тексте окна есть «{phrase}»', phrase in iss_text)
    warn_toggle, warn_final = 'Включено по умолчанию; функция новая, с защитой ACE в игре не проверена.', 'Русский лаунчер включён по умолчанию; функция новая и не проверена в игре с защитой ACE.'
    check('U82a честный текст (включён по умолчанию, новая, не проверена с ACE) есть и у переключателя, и в тексте финала', iss_text.count(warn_toggle) == 1 and iss_text.count(warn_final) == 1)
    check('U82b в финале сказано, как выключить (/LAUNCHERRU=0 и переключатель)', '/LAUNCHERRU=0' in iss_text and 'с выключенным переключателем' in iss_text)
    check('U83 по умолчанию русский лаунчер ВКЛЮЧЁН (когда доступен), /LAUNCHERRU=0 выключает',
          "LauncherOn := LauncherAvailable and (ExpandConstant('{param:LAUNCHERRU|1}') <> '0')" in iss_text)
    check('U85 iss: активатор в {app}\\launcher рядом с остальными файлами, ярлыки и запуск указывают туда',
          'DestDir: "{app}\\launcher"' in iss_text and 'Filename: "{app}\\launcher\\WXQLauncherRU.exe"' in iss_text and iss_text.count('\\WXQLauncherRU.exe"; DestDir: "{app}"') == 0)
    check('U86 iss: результат записи target.txt и datadir.txt проверяется (Abort при сбое)', iss_text.count('if not SaveStringsToUTF8File(') == 2 and iss_text.count('Abort;') >= 2)
    check('U84 в релизных определениях ISCC есть /DWITHLAUNCHER только с лаунчером',
          '/DWITHLAUNCHER' in bi.iscc_defines(Path('s'), Path('o/x.exe'), '0', 'd', 'g', 'p', False, launcher=True) and '/DWITHLAUNCHER' not in bi.iscc_defines(Path('s'), Path('o/x.exe'), '0', 'd', 'g', 'p', False))
    check('U90 флаг русского лаунчера после всех сценариев снят', ru_flag() is None)

    print('L. состав установщика: без лаунчера и Node, конфликты кандидатов')
    sys.path.insert(0, str(INSTALLER_DIR))
    install, files = bi.combine(list(CANDIDATES), 'test', '2026-01-01')
    staging = work / 'staging'
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    bi.make_staging(install, files, staging, with_tool=False)
    names = sorted(f.name for f in staging.rglob('*'))
    check('L1 в staging нет node.exe, *.mjs, *.cjs, launcher-entry-*, artifacts', not [n for n in names if FORBIDDEN.search(n)], str(names))
    check('L2 в staging только данные пакетов 1, 4, 10, 19, значки и списки',
          set(names) == ({'install.json', 'wxq-ru.ico', 'payload-files.iss', 'payload-extract.iss', 'payload-copy.iss'} | set(bi.ui_asset_names())), str(names))
    check('L3 в установщик идут ровно двенадцать файлов пакетов 1, 4, 10, 19',
          sorted(flat for _, flat in files) == sorted(f'p{p}_{p}{s}' for p in PACKAGES for s in ('.db', '_0.db', '.info')))
    check('L4 install.json: пакеты 1, 4, 10, 19 и четыре кандидата', [p['id'] for p in install['packages']] == list(PACKAGES) and len(install['candidates']) == 4)
    check('L4a prime пакетов 1, 4, 10, 19 в списке проверок установщика',
          {r['relative'] for r in install['prime']} >= {f'{p}/{p}.db' for p in PACKAGES} | {f'{p}/{p}_0.db' for p in PACKAGES})
    include = (staging / 'payload-files.iss').read_text('utf-8-sig')
    check('L5 в списке файлов Inno нет лаунчера', not re.search(r'node|\.mjs|\.cjs|launcher-entry|artifacts|\.asar', include, re.IGNORECASE))
    check('L5a в install.json нет путей лаунчера', not re.search(r'launcher-entry|\.mjs|\.cjs|node\.exe', (staging / 'install.json').read_text('utf-8'), re.IGNORECASE))
    try:
        bi.assert_clean(install, files + [(Path('x'), 'launcher-entry-fix.mjs')], staging)
        refused = False
    except SystemExit:
        refused = True
    check('L6 сборка отказывается принять файл лаунчера', refused)
    try:
        bi.combine([CARRIER, OLD_CARRIER], 'test', '2026-01-01')
        two = False
    except SystemExit as error:
        two = 'пакет 1' in str(error)
    check('L7 два кандидата одного пакета: сборка отказывается', two)
    try:
        bi.combine([TEXTURES, OLD_TEXTURES], 'test', '2026-01-01')
        two19 = False
    except SystemExit as error:
        two19 = 'пакет 19' in str(error)
    check('L7a два кандидата пакета 19: сборка отказывается', two19)
    fake = {'packages': ['5'], 'resources': [{'id_hex': 'aa' * 8, 'package': '5'}]}
    fake2 = {'packages': ['6'], 'resources': [{'id_hex': 'aa' * 8, 'package': '6'}]}
    try:
        bi.check_conflicts([('a', fake), ('b', fake2)])
        clash = False
    except SystemExit as error:
        clash = 'ресурс' in str(error)
    check('L8 один ресурс в двух пакетах: сборка отказывается', clash)
    try:
        bi.check_conflicts([('a', fake), ('b', {'packages': ['6'], 'resources': [{'id_hex': 'bb' * 8, 'package': '6'}]})])
        fine = True
    except SystemExit:
        fine = False
    check('L9 разные пакеты и ресурсы: конфликта нет', fine)
    script = (INSTALLER_DIR / 'WXQRussian.iss').read_text('utf-8-sig')
    for phrase in ('Переведена игра. Лаунчер остаётся на китайском', 'не проверено с защитой ACE', 'Запустить игру', 'закройте игру и лаунчер',
                   'перевод нужно поставить заново', 'новое останется китайским', 'не рискуя игрой', 'картинки интерфейса', 'вывеска «Новости»'):
        check(f'L10 в тексте окна есть «{phrase}»', phrase in script)
    for old in ('правила переноса слов', 'так и задумано'):
        check(f'L10a в iss нет старой фразы «{old}»', old not in script)
    ui_cs = (INSTALLER_DIR / 'ui' / 'WXQSetupUI.cs').read_text('utf-8-sig')
    for phrase in ('Игра и лаунчер переведены', 'Часть картинок и экран загрузки остаются на китайском', 'не рискуя игрой', '"Тексты игры"', 'Картинки интерфейса'):
        check(f'U100 в тексте окна WXQSetupUI есть «{phrase}»', phrase in ui_cs)
    for old in ('правила переноса слов', 'так и задумано'):
        check(f'U100a в WXQSetupUI нет старой фразы «{old}»', old not in ui_cs)
    check('U101 iss: окно подключается только под #ifdef WithUI, извлекается во временную папку, /NOUI=1 отключает его',
          "ExtractTemporaryFile('WXQSetupUI.exe')" in script and "{param:NOUI|0}" in script and script.index('#ifdef WithUI') < script.index("ExtractTemporaryFile('WXQSetupUI.exe')"))
    known = script[script.index('function UiKnownCmd'):][:400]
    check('U102 iss: команды окна — ровно install, options, remove, launch, back, close; пути и произвольный текст не принимаются',
          sorted(re.findall(r"Cmd = '(\w+)'", known)) == ['back', 'close', 'install', 'launch', 'options', 'remove'] and 'ShellExec' not in known)
    check('U103 iss: запуск игры — через LaunchGame (оригинальный лаунчер, ShellExec), в UiExec нет Exec и путей от окна',
          'ShellExecAsOriginalUser(' in script and script[script.index('procedure UiExec'):][:2500].split('begin', 1)[1].count('Exec(') == 0)
    check('U104 ссылки окна — только белый список из пяти адресов',
          all(u in ui_cs for u in ('boosty.to/eg01stgames', 't.me/eg01stgame', 'discord.gg/Zf69zsm5dQ', 'vk.com/ego1stgames', 'youtube.com/@egoist_games_one')))
    ui_exe = work / 'ui-selftest' / 'WXQSetupUI.exe'
    ui_exe.parent.mkdir(parents=True, exist_ok=True)
    built = subprocess.run([sys.executable, str(INSTALLER_DIR / 'ui' / 'build_ui.py'), '--out', str(ui_exe), '--test-build'], capture_output=True, timeout=300)
    check('U105 WXQSetupUI.exe собирается', built.returncode == 0 and ui_exe.is_file(), (built.stdout + built.stderr).decode('utf-8', errors='replace')[-300:])
    if ui_exe.is_file():
        selftest = subprocess.run([str(ui_exe), '--selftest'], capture_output=True, timeout=180)
        check('U106 WXQSetupUI.exe --selftest проходит', selftest.returncode == 0, (selftest.stdout + selftest.stderr).decode('utf-8', errors='replace')[-300:])

    run_review_checks(work, box)
    run_secure_folder_checks(work, box, partial_exe, bi)

    check('Z настоящая игра не изменялась за время тестов (файлы пакетов 1, 4, 10, 19 и состав папки лаунчера)', real_state() == state_before)
    check('Z1 заготовка 10.db настоящей игры не менялась', sha(real_pkg10) == real_pkg10_sha)
    check('Z2 заготовка 4.db настоящей игры не менялась', sha(real_pkg4) == real_pkg4_sha)
    check_uninstall_unchanged(uninstall_before)
    failed = [name for name, ok, _ in results if not ok]
    print(f'\nИтог: {len(results) - len(failed)} из {len(results)} проверок пройдено')
    if failed:
        print('Не пройдено:', *failed, sep='\n  ')
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print('Рабочая папка:', work)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
