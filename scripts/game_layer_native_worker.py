"""Isolated native SDK worker for game_layer_build.py; never activates a patch."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import struct
import sys
from pathlib import Path

sys.dont_write_bytecode = True
from game_inspect import QtsContainer

DLL_HASHES = {
    'QtsVFS.dll': '4abb4a508e9e78804a0dda35f5fe68ef895d7cb88ae82da5d87ccccca3b0a1a9',
    'QtskDB.dll': 'a1db33d22176160e0d81351edfe02978825f7d2b3c363e537f8dc9af87ad2a29',
}
# SDK игры 1.6.0.1 (WXQ_Data/Plugins/x86_64, сняты с установки 17.10.2026). Сборщики кандидатов принимают обе версии и пишут в манифест
# фактические SHA; DLL_HASHES остаётся хешами 1.5.0.1 для прежних кандидатов и game_layer_deploy.
DLL_HASHES_1601 = {
    'QtsVFS.dll': 'ef6d33801ea3f65591becb3e3bb7add84c6ef39a84672f49ef8aa3f876ad1945',
    'QtskDB.dll': '0fbf8e9d0076dffecf9731ad4cc9bd1f3598fdf3781f36f51c208c0231f5fbaf',
}
KNOWN_DLL_HASHES = {name: frozenset({DLL_HASHES[name], DLL_HASHES_1601[name]}) for name in DLL_HASHES}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lab', type=Path, required=True)
    parser.add_argument('--phase', choices=['pack', 'create', 'insert', 'verify'], required=True)
    args = parser.parse_args()
    lab = args.lab.resolve(strict=True)
    config = json.loads((lab / 'worker-config.json').read_text('utf-8'))
    task_id = os.environ.get('CODEX_THREAD_ID')
    marker = f'/tasks/{task_id}/work/' if task_id else ''
    if task_id != config['task_thread_id'] or not marker or marker.lower() not in lab.as_posix().lower() or lab != Path(config['lab']).resolve() or 'Program Files' in str(lab):
        raise ValueError('Native worker must operate in its own prepared lab')
    for name, expected in KNOWN_DLL_HASHES.items():
        if hashlib.sha256((lab / name).read_bytes()).hexdigest() not in expected:
            raise ValueError(f'Unexpected native SDK: {name}')
    os.chdir(lab)
    ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    database_api = ctypes.CDLL(str(lab / 'QtskDB.dll'))
    api = ctypes.CDLL(str(lab / 'QtsVFS.dll'))
    logs: list[dict] = []
    result = {'phase': args.phase, 'process_id': os.getpid(), 'calls': [], 'reads': [], 'writes': []}

    def save():
        (lab / f'{args.phase}-result.json').write_text(json.dumps(result, indent=2) + '\n', 'utf-8')
        (lab / f'{args.phase}-native-log.json').write_text(json.dumps(logs, indent=2) + '\n', 'utf-8')

    @ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p)
    def logger(level, message):
        logs.append({'level': level, 'message': message.decode('utf-8', 'replace') if message else ''})
        save()

    @ctypes.CFUNCTYPE(None, ctypes.c_char_p)
    def printer(message):
        logger(0, message)

    def bind(library, name, return_type, argument_types):
        function = getattr(library, name)
        function.restype, function.argtypes = return_type, argument_types
        return function

    def path(name):
        value = (lab / name).resolve()
        if not value.is_relative_to(lab):
            raise ValueError('Native SDK path escapes prepared lab')
        return value.as_posix().encode('utf-8')

    def record(name, value):
        result['calls'].append({'function': name, 'result': value})
        save()
        return value

    if args.phase == 'insert':
        error = ctypes.c_uint32()
        error_pointer = ctypes.POINTER(ctypes.c_uint32)
        base_pointer = ctypes.POINTER(ctypes.c_uint64)
        table_pointer = ctypes.POINTER(ctypes.c_void_p)
        open_base = bind(database_api, 'DB_OpenBase', ctypes.c_uint64, [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_bool, error_pointer])
        open_table = bind(database_api, 'DB_OpenTable', ctypes.c_void_p, [base_pointer, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, error_pointer])
        create_table = bind(database_api, 'DB_CreateTable', ctypes.c_void_p, [base_pointer, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, error_pointer])
        put = bind(database_api, 'DB_PutItem', ctypes.c_bool, [table_pointer, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, error_pointer])
        close_table = bind(database_api, 'DB_CloseTable', ctypes.c_bool, [table_pointer, error_pointer])
        close_base = bind(database_api, 'DB_CloseBase', None, [base_pointer])
        for package in config['packages']:
            if package == '0' or not any(resource['package'] == package for resource in config['resources']):
                continue
            records: dict[str, list[tuple[bytes, bytes]]] = {package: [], package + '_0': []}
            with QtsContainer(lab / 'packed' / package / (package + '.db'), raw_values=True) as metadata:
                for resource in config['resources']:
                    if resource['package'] != package:
                        continue
                    ident = int(resource['id_hex'], 16)
                    values = metadata.raw_record_values(ident)
                    if len(values) != 1 or len(values[0]) != 56:
                        raise ValueError('Unexpected SDK resource metadata')
                    records[package].append((struct.pack('<Q', ident), values[0]))
            with QtsContainer(lab / 'packed' / package / (package + '_0.db')) as container:
                for resource in config['resources']:
                    if resource['package'] != package:
                        continue
                    ident = int(resource['id_hex'], 16)
                    for chunk in container.entries[ident]:
                        value = struct.pack('<I', chunk.uncompressed_size) + bytes(container.data[chunk.payload_offset:chunk.payload_offset + chunk.compressed_size])
                        records[package + '_0'].append((struct.pack('<Qii', ident, chunk.main_block, chunk.sub_block), value))
            base = ctypes.c_uint64(open_base(path('override/' + package), None, False, ctypes.byref(error)))
            assert base.value and not error.value
            try:
                for name, rows in records.items():
                    filename = name.encode('ascii')  # SDK appends .db itself.
                    if (lab / 'override' / package / (name + '.db')).exists():
                        cfg = (ctypes.c_uint32 * 2)(1, 0)  # read/write, default cache.
                        table = ctypes.c_void_p(open_table(ctypes.byref(base), filename, cfg, None, ctypes.byref(error)))
                    else:
                        cfg = (ctypes.c_uint32 * 3)(32, 0, 0)  # predict, cache, forceCreate=false.
                        table = ctypes.c_void_p(create_table(ctypes.byref(base), filename, cfg, None, ctypes.byref(error)))
                    assert table.value and not error.value
                    try:
                        for key, value in rows:
                            key_buffer, value_buffer = ctypes.create_string_buffer(key), ctypes.create_string_buffer(value)
                            success = put(ctypes.byref(table), key_buffer, len(key), value_buffer, len(value), ctypes.byref(error))
                            result['writes'].append({'package': package, 'table': name, 'key_hex': key.hex(), 'bytes': len(value), 'success': success, 'error': error.value})
                            save()
                            assert success and not error.value
                    finally:
                        assert close_table(ctypes.byref(table), ctypes.byref(error)) and not error.value
            finally:
                close_base(ctypes.byref(base))
        save()
        print(json.dumps({'phase': args.phase, 'written_records': len(result['writes'])}))
        return

    initialize = bind(api, 'VFS_Initialize', ctypes.c_bool, [ctypes.c_void_p] * 3)
    assert record('VFS_Initialize', initialize(logger, printer, None))
    if args.phase == 'pack':
        create = bind(api, 'VFS_CreatePacker', ctypes.c_int, [ctypes.c_char_p, ctypes.c_char_p])
        cache_create = bind(api, 'VFS_CreatePackerCacheQuery', ctypes.c_int, [ctypes.c_char_p])
        cache_bind = bind(api, 'VFS_PackerBindCacheQuery', ctypes.c_bool, [ctypes.c_int, ctypes.c_int])
        compress = bind(api, 'VFS_PackerSetGlobalIndexCompressMethod', ctypes.c_bool, [ctypes.c_int, ctypes.c_uint32])
        add = bind(api, 'VFS_PackerAddPackage', ctypes.c_bool, [ctypes.c_int, ctypes.c_char_p, ctypes.c_bool, ctypes.c_char_p, ctypes.c_char_p])
        packer_save = bind(api, 'VFS_PackerSave', ctypes.c_bool, [ctypes.c_int])
        destroy = bind(api, 'VFS_DestroyPacker', None, [ctypes.c_int])
        cache_destroy = bind(api, 'VFS_DestroyPackerCacheQuery', None, [ctypes.c_int])
        handle = record('VFS_CreatePacker', create(path('pack-config.json'), path('packed')))
        assert handle >= 0
        cache = record('VFS_CreatePackerCacheQuery', cache_create(path('packer-cache')))
        assert cache >= 0
        try:
            assert record('VFS_PackerBindCacheQuery', cache_bind(handle, cache))
            assert record('VFS_PackerSetGlobalIndexCompressMethod', compress(handle, 1))
            for package in config['packages']:
                assert record('VFS_PackerAddPackage_' + package, add(handle, package.encode(), True, path('raw/' + package), path('file-config-' + package + '.txt')))
            assert record('VFS_PackerSave', packer_save(handle))
        finally:
            destroy(handle)
            cache_destroy(cache)
        print(json.dumps({'phase': args.phase, 'packed_resources': len(config['resources'])}))
        return

    create_system = bind(api, 'VFS_CreateSystem', ctypes.c_bool, [ctypes.c_char_p] * 4)
    create_desc = bind(api, 'VFS_CreatePackageDescription', ctypes.c_int, [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_bool])
    has_prime = bind(api, 'VFS_PackageDescriptionSetHasPrimePackge', ctypes.c_bool, [ctypes.c_int, ctypes.c_bool])
    create_override = bind(api, 'VFS_CreateOverridePackageFromPrimePackage', ctypes.c_bool, [ctypes.c_int])
    mount = bind(api, 'VFS_MountPackage', ctypes.c_bool, [ctypes.c_int, ctypes.c_int, ctypes.c_int])
    open_hash = bind(api, 'VFS_OpenFileByHash', ctypes.c_void_p, [ctypes.c_char_p, ctypes.c_uint64, ctypes.c_char_p])
    read = bind(api, 'VFS_Read', ctypes.c_uint32, [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p])
    close = bind(api, 'VFS_CloseFile', ctypes.c_int, [ctypes.c_void_p])
    check_package = bind(api, 'VFS_CheckPackage', ctypes.c_bool, [ctypes.c_int])
    system = b'builtin'
    assert record('VFS_CreateSystem', create_system(system, path('prime'), path('override'), path('system-data')))
    descriptions = {}
    for package in config['packages']:
        handle = record('VFS_CreatePackageDescription_' + package, create_desc(system, package.encode(), package == '0'))
        assert handle >= 0
        descriptions[package] = handle
        assert record('VFS_SetHasPrime_' + package, has_prime(handle, True))
        assert record('VFS_Mount_prime_' + package, mount(handle, 0, 0))
    for package, handle in descriptions.items():
        if args.phase == 'create':
            assert record('VFS_CreateOverrideFromPrime_' + package, create_override(handle))
        assert record('VFS_Mount_override_' + package, mount(handle, 1, 0))
    if args.phase == 'verify':
        for package, handle in descriptions.items():
            assert record('VFS_CheckPackage_' + package, check_package(handle))
    for sample in config['samples']:
        expected_path = sample['modified_file'] if args.phase == 'verify' else sample['original_file']
        expected = (lab / expected_path).read_bytes()
        handle = open_hash(system, int(sample['id_hex'], 16), b'rb')
        assert handle, sample['name']
        buffer = ctypes.create_string_buffer(len(expected) + 16)
        count = read(buffer, 1, len(buffer), handle)
        assert close(handle) == 0
        actual = bytes(buffer.raw[:count])
        result['reads'].append({'name': sample['name'], 'id_hex': sample['id_hex'], 'bytes': count, 'sha256': hashlib.sha256(actual).hexdigest(), 'expected_sha256': hashlib.sha256(expected).hexdigest(), 'matches': actual == expected})
        save()
        assert actual == expected, sample['name']
    print(json.dumps({'phase': args.phase, 'verified_resources': len(result['reads']), 'all_match': True}))


if __name__ == '__main__':
    main()
