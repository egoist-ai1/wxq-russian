"""Офлайн-сборка пилотного носителя русификации (override-слой QtsVFS) по pilot-content.json.

Читает установленную игру только на чтение, в неё ничего не пишет и ничего не запускает кроме изолированного
SDK-воркера проекта (game_layer_native_worker.py) в собственной временной лаборатории. Результат — каталог
`build/pilot-<дата>/` с манифестом, SHA, отчётом и файлами пакетов; ставит его game_layer_deploy.py.

Пока у какой-либо записи пустой `ru`, сборка отказывает до чтения игры целиком и ничего не создаёт.
"""
from __future__ import annotations

import argparse
import datetime
import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from contextlib import ExitStack

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
for _path in (str(PROJECT), str(PROJECT / 'scripts')):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import atlas_text_patch  # noqa: E402
import pilot_contract  # noqa: E402
import pilot_start_button  # noqa: E402
import wxq_install  # noqa: E402
from game_inspect import QtsContainer  # noqa: E402
from game_layer_native_worker import DLL_HASHES, KNOWN_DLL_HASHES  # noqa: E402
from localization import core, fit_rules  # noqa: E402
from localization.global_map_codec import GlobalMapDocument, prove_text_only  # noqa: E402
from scripts.game_lang_codec import LangDocument, validate_rewrite  # noqa: E402

HEADER_ID = 0xe12bade797f29f55
NAMESPACES = {'global': 'global-localize', 'lang': 'lang', 'texture': 'rgba32-texture', 'atlas': 'bc7-texture'}
ITEM_KEYS = {'id', 'namespace', 'resource', 'key', 'cn_source', 'en_ref', 'ru', 'slot', 'latin_allowlist', 'where', 'note', 'rect', 'style', 'params'}
REQUIRED_ITEM_KEYS = {'id', 'namespace', 'resource', 'key', 'cn_source', 'en_ref', 'ru'}
RESOURCE = re.compile(r'([1-9][0-9]{0,9})/([0-9a-f]{16})')
DEFAULT_CONTENT = PROJECT / 'pilot' / 'pilot-content.json'
CATALOG = PROJECT / 'source/english-patcher-1.4.3/extracted/catalog/catalog.json'
FILE_CONFIG_CHUNKS = '0,327681,307200'
FALLBACKS_PER_PACKAGE = 2


class PilotError(ValueError):
    """Содержимое или окружение не годятся: сообщение объясняет, что исправить."""


class EmptyTranslation(PilotError):
    pass


def sha(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- содержимое

def load_content(path: Path) -> list[dict]:
    try:
        value = json.loads(path.read_text('utf-8-sig'))
    except (OSError, ValueError) as error:
        raise PilotError(f'pilot-content.json не читается: {path}: {error}') from error
    items = value.get('items') if isinstance(value, dict) else value
    if not isinstance(items, list) or not items:
        raise PilotError('pilot-content.json: ожидается непустой список записей (или {"items": [...]})')
    return items


def check_items(items: list[dict]) -> None:
    """Структура записей и обязательные переводы; ничего не читает с диска."""
    problems, empty, seen = [], [], set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            problems.append(f'запись {index}: не объект')
            continue
        name = item.get('id', f'#{index}')
        if name in seen:
            problems.append(f'{name}: повторяющийся id')
        seen.add(name)
        missing = REQUIRED_ITEM_KEYS - set(item)
        extra = set(item) - ITEM_KEYS
        if missing or extra:
            problems.append(f'{name}: нет полей {sorted(missing)}, лишние поля {sorted(extra)}')
            continue
        if item['namespace'] not in NAMESPACES:
            problems.append(f'{name}: namespace {item["namespace"]!r} не из {sorted(NAMESPACES)}')
        if not isinstance(item['resource'], str) or not RESOURCE.fullmatch(item['resource']):
            problems.append(f'{name}: resource должен быть "<пакет>/<16 hex>"')
        if item['namespace'] in ('global', 'lang') and not item['key']:
            problems.append(f'{name}: пустой key')
        if item['namespace'] == 'atlas' and (not isinstance(item.get('rect'), list) or len(item['rect']) != 4 or item.get('style') not in atlas_text_patch.STYLES):
            problems.append(f'{name}: у атласа нужны rect [x0,y0,x1,y1] и style из {list(atlas_text_patch.STYLES)}')
        if not all(isinstance(item[k], str) for k in ('cn_source', 'en_ref', 'ru', 'key')):
            problems.append(f'{name}: cn_source/en_ref/ru/key должны быть строками')
        elif not item['ru'].strip():
            empty.append(name)
    if problems:
        raise PilotError('pilot-content.json:\n  ' + '\n  '.join(problems))
    if empty:
        raise EmptyTranslation('Пустой перевод `ru` у записей: ' + ', '.join(empty)
                               + '. Впишите русский текст в pilot-content.json; сборка ничего не создавала.')


# ---------------------------------------------------------------- игра (только чтение)

def data_files(game: Path, package: str) -> list[Path]:
    folder = game / 'WXQ_Data/Package/builtin' / package
    files = sorted((p for p in folder.glob(f'{package}_*.db')), key=lambda p: int(p.stem.rsplit('_', 1)[1]))
    if not (folder / f'{package}.db').is_file() or not files:
        raise PilotError(f'Prime-пакет {package} не найден в установке')
    return files


_PREFETCHED: dict[tuple[str, str, str], tuple[bytes, Path]] = {}


def prefetch_originals(game: Path, package: str, idents: set[str]) -> None:
    """Читает много ресурсов пакета за один проход по контейнерам (иначе индекс разбирается заново на каждый ресурс)."""
    left = {int(i, 16): i for i in idents if (str(game), package, i) not in _PREFETCHED}
    for path in data_files(game, package):
        if not left:
            break
        with QtsContainer(path) as container:
            for number in [n for n in left if n in container.entries]:
                _PREFETCHED[(str(game), package, left.pop(number))] = (container.decode_entry(number), path)


def read_original(game: Path, package: str, ident: str) -> tuple[bytes, Path]:
    return _PREFETCHED.get((str(game), package, ident)) or _read_original(str(game), package, ident)


@functools.lru_cache(maxsize=16)
def _read_original(game_root: str, package: str, ident: str) -> tuple[bytes, Path]:
    game = Path(game_root)
    for path in data_files(game, package):
        with QtsContainer(path) as container:
            if int(ident, 16) in container.entries:
                return container.decode_entry(int(ident, 16)), path
    raise PilotError(f'Ресурс {package}/{ident} отсутствует в prime-пакете установки')


@functools.lru_cache(maxsize=4)
def title_font(game: Path) -> bytes:
    asset, _ = read_original(game, pilot_start_button.FONT_PACKAGE, pilot_start_button.FONT_ID)
    return pilot_start_button.extract_font(asset)


def en_reference() -> dict:
    try:
        return json.loads(CATALOG.read_text('utf-8-sig'))['unique']
    except (OSError, ValueError, KeyError):
        return {}


def text_issues(item: dict, actual_cn: str, ru_game: str) -> list[str]:
    issues = []
    if actual_cn != item['cn_source']:
        issues.append('cn_source-differs-from-game')
    issues += core.target_issues(actual_cn, ru_game, set(), item.get('latin_allowlist', ()), formatter={})
    issues += fit_rules.fit_report(actual_cn, ru_game, item.get('slot') or {})
    return issues


def _compose_atlas_job(job: tuple):
    """Кодирование одного BC7-атласа (в отдельном процессе): (ok, результат либо текст ошибки)."""
    original, atlas_items, font, size = job
    try:
        return True, atlas_text_patch.compose_atlas(original, atlas_items, font, size=size)
    except (atlas_text_patch.AtlasBlocker, ValueError) as error:
        return False, str(error)


def _precompute_atlases(game: Path, groups: dict) -> dict:
    """Параллельно (по ядрам) кодирует все атласы; детерминизм сохраняется: результат зависит только от входа."""
    import concurrent.futures
    keys, jobs = [], []
    font = title_font(game)
    for (package, ident), group in groups.items():
        if {row['namespace'] for row in group} != {'atlas'}:
            continue
        original, _ = read_original(game, package, ident)
        atlas_items = [{'id': row['id'], 'rect': row['rect'], 'ru': row['ru'], 'style': row['style'], 'params': row.get('params')} for row in group]
        keys.append((package, ident))
        jobs.append((original, atlas_items, font, pilot_contract.BC7_TEXTURE_SIZES.get(package, {}).get(ident)))
    if len(jobs) < 4:
        return {}
    workers = max(1, min(int(os.environ.get('WXQ_BUILD_WORKERS', 12)), (os.cpu_count() or 4) - 2))   # WXQ_BUILD_WORKERS — потолок при нехватке памяти
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
        return dict(zip(keys, pool.map(_compose_atlas_job, jobs, chunksize=4)))


def prepare(game: Path, items: list[dict]) -> tuple[list[dict], dict]:
    """Новые полезные нагрузки ресурсов и отчёт по записям. Любые замечания — PilotError до записи на диск."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for item in items:
        package, ident = item['resource'].split('/')
        groups.setdefault((package, ident), []).append(item)
    problems, resources, report = [], [], {}
    for package in {p for p, _ in groups}:
        prefetch_originals(game, package, {i for p, i in groups if p == package})
    precomputed = _precompute_atlases(game, groups)
    for (package, ident), group in groups.items():
        namespaces = {row['namespace'] for row in group}
        if len(namespaces) != 1:
            problems.append(f'{package}/{ident}: записи разных namespace в одном ресурсе')
            continue
        namespace = namespaces.pop()
        original, data_file = read_original(game, package, ident)
        record = {'package': package, 'id_hex': ident, 'kind': NAMESPACES[namespace], 'original': original,
                  'original_sha256': sha_bytes(original), 'items': [row['id'] for row in group],
                  'data_file': data_file.name, 'alias': f'/Pilot/{ident}.{"png" if namespace in ("texture", "atlas") else "bin"}'}
        if namespace == 'atlas':
            allowed = pilot_contract.BC7_TEXTURE_ALLOWED.get(package, {}).get(ident)
            if allowed is None or allowed != record['original_sha256']:
                problems.append(f'{package}/{ident}: атлас не из белого списка BC7 или оригинал не совпал с закреплённым SHA')
                continue
            atlas_items = [{'id': row['id'], 'rect': row['rect'], 'ru': row['ru'], 'style': row['style'], 'params': row.get('params')} for row in group]
            if (package, ident) in precomputed:
                ok, outcome = precomputed[(package, ident)]
            else:
                ok, outcome = _compose_atlas_job((original, atlas_items, title_font(game), pilot_contract.BC7_TEXTURE_SIZES.get(package, {}).get(ident)))
            if not ok:
                problems.append(f'{package}/{ident}: атлас: {outcome}')
                continue
            payload, info, before, after = outcome
            record['payload'] = payload
            record['preview'] = {'source': atlas_text_patch.png(before), 'candidate': atlas_text_patch.png(after)}
            for row in group:
                done = info['items'][row['id']]
                report[row['id']] = {'kind': 'atlas', 'ru': done['ru'], 'rect': done['rect'], 'em_ratio': done['em_ratio'], 'condense': done['condense'],
                                     'atlas_psnr_inside_rects_db': info['psnr_inside_rects_db'], 'unchanged_blocks_byte_exact': info['unchanged_blocks_byte_exact'],
                                     'changed_blocks': info['blocks'], 'bc7_modes': info['modes']}
            resources.append(record)
            continue
        if namespace == 'texture':
            if len(group) != 1:
                problems.append(f'{package}/{ident}: у текстуры одна запись')
                continue
            row = group[0]
            try:
                payload, info = pilot_start_button.compose_texture(original, row['ru'].strip(), title_font(game))
            except pilot_start_button.ButtonBlocker as error:
                problems.append(f'{row["id"]}: текстура кнопки: {error}')
                continue
            record['payload'] = payload
            record['preview'] = {'source': pilot_start_button.preview_png(original), 'candidate': pilot_start_button.preview_png(payload)}
            report[row['id']] = {'kind': 'texture', 'mask': info['mask'], 'changed_pixels': info['proof']['changed_pixel_count'],
                                 'ru': row['ru'].strip(), 'outside_mask_rgba_byte_exact': info['proof']['outside_mask_rgba_byte_exact'],
                                 'em_ratio': info['metrics']['em_ratio'], 'condense': info['metrics']['condense']}
            resources.append(record)
            continue
        document = GlobalMapDocument.parse(original) if namespace == 'global' else LangDocument.parse(original)
        actual = {r.key: r.text for r in document.rows}
        translations = {}
        for row in group:
            if row['key'] not in actual:
                problems.append(f'{row["id"]}: ключа {row["key"]} нет в ресурсе {package}/{ident}')
                continue
            ru_game, substitutions = fit_rules.emit_for_game(row['ru'].strip())
            issues = text_issues(row, actual[row['key']], ru_game)
            if issues:
                problems.append(f'{row["id"]}: ' + '; '.join(issues))
                continue
            translations[row['key']] = ru_game
            report[row['id']] = {'kind': namespace, 'key': row['key'], 'ru': ru_game, 'cn': actual[row['key']],
                                 'visible_length': fit_rules.visible_length(ru_game), 'ascii_substitution_ranges': substitutions}
        if len(translations) != len(group):
            continue
        try:
            payload = document.replace_text(translations) if namespace == 'global' else document.replace(translations)
            if namespace == 'global':
                prove_text_only(original, payload, translations)
            else:
                validate_rewrite(document, LangDocument.parse(payload), translations)
        except ValueError as error:
            problems.append(f'{package}/{ident}: кодек отверг замену: {error}')
            continue
        record['payload'] = payload
        resources.append(record)
    if problems:
        raise PilotError('Проверка текстов не пройдена:\n  ' + '\n  '.join(problems))
    for record in resources:
        record['sha256'] = sha_bytes(record['payload'])
    return resources, report


# ---------------------------------------------------------------- лаборатория и сборка

def cleanup_lab_task(lab: Path, thread_id: str) -> None:
    """Удаляет только лабораторию данного задания, сохраняя соседние сборки."""
    temporary = Path(tempfile.gettempdir()).resolve()
    tasks = temporary / 'wxq-pilot/tasks'
    task = tasks / thread_id
    resolved_tasks = tasks.resolve()
    resolved_task = task.resolve()
    resolved_lab = lab.resolve()
    if (not re.fullmatch(r'[0-9a-f]{32}', thread_id)
            or resolved_lab.parent.name != 'work' or resolved_lab.parent.parent != resolved_task
            or not resolved_tasks.is_relative_to(temporary)
            or resolved_task.parent != resolved_tasks or resolved_task.name != thread_id):
        raise PilotError('Отказ от очистки: лаборатория не принадлежит текущему временному заданию')
    shutil.rmtree(resolved_task, ignore_errors=True)


def installed_state(game: Path, packages: list[str]) -> dict:
    cache = game / 'QtsDataFolder/QtsVFSCache/packages'
    log = game / 'QtsDataFolder/QtsVFSCache/QtsVFS.log'
    hashes = pilot_contract.read_prime_pkg_hashes(log)
    infos, headers, versions = {}, {}, set()
    for package in packages:
        if package not in hashes:
            raise PilotError(f'В QtsVFS.log нет pkgHash prime-пакета {package}: запустите игру один раз, чтобы лог появился')
        info_path = cache / package / f'{package}.info'
        info = info_path.read_bytes()
        data = pilot_contract.installed_info(cache, package)
        versions.add(data['version'])
        infos[package] = {'source': str(info_path), 'sha256': sha_bytes(info), 'data': json.loads(info.decode('utf-8-sig'))}
        with QtsContainer(cache / package / f'{package}.db', raw_values=True) as container:
            # Кандидат строится целиком из prime-пакета и content, а не поверх установленного override: записи прежнего
            # пилота (например, «ИГРАТЬ») допустимы и должны быть заново указаны в content; заголовок обязан быть на месте.
            if HEADER_ID not in container.entries or container.invalid_record_count:
                raise PilotError(f'Override пакета {package} в установке повреждён или без заголовка')
            extra = len(container.entries) - 1
            if extra:
                print(f'Замечание: установленный override пакета {package} уже содержит {extra} записей; кандидат заменяет их целиком', file=sys.stderr)
            headers[package] = container.raw_record_values(HEADER_ID)[0].hex()
    if len(versions) != 1:
        raise PilotError('Пакеты кандидата имеют разные версии сборки')
    base_info = pilot_contract.installed_info(cache, '0')
    return {'hashes': {p: hashes[p] for p in packages}, 'infos': infos, 'headers': headers, 'version': versions.pop(),
            'revision': base_info.get('revision', '0')}


def pack_version(version: str) -> int:
    parts = [int(x) for x in version.split('.')]
    if len(parts) != 4 or any(not 0 <= x < 256 for x in parts):
        raise PilotError(f'Версия сборки {version!r} не разбирается')
    return parts[0] << 24 | parts[1] << 16 | parts[2] << 8 | parts[3]


def pick_untouched(container: QtsContainer, exclude: set[int], count: int, prefer: int | None = None) -> list[int]:
    picked = [prefer] if prefer in container.entries and prefer not in exclude else []
    for ident in sorted(container.entries):
        if len(picked) >= count:
            break
        total = sum(c.uncompressed_size for c in container.entries[ident])
        if ident not in exclude and ident not in picked and 64 <= total <= 262144:
            picked.append(ident)
    if len(picked) < count:
        raise PilotError('Не нашлось нетронутых ресурсов для проверки чтения')
    return picked


def run_worker(lab: Path, thread_id: str, phase: str) -> None:
    env = dict(os.environ, CODEX_THREAD_ID=thread_id)
    completed = subprocess.run([sys.executable, '-B', str(PROJECT / 'scripts/game_layer_native_worker.py'), '--lab', str(lab), '--phase', phase],
                               cwd=lab, env=env, capture_output=True, timeout=1800, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    (lab / f'{phase}-command.txt').write_bytes(completed.stdout + completed.stderr)
    if completed.returncode:
        raise RuntimeError(f'Native {phase} failed, exit {completed.returncode}; diagnostics in {lab}')


def build(game: Path, resources: list[dict], report: dict, items: list[dict], output: Path, content_path: Path, keep_lab: bool) -> dict:
    packages = sorted({r['package'] for r in resources}, key=int)
    state = installed_state(game, packages)
    all_packages = ['0'] + packages
    prime_root = game / 'WXQ_Data/Package/builtin'
    prime_files = [f'{p}/{f.name}' for p in all_packages for f in sorted((prime_root / p).glob('*.db'))]
    total = sum((prime_root / rel).stat().st_size for rel in prime_files)
    thread_id = uuid.uuid4().hex
    lab = Path(tempfile.gettempdir()) / 'wxq-pilot' / 'tasks' / thread_id / 'work' / ('pilot-' + uuid.uuid4().hex[:8])
    lab.mkdir(parents=True)
    if shutil.disk_usage(lab).free < total * 1.3 + (64 << 20):
        cleanup_lab_task(lab, thread_id)
        raise PilotError(f'Не хватает места на диске временной папки: нужно около {int(total * 1.3) >> 20} МБ')
    try:
        return _build_in_lab(game, resources, report, items, output, content_path, state, all_packages, packages, prime_files, thread_id, lab)
    finally:
        if not keep_lab:
            cleanup_lab_task(lab, thread_id)


def _build_in_lab(game, resources, report, items, output, content_path, state, all_packages, packages, prime_files, thread_id, lab) -> dict:
    copies = []
    for relative in prime_files:
        source = game / 'WXQ_Data/Package/builtin' / relative
        destination = lab / 'prime' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        before = sha(source)
        shutil.copyfile(source, destination)
        if sha(destination) != before:
            raise PilotError(f'Копия prime {relative} отличается от оригинала')
        copies.append({'relative': relative, 'source': str(source), 'sha256': before, 'bytes': source.stat().st_size})
    sdk_actual = {}
    for name, expected in KNOWN_DLL_HASHES.items():
        source = game / 'WXQ_Data/Plugins/x86_64' / name
        sdk_actual[name] = sha(source)
        if sdk_actual[name] not in expected:
            raise PilotError(f'Native SDK установки отличается от закреплённого: {name}')
        shutil.copyfile(source, lab / name)
    changed = {(r['package'], int(r['id_hex'], 16)) for r in resources}
    samples, fallback = [], {}
    (lab / 'expected').mkdir()
    for package in all_packages:
        (lab / 'raw' / package).mkdir(parents=True)
        lines = []
        # Один индекс на storage: повторный разбор того же архива для каждого
        # ресурса делал большие сборки квадратичными по числу записей.
        with ExitStack() as readers:
            containers = {}
            for resource in (r for r in resources if r['package'] == package):
                ident = int(resource['id_hex'], 16)
                data_file = resource['data_file']
                if data_file not in containers:
                    containers[data_file] = readers.enter_context(QtsContainer(lab / 'prime' / package / data_file))
                container = containers[data_file]
                if sha_bytes(container.decode_entry(ident)) != resource['original_sha256']:
                    raise PilotError('Источник ресурса в копии prime не совпал с оригиналом')
                original_file = 'expected/original-' + resource['id_hex']
                (lab / original_file).write_bytes(resource['original'])
                modified_file = 'raw/' + package + resource['alias']
                (lab / modified_file).parent.mkdir(parents=True, exist_ok=True)
                (lab / modified_file).write_bytes(resource['payload'])
                lines.append(f'{ident}|0|0|{resource["alias"]}|{(lab / modified_file).as_posix()}|{FILE_CONFIG_CHUNKS};')
                samples.append({'name': 'Pilot_' + resource['id_hex'], 'id_hex': resource['id_hex'], 'original_file': original_file, 'modified_file': modified_file})
        (lab / f'file-config-{package}.txt').write_text('\n'.join(lines) + '\n', 'utf-8')
        # Нетронутые ресурсы того же пакета: чтение через override должно остаться побайтно прежним.
        own_files = sorted({r['data_file'] for r in resources if r['package'] == package}) or [f'{package}_0.db']
        with QtsContainer(lab / 'prime' / package / own_files[0]) as container:
            excluded = {ident for pkg, ident in changed if pkg == package}
            prefer = 0xaf1fb540a2b8cb52 if package == '0' else None
            for ident in pick_untouched(container, excluded, 1 if package == '0' else FALLBACKS_PER_PACKAGE, prefer):
                data = container.decode_entry(ident)
                name = f'{ident:016x}'
                (lab / 'expected' / f'untouched-{name}').write_bytes(data)
                samples.append({'name': 'Untouched_' + name, 'id_hex': name, 'original_file': f'expected/untouched-{name}', 'modified_file': f'expected/untouched-{name}'})
                fallback[name] = sha_bytes(data)
    config_resources = [{k: r[k] for k in ('package', 'id_hex', 'alias', 'sha256', 'original_sha256')} for r in resources]
    config = {'lab': str(lab), 'task_thread_id': thread_id, 'packages': all_packages, 'resources': config_resources, 'samples': samples}
    (lab / 'worker-config.json').write_text(json.dumps(config, indent=2) + '\n', 'utf-8')
    pack_config = {'PackLog': True, 'Version': pack_version(state['version']), 'BuildID': int(state['revision']), 'MaxStorageSize': 1073741824,
                   'AllInOne': False, 'InternalPackage': '0', 'ExtraExportDataPackage': '', 'WriteVersion': True, 'WriteBuildID': True}
    (lab / 'pack-config.json').write_text(json.dumps(pack_config, indent=2) + '\n', 'utf-8')
    for phase in ('pack', 'create', 'insert', 'verify'):
        run_worker(lab, thread_id, phase)
    for package in packages:
        wanted = {int(r['id_hex'], 16) for r in resources if r['package'] == package}
        with QtsContainer(lab / 'override' / package / (package + '.db'), raw_values=True) as container:
            if set(container.entries) != wanted | {HEADER_ID} or container.invalid_record_count:
                raise PilotError('Override содержит неожиданные записи метаданных/удаления')
            if container.raw_record_values(HEADER_ID)[0].hex() != state['headers'][package]:
                raise PilotError(f'Заголовок override пакета {package} не совпал с установленным')
        with QtsContainer(lab / 'override' / package / (package + '_0.db')) as container:
            if set(container.entries) != wanted or container.invalid_record_count:
                raise PilotError('В данных override лишние или отсутствующие ресурсы')
            for resource in (r for r in resources if r['package'] == package):
                if sha_bytes(container.decode_entry(int(resource['id_hex'], 16))) != resource['sha256']:
                    raise PilotError('Независимое чтение LZ4 не совпало с новой нагрузкой')
    for row in copies:
        if sha(Path(row['source'])) != row['sha256'] or sha(lab / 'prime' / row['relative']) != row['sha256']:
            raise PilotError('Prime-файлы изменились во время сборки')
    for package in packages:
        shutil.copyfile(state['infos'][package]['source'], lab / 'override' / package / (package + '.info'))
    verification = json.loads((lab / 'verify-result.json').read_text('utf-8'))
    checks = {row['function']: row['result'] for row in verification['calls']}
    if not all(checks.get('VFS_CheckPackage_' + p) is True for p in all_packages) or len(verification['reads']) != len(samples) or not all(r['matches'] for r in verification['reads']):
        raise PilotError('Неполная нативная проверка пакетов/чтений')
    index_file = lab / 'override/builtin/Globalindex/GlobalIndexPrime.data'
    installed_index = game / 'QtsDataFolder/QtsVFSCache/packages/builtin/Globalindex/GlobalIndexPrime.data'
    index_sha = sha(index_file)
    if index_sha != sha(installed_index):
        raise PilotError('Нативный индекс prime отличается от индекса установки')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'payloads').mkdir()
    files = []
    for package in packages:
        target = output / 'packages' / package
        target.mkdir(parents=True)
        for name in (package + '.db', package + '_0.db', package + '.info'):
            shutil.copyfile(lab / 'override' / package / name, target / name)
            files.append({'relative': f'packages/{package}/{name}', 'bytes': (target / name).stat().st_size, 'sha256': sha(target / name)})
    manifest_resources = []
    for resource in resources:
        payload_path = output / 'payloads' / f'{resource["package"]}-{resource["id_hex"]}.bin'
        payload_path.write_bytes(resource['payload'])
        manifest_resources.append({'package': resource['package'], 'id_hex': resource['id_hex'], 'kind': resource['kind'], 'source': str(payload_path),
                                   'sha256': resource['sha256'], 'original_sha256': resource['original_sha256'], 'alias': resource['alias'],
                                   'items': resource['items'], 'data_file': resource['data_file']})
        if 'preview' in resource:
            (output / 'preview').mkdir(exist_ok=True)
            (output / 'preview' / f'texture-{resource["id_hex"]}-source.png').write_bytes(resource['preview']['source'])
            (output / 'preview' / f'texture-{resource["id_hex"]}-candidate.png').write_bytes(resource['preview']['candidate'])
    manifest = {
        'schema_version': pilot_contract.SCHEMA_VERSION, 'status': pilot_contract.STATUS, 'resource_contract': pilot_contract.CONTRACT,
        'game_version': state['version'], 'packages': packages, 'prime_pkg_hashes': state['hashes'], 'task_thread_id': thread_id,
        'built_at': datetime.datetime.now().astimezone().isoformat(timespec='seconds'), 'content_sha256': sha(content_path),
        'resources': manifest_resources, 'files': files, 'source_copies': copies, 'override_package_headers_hex': state['headers'],
        'official_cache_infos': state['infos'], 'prime_global_index_sha256': index_sha, 'native_sdk_hashes': sdk_actual,
        'native_verification': verification, 'native_fallback_reads': fallback,
        'actual_override_root': str(game / 'QtsDataFolder/QtsVFSCache/packages'),
        'headers_equal_observed_runtime_cache': True, 'complete_prime_global_index_unchanged': True, 'deletion_records': 0, 'vendor_diff_called': False,
        'items': report,
        'limitations': ['Игра и лаунчер не запускались: отображение, ACE/SGuard и чтение Texture2D из override не проверены',
                        'Размер/шрифт игры не менялись; вёрстка оценена по fit_rules, а не на экране',
                        'Лаунчер/ремонт игры могут стереть override; сохранность после обновления не проверена',
                        'Язык клиента остаётся zh_cn'],
    }
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', 'utf-8')
    for phase in ('pack', 'create', 'insert', 'verify'):
        for suffix in ('result.json', 'native-log.json'):
            shutil.copyfile(lab / f'{phase}-{suffix}', output / f'{phase}-{suffix}')
    shutil.copyfile(content_path, output / 'pilot-content.used.json')
    (output / 'REPORT.md').write_text(render_report(manifest, items), 'utf-8')
    return manifest


def render_report(manifest: dict, items: list[dict]) -> str:
    lines = ['# Отчёт пилотной сборки', '', f'Версия игры: {manifest["game_version"]}; пакеты: {", ".join(manifest["packages"])}; собрано: {manifest["built_at"]}', '',
             'Статус: кандидат собран и проверен нативным SDK офлайн, в игру НЕ установлен.', '', '## Записи', '',
             '| id | ресурс | ключ | русский текст | замечания |', '|---|---|---|---|---|']
    for item in items:
        info = manifest['items'].get(item['id'], {})
        lines.append(f'| {item["id"]} | {item["resource"]} | {item["key"] or "-"} | {info.get("ru", item["ru"])} | нет |')
    lines += ['', '## Файлы', '', '| файл | байт | sha256 |', '|---|---|---|']
    lines += [f'| {f["relative"]} | {f["bytes"]} | {f["sha256"]} |' for f in manifest['files']]
    lines += ['', '## Ресурсы', '', '| пакет/id | тип | sha256 нового | sha256 оригинала |', '|---|---|---|---|']
    lines += [f'| {r["package"]}/{r["id_hex"]} | {r["kind"]} | {r["sha256"]} | {r["original_sha256"]} |' for r in manifest['resources']]
    reads = manifest['native_verification']['reads']
    lines += ['', '## Проверки', '', f'- нативных чтений совпало: {sum(1 for r in reads if r["matches"])}/{len(reads)}',
              '- VFS_CheckPackage: ' + ', '.join(sorted(c['function'].rsplit('_', 1)[1] for c in manifest['native_verification']['calls'] if c['function'].startswith('VFS_CheckPackage_') and c['result'] is True)),
              f'- GlobalIndexPrime.data не менялся: {manifest["prime_global_index_sha256"]}',
              '- заголовки override равны установленным, записей удаления нет, vendor diff не вызывался', '', '## Не проверено', '']
    lines += [f'- {text}' for text in manifest['limitations']]
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------- шаблон и CANDIDATES.md

TEMPLATE = {'items': [
    {'id': 'global-title-setting', 'namespace': 'global', 'resource': '1/dd5b5edfbb69feed', 'key': 'TitleSetting', 'cn_source': '设置', 'en_ref': 'Settings',
     'ru': '', 'where': 'Заголовок окна настроек, предположительно по имени ключа TitleSetting; в клиенте не подтверждено.', 'note': 'Короткое слово без чисел и разметки.'},
    {'id': 'lang-fusion-count', 'namespace': 'lang', 'resource': '1/7c3d367e8bc8428c', 'key': '#AB215638C6884CD4#', 'cn_source': '进行<color=#FFE897>3次</color>合成',
     'en_ref': 'Perform <color=#FFE897>3</color> fusions', 'ru': '', 'slot': {'max_chars': 40}, 'where': 'Текст задания (ресурс с заданиями); предположительно, в клиенте не подтверждено.',
     'note': 'Цвет вокруг числа и единицы; число 3 обязано остаться.'},
    {'id': 'lang-gacha-coins', 'namespace': 'lang', 'resource': '1/58d24d0f3e982efd', 'key': '#7D6E48DF70298876#',
     'cn_source': '这一关先体验一下<color=#98CBFF>40枚扭蛋币</color>加持过的超级扭蛋机吧。',
     'en_ref': "In this stage, Experience the Super Gacha Machine powered by <color=#98CBFF>40 Gacha Coin</color>.", 'ru': '', 'slot': {'max_chars': 110},
     'where': 'Реплика обучения (ресурс с обучающими репликами); предположительно, в клиенте не подтверждено.', 'note': 'Цвет вокруг «число + единица» внутри предложения.'},
    {'id': 'lang-talent-slots', 'namespace': 'lang', 'resource': '1/374d23cb263125f9', 'key': '#58D21CA46BB219D8#',
     'cn_source': '<color=#FFFFFF>提升棋手等级至3级和5级时，可获得+1上阵人数。</color>',
     'en_ref': '<color=#FFFFFF>Leveling up the chess player to Lv. 3 and Lv. 5 grants +1 deployment slot.</color>', 'ru': '', 'slot': {'max_chars': 110},
     'where': 'Подсказка о развитии (ресурс с подсказками); предположительно, в клиенте не подтверждено.', 'note': 'Весь текст в цвете; числа 3, 5 и +1.'},
    {'id': 'texture-start-button', 'namespace': 'texture', 'resource': '19/863114a42f12ef76', 'key': 'label', 'cn_source': '开始游戏', 'en_ref': 'Start Game', 'ru': '',
     'where': 'Надпись кнопки StartGame (RGBA32 372x102, пакет 19); на каком экране — не подтверждено.', 'note': 'Одно слово короче поместится крупнее: например ИГРАТЬ; НАЧАТЬ ИГРУ будет мельче примерно вдвое.'},
]}


def markup_summary(text: str) -> str:
    tags = re.findall(r'</?[A-Za-z][^<>]*>|\{\d+\}', text)
    return ' '.join(f'`{t}`' for t in tags) if tags else '-'


def write_candidates(game: Path, items: list[dict], output: Path) -> Path:
    """CANDIDATES.md: оригинал из игры и EN-справка из каталога; только чтение."""
    english = en_reference()
    rows, cache = [], {}
    for item in items:
        package, ident = item['resource'].split('/')
        namespace = item['namespace']
        if namespace == 'texture':
            original, _ = cache.setdefault((package, ident), read_original(game, package, ident))
            cn, en = item['cn_source'], item['en_ref']
            size = f'{len(original)} байт, RGBA32 {"x".join(map(str, pilot_start_button.texture_size(original)))}, один mip'
            rows.append((item, cn, en, size, '-'))
            continue
        original, _ = cache.setdefault((package, ident), read_original(game, package, ident))
        document = GlobalMapDocument.parse(original) if namespace == 'global' else LangDocument.parse(original)
        text = {r.key: r.text for r in document.rows}.get(item['key'])
        if text is None:
            raise PilotError(f'{item["id"]}: ключ {item["key"]} не найден в {item["resource"]}')
        en = english.get(text, item['en_ref'])
        rows.append((item, text[:200], en, f'{len(text)} симв. (видимых {fit_rules.visible_length(text)})', markup_summary(text)))
    lines = ['# Кандидаты пилота', '',
             'Оригиналы прочитаны из установленной игры (только чтение); EN — из каталога `english-patcher-1.4.3/catalog.json`.',
             'Колонка «где» — предположение по соседним строкам ресурса; в клиенте не подтверждено.', '']
    for item, cn, en, length, markup in rows:
        lines += [f'## {item["id"]}', '', f'- ресурс: `{item["resource"]}`', f'- ключ: `{item["key"] or "-"}`', f'- CN (до 200 символов): {cn}', f'- EN: {en}',
                  f'- разметка и аргументы: {markup}', f'- размер: {length}', f'- где отображается: {item.get("where", "не известно")}',
                  f'- заметка: {item.get("note", "-")}', '']
    output.mkdir(parents=True, exist_ok=True)
    path = output / 'CANDIDATES.md'
    path.write_text('\n'.join(lines), 'utf-8')
    return path


# ---------------------------------------------------------------- CLI

def default_output() -> Path:
    return PROJECT / 'build' / f'pilot-{datetime.date.today().isoformat()}'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-root', type=Path, default=None, help='По умолчанию установка, которую сейчас использует лаунчер')
    parser.add_argument('--content', type=Path, default=DEFAULT_CONTENT)
    parser.add_argument('--output', type=Path, default=None, help='Новый каталог build/pilot-<дата>')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--template', action='store_true', help='Создать шаблон pilot-content.json (если его нет) с пустыми ru и выйти')
    mode.add_argument('--candidates', action='store_true', help='Записать CANDIDATES.md по записям content и выйти')
    mode.add_argument('--check', action='store_true', help='Проверить тексты и отрисовать кнопку, ничего не записывая')
    parser.add_argument('--keep-lab', action='store_true', help='Не удалять временную лабораторию (отладка)')
    args = parser.parse_args(argv)
    if args.template:
        if args.content.exists():
            print(f'Уже есть: {args.content}')
            return 0
        args.content.parent.mkdir(parents=True, exist_ok=True)
        args.content.write_text(json.dumps(TEMPLATE, indent=2, ensure_ascii=False) + '\n', 'utf-8')
        print(f'Шаблон записан: {args.content}')
        return 0
    output = (args.output or default_output()).resolve()
    allowed = (PROJECT / 'build').resolve()
    if output.parent != allowed or not output.name.startswith('pilot-'):
        print('Выход должен быть build/pilot-<имя> внутри проекта', file=sys.stderr)
        return 2
    try:
        items = load_content(args.content)
        if not args.candidates:
            check_items(items)
        game = wxq_install.require_install_structure(args.game_root if args.game_root else wxq_install.detect_active_install(), full=True)
        if output.is_relative_to(game):
            raise PilotError('Выход не может лежать внутри игры')
        if args.candidates:
            for item in items:
                if not isinstance(item, dict) or REQUIRED_ITEM_KEYS - {'ru'} - set(item):
                    raise PilotError('pilot-content.json: у записей нет обязательных полей')
            print(write_candidates(game, items, output))
            return 0
        if (output / 'manifest.json').exists() or (output / 'packages').exists():
            raise PilotError(f'Каталог уже содержит сборку: {output}')
        resources, report = prepare(game, items)
        if args.check:
            print(json.dumps({'ok': True, 'resources': [{'package': r['package'], 'id_hex': r['id_hex'], 'kind': r['kind'], 'sha256': r['sha256']} for r in resources], 'items': report},
                             ensure_ascii=False, indent=2))
            return 0
        manifest = build(game, resources, report, items, output, args.content.resolve(), args.keep_lab)
    except EmptyTranslation as error:
        print(str(error), file=sys.stderr)
        return 3
    except PilotError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps({'output': str(output), 'resources': len(manifest['resources']), 'packages': manifest['packages'],
                      'native_reads_match': all(r['matches'] for r in manifest['native_verification']['reads']), 'installed': False}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
