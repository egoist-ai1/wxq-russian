"""Сборка установщика перевода: кандидаты носителя -> один .exe (Inno Setup).

    python -X utf8 -B installer/build_installer.py --candidate build/carrier-2026-10-13-42a4bb \
        [--candidate build/pilot-2026-10-13-tex] [--output installer/dist/имя.exe]

Что делает:
  1. проверяет каждый кандидат теми же правилами, что и scripts/game_layer_deploy.py (манифест пилотного
     контракта, SHA файлов, состав ресурсов, native-проверки) — без доступа к установленной игре;
     Кандидатов может быть сколько угодно, но один пакет — один кандидат, а один ресурс — один пакет;
  2. сводит кандидаты в одно описание install.json (пакеты, ожидаемые SHA prime, список «наших прежних» файлов);
  3. собирает WXQTool.exe штатным csc.exe из .NET Framework 4 (есть в любой Windows 10/11);
  4. раскладывает всё во временную staging-папку и вызывает ISCC.
Русский лаунчер входит только с --launcher-build build/launcher-ru-*: активатор WXQLauncherRU.exe (csc, релиз) и шесть файлов
папки launcher/ рядом с ним (модуль, словарь, профили и три файла, которые читает runtime) с проверкой SHA по manifest.json сборки. Без параметра staging проверяется на Node,
*.mjs, *.cjs, launcher-entry-*, artifacts, лаунчера в нём нет. Node и любые другие файлы лаунчера не принимаются никогда.
В игру, в build/ и в rollback/ ничего не пишется.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

sys.dont_write_bytecode = True
INSTALLER = Path(__file__).resolve().parent
PROJECT = INSTALLER.parent
sys.path.insert(0, str(PROJECT / 'scripts'))

ISCC = Path.home() / '.nuget/packages/dotnet-innosetup/6.2.1/tools/is/ISCC.exe'
CSC = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
PRODUCT = 'Egoist · Русификатор Honor of Kings ACE'
HEADER_ID = 0xe12bade797f29f55
BUILD_ROOT = (PROJECT / 'build').resolve()
GAME_ROOT: Path | None = None  # папка игры для повторной сборки Lua-нагрузок (по умолчанию — установка, найденная лаунчером)
CANDIDATE_PREFIXES = ('pilot-', 'carrier-', 'lua-wrap-')
# Из русского лаунчера в установщик входят только активатор и три файла (белый список): ни Node, ни другие скрипты, ни входные точки, ни artifacts.
LAUNCHER_PREFIX = 'launcher-ru-'
# Все файлы, которые runtime читает из своей папки (loadPayload: словарь, draft, css, png), плюс profiles.json активатора. Активатор тоже лежит в launcher\.
LAUNCHER_FILES = ('launcher-ru-runtime.cjs', 'launcher-ru.json', 'profiles.json',
                  'launcher-entry-public-ru.draft.json', 'launcher-layout.css', 'main-logo-title.a73b14e6.png')   # ставятся в {app}\launcher\
LAUNCHER_STAGED = {'launcher/WXQLauncherRU.exe'} | {'launcher/' + name for name in LAUNCHER_FILES}   # единственное, что с «лаунчером» может лежать в staging
FORBIDDEN_NAME = re.compile(r'(^node(\.exe)?$|\.(mjs|cjs)$|^launcher-entry|^artifacts?$|\.asar$)', re.IGNORECASE)


def sha(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def fail(message: str) -> None:
    raise SystemExit('Ошибка сборки: ' + message)


def load_launcher_build(directory: Path) -> dict:
    """Проверка сборки перевода лаунчера build/launcher-ru-*: манифест и SHA всех файлов LAUNCHER_FILES. Возвращает манифест."""
    directory = directory.resolve(strict=True)
    if directory.parent != BUILD_ROOT or not directory.name.startswith(LAUNCHER_PREFIX):
        fail(f'{directory}: сборка лаунчера должна лежать в build/{LAUNCHER_PREFIX}*')
    manifest = json.loads((directory / 'manifest.json').read_text('utf-8-sig'))
    files = manifest.get('files')
    if manifest.get('schema') != 1 or not isinstance(files, dict):
        fail(f'{directory.name}: нет манифеста сборки лаунчера (schema 1 и files)')
    for name in LAUNCHER_FILES:
        source = directory / name
        if not source.is_file() or source.is_symlink() or sha(source) != files.get(name):
            fail(f'{directory.name}: файл {name} не совпадает с manifest.json')
    if files['launcher-ru-runtime.cjs'] != manifest.get('module_sha256') or files['launcher-ru.json'] != manifest.get('dictionary_sha256'):
        fail(f'{directory.name}: module_sha256 или dictionary_sha256 расходятся с files')
    for field in ('module_sha256', 'dictionary_sha256'):
        if not re.fullmatch(r'[0-9a-f]{64}', str(manifest.get(field))):
            fail(f'{directory.name}: {field} не SHA-256')
    profiles = json.loads((directory / 'profiles.json').read_text('utf-8-sig'))
    if profiles.get('schema') != 1 or not profiles.get('builds'):
        fail(f'{directory.name}: profiles.json без сборок лаунчера')
    return manifest


def expected_text(manifest: dict) -> str:
    """Ресурс launcher-expected.txt активатора. Ключи ровно runtime_sha256 и dictionary_sha256 (так читает LoadExpected в WXQLauncherRU.cs);
    файл launcher-expected.txt в сборке перевода (module=, dictionary=) для активатора не годится и не используется."""
    return f'runtime_sha256={manifest["module_sha256"]}\ndictionary_sha256={manifest["dictionary_sha256"]}\n'


def build_activator(staging: Path, manifest: dict) -> None:
    """WXQLauncherRU.exe: релизная сборка (без TESTBUILD), манифест asInvoker, SHA модуля и словаря зашиты ресурсом."""
    if not CSC.is_file():
        fail(f'не найден csc.exe из .NET Framework 4: {CSC}')
    with tempfile.TemporaryDirectory(prefix='wxq-ru-expected-') as temporary:
        expected = Path(temporary) / 'launcher-expected.txt'
        expected.write_text(expected_text(manifest), encoding='utf-8', newline='\n')
        completed = subprocess.run([str(CSC), '/nologo', '/warnaserror+', '/optimize+', '/platform:x64', '/target:winexe', '/codepage:65001',
                                    f'/win32manifest:{INSTALLER / "WXQLauncherRU.manifest"}', '/r:System.Management.dll', '/r:System.Web.Extensions.dll',
                                    f'/resource:{expected},launcher-expected.txt', f'/out:{staging / "launcher" / "WXQLauncherRU.exe"}',
                                    str(INSTALLER / 'WXQLauncherRU.cs')], capture_output=True)
    if completed.returncode:
        fail('csc не собрал WXQLauncherRU.cs:\n' + (completed.stdout + completed.stderr).decode('cp866', errors='replace'))


def stage_launcher(staging: Path, directory: Path, manifest: dict) -> None:
    """Активатор и файлы лаунчера в staging/launcher (так же они лягут в {app}\\launcher: активатор читает target.txt рядом с собой);
    копируются только перечисленные файлы, SHA сверяются заново."""
    target = staging / 'launcher'
    target.mkdir()
    for name in LAUNCHER_FILES:
        shutil.copyfile(directory / name, target / name)
        if sha(target / name) != manifest['files'][name]:
            fail(f'{name}: SHA после копирования не совпадает с manifest.json')
    build_activator(staging, manifest)


def load_candidate(directory: Path) -> dict:
    """Проверка кандидата. Возвращает его манифест. Повторяет проверки load_candidate из game_layer_deploy; оригиналы prime читаются из игры (только чтение)."""
    import pilot_contract
    from game_inspect import QtsContainer
    directory = directory.resolve(strict=True)
    if directory.parent != BUILD_ROOT or not directory.name.startswith(CANDIDATE_PREFIXES):
        fail(f'{directory}: кандидат должен лежать в build/pilot-*, build/carrier-* или build/lua-wrap-*')
    manifest = json.loads((directory / 'manifest.json').read_text('utf-8-sig'))
    if manifest.get('resource_contract') != pilot_contract.CONTRACT:
        fail(f'{directory.name}: не контракт пилотного носителя')
    if manifest['headers_equal_observed_runtime_cache'] is not True or manifest['complete_prime_global_index_unchanged'] is not True \
            or manifest['deletion_records'] != 0 or manifest['vendor_diff_called'] is not False:
        fail(f'{directory.name}: нет подтверждения native-проверки')
    packages = pilot_contract.require_shape(manifest)
    if directory.name.startswith('lua-wrap-'):
        if {r.get('kind') for r in manifest['resources']} != {'lua-script'} or packages != ['10'] or not isinstance(manifest.get('lua_wrap'), dict):
            fail(f'{directory.name}: lua-wrap-* должен состоять только из Lua-скриптов пакета 10')
        verify_lua_regeneration(directory, manifest)
    if directory.name.startswith('carrier-'):
        info = manifest.get('carrier')
        if not isinstance(info, dict) or info.get('subset') is not None or info.get('tm_status') != 'approved':
            fail(f'{directory.name}: carrier-* должен быть полной сборкой из утверждённой памяти переводов')
    expected = {f'packages/{p}/{n}' for p in packages for n in (p + '.db', p + '_0.db', p + '.info')}
    if {row['relative'] for row in manifest['files']} != expected or len(manifest['files']) != len(expected):
        fail(f'{directory.name}: состав файлов не совпадает с тройками по пакетам')
    for row in manifest['files']:
        source = (directory / row['relative']).resolve(strict=True)
        if not source.is_relative_to(directory) or sha(source) != row['sha256'] or source.stat().st_size != row['bytes']:
            fail(f'{directory.name}: файл {row["relative"]} не совпадает с манифестом')
    # Оригиналы из prime нужны всегда: текстовые ресурсы доказываются заново, у текстур длина потока сверяется с оригиналом
    # (игра читается только на чтение; нет игры нужной версии - отказ).
    import wxq_install
    try:
        game = wxq_install.require_install_structure(GAME_ROOT if GAME_ROOT else wxq_install.detect_active_install(), full=True)
    except wxq_install.InstallError as error:
        fail(f'{directory.name}: нет установленной игры, чтобы сверить ресурсы с оригиналами prime ({error}); укажите --game-root')
    reader = pilot_contract.make_prime_reader(game, QtsContainer)
    try:
        pilot_contract.validate_carrier(directory, manifest, QtsContainer, HEADER_ID, prime_reader=reader)
    except ValueError as error:
        fail(f'{directory.name}: {error} (оригиналы читаются из игры {game}; нужна та же версия, что у кандидата, при необходимости укажите --game-root)')
    for package in packages:
        if sha(directory / f'packages/{package}/{package}.info') != manifest['official_cache_infos'][package]['sha256']:
            fail(f'{directory.name}: .info пакета {package} отличается от официального')
    verification = manifest['native_verification']
    if verification.get('writes') or verification.get('phase') != 'verify' or not all(r['matches'] is True for r in verification['reads']):
        fail(f'{directory.name}: нет полной native-проверки чтения')
    return manifest


def verify_lua_regeneration(directory: Path, manifest: dict) -> None:
    """Нагрузки Lua кандидата воспроизводятся заново из оригиналов prime тем же patch_lua и тем же помощником.

    Оригиналы читаются из установленной игры только на чтение (SHA сверяются с закреплёнными в pilot_contract).
    Без игры в сборочной среде проверку сделать нечем, и сборка отказывается: нагрузка, которую нельзя воспроизвести
    из оригинала, в установщик не попадает.
    """
    import build_lua_package as blp
    import wxq_install
    lua = manifest['lua_wrap']
    diag_log = bool(lua.get('diag_log'))
    helper_sha = blp.helper_sha_of(diag_log)   # обычная сборка: SHA файла помощника; --diag-log: шаблон помощник + диагностический слой
    if lua.get('helper_sha256') != helper_sha or manifest.get('content_sha256') != helper_sha:
        fail(f'{directory.name}: помощник {blp.HELPER.name} изменился после сборки кандидата (SHA {helper_sha} вместо {lua.get("helper_sha256")}); пересоберите кандидат')
    try:
        game = wxq_install.require_install_structure(GAME_ROOT if GAME_ROOT else wxq_install.detect_active_install(), full=True)
        regenerated = {r['id_hex']: r for r in blp.read_resources(game, bool(lua.get('diagnostic_marker')), diag_log)}
    except (wxq_install.InstallError, blp.LuaPackageError, blp.bp.PilotError) as error:
        fail(f'{directory.name}: не удалось заново получить Lua-нагрузки из оригиналов prime ({error}); укажите --game-root')
    declared = {r['id_hex']: r for r in manifest['resources']}
    if set(regenerated) != set(declared):
        fail(f'{directory.name}: набор Lua-ресурсов кандидата не совпадает с набором помощника сборки')
    for ident, row in declared.items():
        fresh = regenerated[ident]
        # Сам файл кандидата сверяется с манифестом в validate_carrier; здесь манифест сверяется с повторной сборкой.
        if fresh['sha256'] != row['sha256'] or fresh['original_sha256'] != row['original_sha256']:
            fail(f'{directory.name}: Lua-нагрузка {ident} не воспроизводится из оригинала prime (SHA {fresh["sha256"]} вместо {row["sha256"]})')
    for dependency in manifest.get('lua_replacements', []):
        if dependency['sha256'] != declared[dependency['id_hex']]['sha256']:
            fail(f'{directory.name}: объявление Lua-замены {dependency["id_hex"]} расходится с ресурсом')


def known_hashes(game_version: str) -> dict[str, set[str]]:
    """SHA файлов override, которые написали мы (кандидаты build/ и receipts) для этой версии игры."""
    known: dict[str, set[str]] = {}

    def add(relative: str, value: str) -> None:
        known.setdefault(relative, set()).add(value)

    for path in [p for prefix in CANDIDATE_PREFIXES for p in BUILD_ROOT.glob(prefix + '*/manifest.json')]:
        try:
            data = json.loads(path.read_text('utf-8-sig'))
        except (OSError, ValueError):
            continue
        if data.get('game_version') != game_version:
            continue
        for row in data.get('files', []):
            add(row['relative'], row['sha256'])
    for path in (PROJECT / 'rollback/game-layer').glob('deployment-*/receipt.json'):
        try:
            data = json.loads(path.read_text('utf-8-sig'))
        except (OSError, ValueError):
            continue
        if data.get('game_version') != game_version:
            continue
        for row in data.get('operations', []):
            add(row['relative'], row['candidate_sha256'])
    return known


def check_conflicts(manifests: list[tuple[str, dict]]) -> None:
    """Кандидаты в одном установщике не должны пересекаться: один пакет — один кандидат, один ресурс — один пакет.

    Пакет делится между кандидатами только целиком (файлы пакета заменяются как тройка), поэтому два кандидата
    одного пакета дали бы тот же файл с разным содержимым. Тот же ресурс в разных пакетах игра прочтёт неоднозначно.
    """
    owner: dict[str, str] = {}
    resource_owner: dict[str, tuple[str, str]] = {}
    for name, manifest in manifests:
        for package in manifest['packages']:
            if package in owner:
                fail(f'пакет {package} есть в двух кандидатах ({owner[package]} и {name}); соберите один кандидат на пакет')
            owner[package] = name
        for row in manifest['resources']:
            key = str(row['id_hex'])
            old = resource_owner.get(key)
            if old and old[0] != name:
                fail(f'ресурс {key} переопределён в двух кандидатах ({old[0]}, пакет {old[1]}; {name}, пакет {row["package"]}); так установщик не соберётся')
            resource_owner[key] = (name, str(row['package']))


def package_resources(directory: Path, manifest: dict, package: str, missing: list[str]) -> list[dict]:
    """Ресурсы пакета для install.json: id_hex, kind, SHA оригинала prime и нагрузки, размер декодированной нагрузки, число чанков.
    Размер и число чанков берутся из самого <N>_0.db кандидата (validate_carrier уже сверил SHA нагрузок с манифестом).
    Ресурс без корректного original_sha256 попадает в missing (combine потом останавливает сборку)."""
    from game_inspect import QtsContainer
    rows = []
    with QtsContainer(directory / 'packages' / package / f'{package}_0.db') as data:
        for resource in sorted((r for r in manifest['resources'] if r['package'] == package), key=lambda r: r['id_hex']):
            original = resource.get('original_sha256')
            if not isinstance(original, str) or not re.fullmatch(r'[0-9a-f]{64}', original):
                missing.append(f'{package}/{resource.get("id_hex")}')
                continue
            chunks = data.entries[int(resource['id_hex'], 16)]
            rows.append({'id_hex': resource['id_hex'], 'kind': resource['kind'], 'original_sha256': original, 'sha256': resource['sha256'],
                         'bytes': sum(c.uncompressed_size for c in chunks), 'chunks': len(chunks)})
    return rows


def combine(candidates: list[Path], version: str, built: str) -> tuple[dict, list[tuple[Path, str]]]:
    """Сводит кандидаты. Возвращает install.json и список (файл, имя в установщике)."""
    manifests = [(directory, load_candidate(directory)) for directory in candidates]
    check_conflicts([(directory.resolve().name, manifest) for directory, manifest in manifests])
    first = manifests[0][1]
    game_version = first['game_version']
    packages: dict[str, dict] = {}
    prime: dict[str, dict] = {}
    files: list[tuple[Path, str]] = []
    missing_original: list[str] = []
    for directory, manifest in manifests:
        resolved = directory.resolve()
        for key in ('game_version', 'prime_global_index_sha256', 'native_sdk_hashes'):
            if manifest[key] != first[key]:
                fail(f'{resolved.name}: {key} расходится с первым кандидатом (кандидаты собраны для разных сборок игры)')
        for package in manifest['packages']:
            entry = {
                'id': package,
                'prime_pkg_hash': str(manifest['prime_pkg_hashes'][package]),
                'header_hex': manifest['override_package_headers_hex'][package],
                'info_sha256': manifest['official_cache_infos'][package]['sha256'],
                'info_version': manifest['official_cache_infos'][package]['data']['version'],
                'files': [],
            }
            for row in manifest['files']:
                parts = row['relative'].split('/')
                if parts[1] != package:
                    continue
                flat = f'p{package}_{parts[2]}'
                files.append((resolved / row['relative'], flat))
                entry['files'].append({'name': parts[2], 'bytes': row['bytes'], 'sha256': row['sha256'], 'payload': flat})
            entry['resources'] = package_resources(resolved, manifest, package, missing_original)
            packages[package] = entry
        for row in manifest['source_copies']:
            relative = row['relative']
            old = prime.get(relative)
            if old and old['sha256'] != row['sha256']:
                fail(f'prime {relative}: SHA расходится между кандидатами')
            prime[relative] = {'relative': relative, 'bytes': row['bytes'], 'sha256': row['sha256']}
    if missing_original:
        fail('у ресурсов нет original_sha256 в manifest.json (пересадка записей без него невозможна): ' + ', '.join(missing_original))
    known = known_hashes(game_version)
    # QtskDB.dll, с которой разрешена пересадка записей в более новую версию игры: библиотека сборки и закреплённая в воркере (1.4.0.1).
    from game_layer_native_worker import DLL_HASHES
    sdk_known = sorted({first['native_sdk_hashes']['QtskDB.dll'], DLL_HASHES['QtskDB.dll']})
    install = {
        'product': PRODUCT,
        'version': version,
        'built': built,
        'game_version': game_version,
        'index_sha256': first['prime_global_index_sha256'],
        'sdk_hashes': first['native_sdk_hashes'],
        'sdk_known': sdk_known,
        'candidates': [d.resolve().name for d, _ in manifests],
        'packages': [packages[p] for p in sorted(packages, key=int)],
        'prime': [prime[k] for k in sorted(prime)],
        'known': {k: sorted(v) for k, v in sorted(known.items())},
    }
    return install, files


# ---- графика окна: сдержанный графит и один тёплый акцент; без текста в картинках ---------------------------------
# Все растры отдаются заранее нарисованными в натуральных пикселях для каждого масштаба Windows (суперсэмплинг x8 и LANCZOS вниз
# до точного размера); Inno выбирает вариант по ScaleX(100) и не растягивает картинку. Текст в картинки не попадает вообще.

UI_SCALES = (100, 125, 150, 175, 200, 250)
GRAPHITE = (0x0E, 0x0F, 0x12)      # фон окна
SURFACE = (0x15, 0x17, 0x1C)
LINE_STRONG = (0x4A, 0x4D, 0x57)
GOLD = (0xC8, 0xA5, 0x5A)
IVORY = (0xEC, 0xE8, 0xDF)
MUTED_RING = (0x6B, 0x6D, 0x76)
GREEN = (0x7B, 0xAE, 0x7F)
RED = (0xD0, 0x60, 0x5E)
INK = (0x14, 0x12, 0x0C)           # тёмный знак на золоте, зелёном и красном
SS = 8                             # суперсэмплинг

MARK_DP, CHECK_DP, DOT_DP = 56, 20, 16     # логические размеры (при 100 %): знак шапки, флажок, маркер шага
WINDOW_DP, HEADER_DP = 600, 96             # окно установщика и его шапка (при 100 %)
ART_SHARE, ART_BRIGHT, ART_RAMP = 0.62, 0.7, 1.6   # арт: доля ширины окна справа, яркость, степень растворения слева
ART_FROM_DP = 376                          # левее этого x (окно, 100 %) арта нет: там заканчиваются подписи шапки
BRAND = INSTALLER / 'brand'
DOT_STATES = ('wait', 'act', 'done', 'fail')


def px(logical: float, pct: int) -> int:
    """Логические пиксели -> физические при масштабе pct (округление к ближайшему, как MulDiv в Inno)."""
    return int(logical * pct / 100 + 0.5)


def ui_asset_names() -> list[str]:
    """Растры окна (по шесть масштабов); попадают в установщик как dontcopy и распаковываются по одному варианту."""
    names = []
    for pct in UI_SCALES:
        names.append(f'mark-{pct}.bmp')
        names.append(f'header-{pct}.bmp')
        names += [f'check-off-{pct}.bmp', f'check-on-{pct}.bmp']
        names += [f'dot-{state}-{pct}.bmp' for state in DOT_STATES]
    return names


def _canvas(size: int, fill):
    from PIL import Image
    return Image.new('RGBA', (size * SS, size * SS), fill)


def _down(image, size: int, exact: bool = False):
    """Вниз до точного размера. exact — фигуры по целым пикселям (значок до 32 px): усреднение без «звона» LANCZOS на жёстких краях."""
    from PIL import Image
    return image.resize((size, size), Image.BOX if exact else Image.LANCZOS)


def monogram(size: int, outline=GOLD, letter=IVORY, tile=GRAPHITE):
    """Знак: тонкая рамка со скруглёнными углами и строгая геометрическая «E». Без короны и декора. RGBA size x size, углы прозрачные."""
    from PIL import ImageDraw
    image = _canvas(size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    stroke = max(1, round(size / 24))
    radius = round(size * 0.2)
    draw.rounded_rectangle([0, 0, size * SS - 1, size * SS - 1], radius=radius * SS, fill=tile + (255,),
                           outline=outline + (255,), width=stroke * SS)
    bar = max(1, round(size * 0.062))
    gap = max(1, round(size * 0.098))
    height = 3 * bar + 2 * gap
    width = round(size * 0.29)
    left = (size - width) // 2 + round(size * 0.01)
    top = (size - height) // 2
    short = width - round(size * 0.04)
    for index, length in enumerate((width, short, width)):
        y = top + index * (bar + gap)
        draw.rectangle([left * SS, y * SS, (left + length) * SS - 1, (y + bar) * SS - 1], fill=letter + (255,))
    draw.rectangle([left * SS, top * SS, (left + bar) * SS - 1, (top + height) * SS - 1], fill=letter + (255,))
    return _down(image, size)


def _brand_glyph(name: str):
    """Знак из brand/: золотой RGBA по границам рисунка; цвет целиком из золота акцента, форма из альфа-канала (без каймы). None, если файла нет."""
    from PIL import Image
    path = BRAND / name
    if not path.is_file():
        return None
    alpha = Image.open(path).convert('RGBA').split()[3]
    box = alpha.point(lambda v: 255 if v > 8 else 0).getbbox()
    if not box:
        return None
    alpha = alpha.crop(box)
    glyph = Image.new('RGBA', alpha.size, GOLD + (255,))
    glyph.putalpha(alpha)
    return glyph


def _fit(glyph, box_w: int, box_h: int):
    """Вписывает знак в прямоугольник с сохранением пропорций (LANCZOS)."""
    from PIL import Image
    ratio = min(box_w / glyph.width, box_h / glyph.height)
    return glyph.resize((max(1, round(glyph.width * ratio)), max(1, round(glyph.height * ratio))), Image.LANCZOS)


def draw_mark(pct: int):
    """Знак шапки 56 dp: шахматный конь в рамке (brand/mark-knight-frame.png); без файла прежняя монограмма."""
    from PIL import Image
    size = px(MARK_DP, pct)
    flat = Image.new('RGB', (size, size), GRAPHITE)
    glyph = _brand_glyph('mark-knight-frame.png')
    glyph = _fit(glyph, size, size) if glyph else monogram(size)
    flat.paste(glyph, ((size - glyph.width) // 2, (size - glyph.height) // 2), glyph)
    return flat


def _art_file():
    found = sorted((BRAND / 'source').glob('game-art-sun-*.png'))
    return found[0] if found else None


def draw_header(pct: int):
    """Правая часть шапки: арт игры (спираль Sun), яркость x0,7, растворение слева в графит (маска ramp**1,6); в натуральных пикселях.
    Возвращает только полосу арта (ART_SHARE ширины окна); остальное в шапке плоский графит окна. Без файла арта полоса плоская графитовая.
    Подписи шапки живые надписи с непрозрачным фоном графита, поэтому арт вовсе не заходит левее ART_FROM_DP: рамки надписей на графите не видны."""
    from PIL import Image
    import numpy as np
    width = round(px(WINDOW_DP, pct) * ART_SHARE)
    height = px(HEADER_DP, pct)
    strip = Image.new('RGB', (width, height), GRAPHITE)
    source = _art_file()
    if source is not None:
        art = Image.open(source).convert('RGB')
        scaled_h = round(width * art.height / art.width)
        art = art.resize((width, scaled_h), Image.LANCZOS)
        top = int((scaled_h - height) * 0.45)
        art = art.crop((0, top, width, top + height))
        shade = np.asarray(art).astype(float) * ART_BRIGHT
        start = px(ART_FROM_DP, pct) - (px(WINDOW_DP, pct) - width)   # где арт начинает проступать (в полосе)
        ramp = np.clip(np.clip(np.linspace(-start, width - start, width) / (width - start), 0, 1) ** ART_RAMP * 1.25, 0, 1)
        base = np.array(GRAPHITE, dtype=float)
        mixed = base + (shade - base) * ramp[None, :, None]
        strip = Image.fromarray(np.clip(mixed + 0.5, 0, 255).astype('uint8'))
    return strip


def draw_check(on: bool, pct: int):
    """Флажок 20 dp: выключен — контур, включён — золотая заливка с тёмной галочкой. Фон — фон окна."""
    from PIL import ImageDraw
    size = px(CHECK_DP, pct)
    image = _canvas(size, GRAPHITE + (255,))
    draw = ImageDraw.Draw(image)
    border = max(1, px(1, pct))
    radius = px(4, pct)
    box = [0, 0, size * SS - 1, size * SS - 1]
    if on:
        draw.rounded_rectangle(box, radius=radius * SS, fill=GOLD + (255,))
        width = max(2, px(2, pct))
        path = [(x * size * SS, y * size * SS) for x, y in ((0.27, 0.52), (0.43, 0.68), (0.74, 0.34))]
        draw.line(path, fill=INK + (255,), width=width * SS, joint='curve')
        for x, y in (path[0], path[-1]):
            r = width * SS / 2
            draw.ellipse([x - r, y - r, x + r, y + r], fill=INK + (255,))
    else:
        draw.rounded_rectangle(box, radius=radius * SS, fill=SURFACE + (255,), outline=LINE_STRONG + (255,), width=border * SS)
    return _down(image, size).convert('RGB')


def draw_dot(state: str, pct: int):
    """Маркер шага 16 dp с кругом 12 dp: ожидание — кольцо, идёт — золотое кольцо с точкой, готово — зелёный с галочкой, сбой — красный с крестом."""
    from PIL import ImageDraw
    size = px(DOT_DP, pct)
    image = _canvas(size, GRAPHITE + (255,))
    draw = ImageDraw.Draw(image)
    diameter = px(12, pct)
    offset = (size - diameter) / 2
    ring = max(1, px(1.5, pct))
    area = [offset * SS, offset * SS, (offset + diameter) * SS - 1, (offset + diameter) * SS - 1]
    k = size * SS
    cx = k / 2
    if state == 'wait':
        draw.ellipse(area, outline=MUTED_RING + (255,), width=ring * SS)
    elif state == 'act':
        draw.ellipse(area, outline=GOLD + (255,), width=ring * SS)
        r = diameter * SS * 0.20
        draw.ellipse([cx - r, cx - r, cx + r, cx + r], fill=GOLD + (255,))
    else:
        draw.ellipse(area, fill=(GREEN if state == 'done' else RED) + (255,))
        width = max(1, px(1.5, pct)) * SS
        if state == 'done':
            draw.line([(0.33 * k, 0.52 * k), (0.45 * k, 0.64 * k), (0.68 * k, 0.37 * k)], fill=INK + (255,), width=width, joint='curve')
        else:
            for a, b in (((0.38, 0.38), (0.62, 0.62)), ((0.62, 0.38), (0.38, 0.62))):
                draw.line([(a[0] * k, a[1] * k), (b[0] * k, b[1] * k)], fill=INK + (255,), width=width)
    return _down(image, size).convert('RGB')


def draw_icon(size: int):
    """Кадр значка: графитовая плитка с прозрачными углами и золотой шахматный конь. От 48 px конь в рамке; до 32 px рамки нет,
    конь крупнее и занимает целое число пикселей по высоте (чёткие края). Без brand/ прежняя монограмма."""
    from PIL import Image, ImageDraw, ImageFilter
    frame, knight = _brand_glyph('mark-knight-frame.png'), _brand_glyph('mark-knight.png')
    if frame is None or knight is None:
        return monogram(size) if size >= 48 else _legacy_icon(size)
    radius = {16: 3, 20: 4, 24: 4, 32: 6}.get(size, round(size * 0.18))
    tile = Image.new('RGBA', (size * SS, size * SS), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle([0, 0, size * SS - 1, size * SS - 1], radius=radius * SS, fill=GRAPHITE + (255,))
    tile = _down(tile, size)
    if size >= 48:
        glyph = _fit(frame, round(size * 0.80), round(size * 0.80))
    else:
        glyph = _fit(knight, size - 4, size - 4 if size > 16 else 13)
        glyph = glyph.filter(ImageFilter.UnsharpMask(radius=0.6, percent=120, threshold=0))
    tile.alpha_composite(glyph, ((size - glyph.width) // 2, (size - glyph.height) // 2))
    return tile


def _legacy_icon(size: int):
    """Запасной значок без brand/: золотая плитка и тёмная «E» по целым пикселям."""
    from PIL import ImageDraw
    # (ширина E, высота E, планка, корешок, радиус плитки): три планки и два равных зазора по целым пикселям
    width, height, bar, spine, radius = {16: (6, 8, 2, 2, 2), 20: (8, 10, 2, 2, 3), 24: (10, 12, 2, 2, 3), 32: (12, 14, 2, 3, 4)}[size]
    image = _canvas(size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([0, 0, size * SS - 1, size * SS - 1], radius=radius * SS, fill=GOLD + (255,))
    left, top = (size - width) // 2, (size - height) // 2
    gap = (height - 3 * bar) // 2
    for index in range(3):
        y = top + index * (bar + gap)
        draw.rectangle([left * SS, y * SS, (left + width) * SS - 1, (y + bar) * SS - 1], fill=INK + (255,))
    draw.rectangle([left * SS, top * SS, (left + spine) * SS - 1, (top + height) * SS - 1], fill=INK + (255,))
    return _down(image, size, exact=True)


ICON_SIZES = (16, 20, 24, 32, 48, 64, 256)


def icon_bytes() -> bytes:
    """ICO с отдельно нарисованными кадрами: 256 — PNG, остальные — несжатые 32-битные DIB с маской."""
    import io
    import struct
    entries = []
    for size in ICON_SIZES:
        frame = draw_icon(size).convert('RGBA')
        if size == 256:
            buffer = io.BytesIO()
            frame.save(buffer, format='PNG')
            data = buffer.getvalue()
        else:
            header = struct.pack('<IiiHHIIiiII', 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
            pixels = frame.load()
            xor = bytearray()
            for y in range(size - 1, -1, -1):
                for x in range(size):
                    r, g, b, a = pixels[x, y]
                    xor += bytes((b, g, r, a))
            stride = ((size + 31) // 32) * 4
            data = header + bytes(xor) + bytes(stride * size)   # маска AND нулевая: прозрачность берётся из альфа-канала
        entries.append((size, data))
    out = bytearray(struct.pack('<HHH', 0, 1, len(entries)))
    offset = 6 + 16 * len(entries)
    for size, data in entries:
        out += struct.pack('<BBBBHHII', 0 if size == 256 else size, 0 if size == 256 else size, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return bytes(out) + b''.join(data for _, data in entries)


def write_assets(staging: Path) -> None:
    (staging / 'wxq-ru.ico').write_bytes(icon_bytes())
    for pct in UI_SCALES:
        draw_mark(pct).save(staging / f'mark-{pct}.bmp', format='BMP')
        draw_header(pct).save(staging / f'header-{pct}.bmp', format='BMP')
        draw_check(False, pct).save(staging / f'check-off-{pct}.bmp', format='BMP')
        draw_check(True, pct).save(staging / f'check-on-{pct}.bmp', format='BMP')
        for state in DOT_STATES:
            draw_dot(state, pct).save(staging / f'dot-{state}-{pct}.bmp', format='BMP')


UPDATE_KEYS = PROJECT / 'keys' / 'keys.json'
UPDATE_REPO = re.compile(r'^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$')


def write_update_config(staging: Path, repo: str) -> Path:
    """update-config.json: адрес канала обновлений (GitHub Releases) и публичные ключи подписи (основной и запасной) из release-repo/keys/keys.json.
    Файл вшивается в WXQTool.exe ресурсом; закрытых ключей здесь нет и быть не может."""
    if not UPDATE_REPO.fullmatch(repo):
        fail(f'--update-repo должен быть вида владелец/репозиторий, получено: {repo!r}')
    if not UPDATE_KEYS.is_file():
        fail(f'нет файла публичных ключей: {UPDATE_KEYS}')
    keys = json.loads(UPDATE_KEYS.read_text('utf-8-sig'))['keys']
    rows = [{'key_id': row['key_id'], 'public_key_pem': row['public_key_pem']} for row in keys]
    if not rows or any('PRIVATE' in row['public_key_pem'] for row in rows):
        fail('в keys.json нет публичных ключей (или там закрытый ключ)')
    path = staging / 'update-config.json'
    path.write_text(json.dumps({'schema': 1, 'base_url': f'https://github.com/{repo}/releases/latest/download/', 'keys': rows}, ensure_ascii=False, indent=1), encoding='utf-8')
    return path


def build_tool(staging: Path, test_build: bool = False, update_config: Path | None = None) -> None:
    """WXQTool.exe. test_build (/define:TESTBUILD) только для installer/tests: корень доверия лаунчера задаёт WXQ_RU_TRUST_ROOT.
    update_config (update-config.json) вшивается ресурсом: адрес канала и публичные ключи для команды update."""
    if not CSC.is_file():
        fail(f'не найден csc.exe из .NET Framework 4: {CSC}')
    env = dict(os.environ)
    # Только структура пустой QtsVFS-таблицы; заголовок пакета заменяется проверенным заголовком установленной игры.
    template = INSTALLER / 'tests/fixtures/10.db.placeholder'
    if sha(template) != 'a997d0bb53887d5a3ed45152700ba5130dc140a8afe3be85de4361819897a51a':
        fail('шаблон пустого кэша повреждён')
    completed = subprocess.run([str(CSC), '/nologo', '/target:exe', '/platform:x64', '/optimize+', '/codepage:65001',
                                *(['/define:TESTBUILD'] if test_build else []), '/r:System.Web.Extensions.dll', '/r:Microsoft.CSharp.dll',
                                f'/resource:{template},cache-template.db',
                                *([f'/resource:{update_config},update-config.json'] if update_config else []),
                                f'/out:{staging / "WXQTool.exe"}', str(INSTALLER / 'WXQTool.cs')],
                               capture_output=True, env=env)
    if completed.returncode:
        text = (completed.stdout + completed.stderr).decode('cp866', errors='replace')
        fail('csc не собрал WXQTool.cs:\n' + text)


def build_tray(staging: Path) -> None:
    """WXQTray.exe (значок в области уведомлений, без прав администратора) из installer/WXQTray.cs."""
    if not CSC.is_file():
        fail(f'не найден csc.exe из .NET Framework 4: {CSC}')
    icon = staging / 'wxq-ru.ico'
    completed = subprocess.run([str(CSC), '/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/codepage:65001', '/warnaserror+',
                                '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll', '/r:System.Web.Extensions.dll',
                                *([f'/win32icon:{icon}'] if icon.is_file() else []),
                                f'/out:{staging / "WXQTray.exe"}', str(INSTALLER / 'WXQTray.cs')], capture_output=True)
    if completed.returncode:
        fail('csc не собрал WXQTray.cs:\n' + (completed.stdout + completed.stderr).decode('cp866', errors='replace'))


def build_ui(staging: Path, test_build: bool = False) -> None:
    """WXQSetupUI.exe (видимое окно установщика, WPF) из installer/ui. test_build (/define:TESTBUILD) добавляет автопилот для installer/tests."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('wxq_build_ui', INSTALLER / 'ui' / 'build_ui.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        module.build(str(staging / 'WXQSetupUI.exe'), test_build)
    except RuntimeError as error:
        fail('csc не собрал WXQSetupUI.cs:' + chr(10) + str(error))


def write_include(staging: Path, files: list[tuple[Path, str]], launcher: bool = False) -> None:
    lines = [f'Source: "{staging / "install.json"}"; DestName: "install.json"; Flags: dontcopy',
             f'Source: "{staging / "WXQTool.exe"}"; DestName: "WXQTool.exe"; Flags: dontcopy']
    if (staging / 'WXQSetupUI.exe').is_file():   # окно установщика (WPF): мастер извлекает его в {tmp}, копирует в защищённую папку SecDir и запускает оттуда; без него мастер остаётся на стандартных страницах Inno
        lines.append(f'Source: "{staging / "WXQSetupUI.exe"}"; DestName: "WXQSetupUI.exe"; Flags: dontcopy')
    lines += [f'Source: "{staging / name}"; DestName: "{name}"; Flags: dontcopy' for name in ui_asset_names()]   # растры окна по масштабам; код берёт один вариант
    extract = ['  ExtractTemporaryFile(\'install.json\');']
    if launcher:   # список сборок лаунчера нужен помощнику (find) до установки файлов; в {app} эти же байты кладёт [Files] из .iss
        lines.append(f'Source: "{staging / "launcher" / "profiles.json"}"; DestName: "launcher-profiles.json"; Flags: dontcopy')
        extract.append("  ExtractTemporaryFile('launcher-profiles.json');")
    for source, flat in files:
        lines.append(f'Source: "{source}"; DestName: "{flat}"; Flags: dontcopy')
        extract.append(f"  ExtractTemporaryFile('{flat}');")
    # Объём данных перевода (install.json и файлы): мастер сверяет с ним свободное место в {tmp} и в защищённой папке до извлечения.
    payload_bytes = (staging / 'install.json').stat().st_size + sum(source.stat().st_size for source, _flat in files)
    lines.insert(0, f'#define PayloadBytes "{payload_bytes}"')
    (staging / 'payload-files.iss').write_text('\n'.join(lines) + '\n', encoding='utf-8-sig')
    (staging / 'payload-extract.iss').write_text('\n'.join(extract) + '\n', encoding='utf-8-sig')
    # Копии файлов данных в защищённую папку установщика (ExtractPayload в .iss): имя и SHA-256 источника; помощник читает только копии.
    # install.json и launcher-profiles.json копируются раньше (SecPrepare), их SHA-256 приходит отдельными определениями ISCC.
    copy = [f"  if not SecCopyFile(ExpandConstant('{{tmp}}\\{flat}'), '{flat}', '{sha(source)}') then Exit;" for source, flat in files]
    (staging / 'payload-copy.iss').write_text('\n'.join(copy) + '\n', encoding='utf-8-sig')


PAYLOAD_NAME = re.compile(r'^p[1-9][0-9]{0,9}_[1-9][0-9]{0,9}(_0\.db|\.db|\.info)$')


def assert_clean(install: dict, files: list[tuple[Path, str]], staging: Path, launcher: bool = False) -> None:
    """В установщик входят тексты игры (override-пакеты), помощник и значки. Лаунчер, Node и скрипты не входят, кроме белого списка
    (активатор и шесть файлов в launcher/), и он допустим только при launcher=True: любой другой файл с такими именами отвергается."""
    allowed = LAUNCHER_STAGED if launcher else set()
    present = {f.relative_to(staging).as_posix() for f in staging.rglob('*') if f.is_file()}
    bad = [name for name in present if name not in allowed and (FORBIDDEN_NAME.search(name.rsplit('/', 1)[-1]) or 'launcher' in name.lower())]
    bad += [f.name for f in staging.rglob('*') if f.is_dir() and not (launcher and f.name == 'launcher') and (FORBIDDEN_NAME.search(f.name) or 'launcher' in f.name.lower())]
    if launcher:
        bad += sorted(allowed - present)   # белый список выложен целиком
    bad += [flat for _, flat in files if not PAYLOAD_NAME.fullmatch(flat) or FORBIDDEN_NAME.search(flat)]
    bad += [name for name in install['candidates'] if 'launcher' in name.lower()]
    if bad:
        fail('в установщик попали лишние файлы лаунчера или Node (или нет нужных): ' + ', '.join(sorted(set(bad))))


def make_staging(install: dict, files: list[tuple[Path, str]], staging: Path, with_tool: bool = True, launcher_build: Path | None = None,
                 test_build: bool = False, update_repo: str | None = None) -> None:
    """Раскладывает staging-папку (данные, значки, помощник, списки для Inno, русский лаунчер при launcher_build) и проверяет её состав.
    update_repo (владелец/репозиторий GitHub): включает автообновление - ресурс update-config.json в помощнике и WXQTray.exe в установщике."""
    (staging / 'install.json').write_text(json.dumps(install, ensure_ascii=False, indent=1), encoding='utf-8')
    write_assets(staging)
    if with_tool:
        build_tool(staging, test_build, write_update_config(staging, update_repo) if update_repo else None)
        build_ui(staging, test_build)
        if update_repo:
            build_tray(staging)
    if launcher_build is not None:
        stage_launcher(staging, launcher_build.resolve(), load_launcher_build(launcher_build))
    write_include(staging, files, launcher_build is not None)
    assert_clean(install, files, staging, launcher_build is not None)


def iscc_defines(staging: Path, output: Path, version: str, date: str, game_version: str, packages_text: str, test_build: bool,
                 launcher: bool = False, dump_script: Path | None = None, diag: bool = False) -> list[str]:
    """Определения для ISCC. /DTESTBUILD (разрешает /CURRENTUSER в командной строке) только в тестовой сборке; /DWITHLAUNCHER — с русским лаунчером.
    /DToolSha256, /DInstallSha256, /DUiSha256, /DProfilesSha256 — SHA-256 собранных файлов: повышенный мастер сверяет с ними копии в защищённой папке.
    dump_script (/DDumpScript=<файл>): ISPP сохраняет готовый после препроцессора скрипт (тесты проверяют по нему релизную сборку)."""
    defines = [f'/DStaging={staging}', f'/DOutputDir={output.parent}', f'/DOutputName={output.stem}', f'/DAppVersion={version}',
               f'/DBuildDate={date}', f'/DGameVersion={game_version}', f'/DPackagesText={packages_text}']
    numeric = re.match(r'\d+(?:\.\d+){0,3}', version)   # VersionInfo* требует чисел x.x.x.x: буква в конце версии (0.4.3d) отбрасывается
    if numeric and numeric.group(0) != version:
        defines.append(f'/DAppVersionNumeric={numeric.group(0)}')
    if test_build:
        defines.append('/DTESTBUILD')
    if diag:
        defines.append('/DDIAGBUILD')
    if launcher:
        defines.append('/DWITHLAUNCHER')
    if (staging / 'WXQSetupUI.exe').is_file():
        defines.append('/DWITHUI')
    if (staging / 'WXQTray.exe').is_file():   # автообновление: задача планировщика, значок в трее, запись автозапуска
        defines.append('/DWITHUPDATER')
    for name, define in (('WXQTool.exe', 'ToolSha256'), ('install.json', 'InstallSha256'), ('WXQSetupUI.exe', 'UiSha256'),
                         ('launcher/profiles.json', 'ProfilesSha256')):
        if (staging / name).is_file():
            defines.append(f'/D{define}={sha(staging / name)}')
    if dump_script is not None:
        defines.append(f'/DDumpScript={dump_script}')
    return defines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--candidate', type=Path, action='append', required=True, help='папка кандидата build/carrier-*, build/pilot-* или build/lua-wrap-* (можно несколько, один кандидат на пакет)')
    parser.add_argument('--output', type=Path, help='путь итогового .exe (по умолчанию installer/dist/WXQ-Russian-Setup-<версия>-<дата>.exe)')
    parser.add_argument('--version', default='0.1.0')
    parser.add_argument('--date', default=datetime.date.today().isoformat())
    parser.add_argument('--keep-staging', action='store_true', help='оставить staging-папку (для отладки)')
    parser.add_argument('--test-build', action='store_true', help='тестовая сборка: /CURRENTUSER и /ALLUSERS разрешены в командной строке (только для installer/tests; не раздавать)')
    parser.add_argument('--dump-script', type=Path, help='сохранить скрипт .iss после препроцессора ISPP (для проверки релизной сборки тестами); установщик собирается как обычно')
    parser.add_argument('--launcher-build', type=Path, help='папка build/launcher-ru-*: включить русский лаунчер (активатор и файлы launcher/); без параметра установщик собирается без лаунчера')
    parser.add_argument('--diag', action='store_true', help='метка диагностической сборки (нужен кандидат lua-wrap-* с --diag-log): заголовок окна, подвал и свойства exe; без метки такой кандидат отвергается')
    parser.add_argument('--update-repo', help='владелец/репозиторий GitHub с релизами: включает автообновление (адрес канала и публичные ключи вшиваются в WXQTool.exe, добавляется WXQTray.exe). Без параметра установщик собирается без автообновления')
    parser.add_argument('--game-root', type=Path, help='папка игры (по умолчанию — установка лаунчера): оригиналы prime для проверки кандидатов (длина потока текстур, текстовые ресурсы) и повторной сборки Lua-нагрузок; игра только читается, версия должна совпасть с кандидатами')
    args = parser.parse_args()
    global GAME_ROOT
    GAME_ROOT = args.game_root
    if not ISCC.is_file():
        fail(f'не найден ISCC.exe: {ISCC}')
    output = (args.output or INSTALLER / 'dist' / f'WXQ-Russian-Setup-{args.version}-{args.date}.exe').resolve()
    if output.exists():
        fail(f'{output} уже есть; выберите другое имя (готовые сборки не перезаписываются)')
    output.parent.mkdir(parents=True, exist_ok=True)
    install, files = combine(args.candidate, args.version, args.date)
    # Диагностический Lua-кандидат (--diag-log) входит только в установщик с --diag, и наоборот: иначе журнал уехал бы в обычную раздачу.
    has_diag_log = any(bool(json.loads((d.resolve() / 'manifest.json').read_text('utf-8-sig')).get('lua_wrap', {}).get('diag_log')) for d in args.candidate)
    if has_diag_log != args.diag:
        fail('кандидат lua-wrap-* с --diag-log допустим только вместе с --diag (и --diag требует такого кандидата)')
    staging = Path(tempfile.mkdtemp(prefix='wxq-ru-staging-'))
    try:
        make_staging(install, files, staging, launcher_build=args.launcher_build, test_build=args.test_build, update_repo=args.update_repo)
        packages_text = ', '.join(p['id'] for p in install['packages'])
        command = [str(ISCC), '/Q', *iscc_defines(staging, output, args.version, args.date, install['game_version'], packages_text, args.test_build,
                                                    launcher=args.launcher_build is not None, dump_script=args.dump_script.resolve() if args.dump_script else None, diag=args.diag),
                   str(INSTALLER / 'WXQRussian.iss')]
        completed = subprocess.run(command, capture_output=True)
        text = (completed.stdout + completed.stderr).decode('utf-8', errors='replace')
        if completed.returncode:
            fail('ISCC завершился с ошибкой:\n' + text)
        if text.strip():
            print(text.strip())
    finally:
        if args.keep_staging:
            print('staging:', staging)
        else:
            shutil.rmtree(staging, ignore_errors=True)
    print(json.dumps({'installer': str(output), 'bytes': output.stat().st_size, 'sha256': sha(output), 'game_version': install['game_version'],
                      'packages': [p['id'] for p in install['packages']], 'candidates': install['candidates'], 'test_build': args.test_build, 'diag': args.diag,
                      'launcher': args.launcher_build.resolve().name if args.launcher_build else None}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
