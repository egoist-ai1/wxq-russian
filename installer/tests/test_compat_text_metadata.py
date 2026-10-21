"""Структурные метаданные точного переиспользования языковых строк.

    python -X utf8 -B installer/tests/test_compat_text_metadata.py [-v]

Тесты не пишут в игру и не запускают её. При наличии локальных prime 1.6.0.1
и последнего кандидата дополнительно читаются 296 lang и один Global ресурс.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[2]
for directory in (PROJECT, PROJECT / 'installer', PROJECT / 'scripts'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import build_compat_text as compat  # noqa: E402
from scripts.game_lang_codec import CodecError, varint  # noqa: E402
from localization.global_map_codec import GlobalMapDocument  # noqa: E402


def field(number: int, value: bytes, *, long_length: bool = False) -> bytes:
    length = varint(len(value))
    if long_length:
        length = length[:-1] + bytes([length[-1] | 128, 0])
    return varint(number * 8 + 2) + length + value


def integer(number: int, value: int) -> bytes:
    return varint(number * 8) + varint(value)


def row(key: str, text: str, extra: bytes = b'', *, long_length: bool = False) -> bytes:
    payload = field(1, key.encode('utf-8')) + extra + field(2, text.encode('utf-8'), long_length=long_length)
    return field(1, payload, long_length=long_length)


class CompatMetadataTest(unittest.TestCase):
    def test_unsupported_kind_is_not_interpreted_as_a_language_map(self):
        self.assertIsNone(compat.make_reuse('pb-table-text', b'bad', b'bad'))

    def test_lang_changed_rows_only_keep_source_hash_and_target(self):
        first, second = '#0123456789abcdef#', '#FEDCBA9876543210#'
        original = row(first, '已知{0}', integer(17, 45)) + row(second, '相同') + integer(9, 270)
        translated = row(first, 'Известно{0}', integer(17, 45)) + row(second, '相同') + integer(9, 270)
        expected = {'schema': 1, 'codec': 'lang-map-v1', 'rows': [
            {'key': first, 'source_sha256': hashlib.sha256('已知{0}'.encode()).hexdigest(), 'target': 'Известно{0}'}]}
        self.assertEqual(compat.make_reuse('lang', original, translated), expected)
        self.assertNotIn('已知', json.dumps(expected, ensure_ascii=False))

    def test_global_exact_wire_inverse_and_deterministic_order(self):
        # Группа неизвестного поля и неканонические длины сохраняются; исходники не попадают в карту.
        group = b'\x4b' + integer(11, 3) + b'\x4c'
        original = row('B', '甲乙', group, long_length=True) + row('A', '同') + integer(4, 1234)
        # Равная UTF-8 длина сохраняет прежнюю двухбайтную длину поля и строки.
        translated = row('B', 'абв', group, long_length=True) + row('A', '同') + integer(4, 1234)
        result = compat.make_reuse('global-localize', original, translated)
        self.assertEqual(result, compat.make_reuse('global-localize', original, translated))
        self.assertEqual(result['codec'], 'global-map-v1')
        self.assertEqual([item['key'] for item in result['rows']], ['B'])
        self.assertEqual(result['rows'][0]['target'], 'абв')
        self.assertEqual(GlobalMapDocument.parse(translated).rows[0].unknown_fields[0].raw, group)

    def test_changed_lengths_are_derived_from_the_original_frame(self):
        key = '#0123456789abcdef#'
        original = row(key, '一')
        translated = row(key, 'Длинная строка {0}')
        result = compat.make_reuse('lang', original, translated)
        self.assertEqual(result['rows'][0]['target'], 'Длинная строка {0}')

    def test_numeric_unit_conversion_is_not_rejected_by_structural_proof(self):
        original = row('#0123456789abcdef#', '1万')
        translated = row('#0123456789abcdef#', '10 тыс.')
        self.assertEqual(compat.make_reuse('lang', original, translated)['rows'][0]['target'], '10 тыс.')

    def test_empty_diff_produces_empty_metadata_without_wire_normalization(self):
        original = row('Menu', '一二', integer(7, 42), long_length=True)
        self.assertEqual(compat.make_reuse('global-localize', original, original),
                         {'schema': 1, 'codec': 'global-map-v1', 'rows': []})

    def test_reordered_or_added_or_changed_keys_are_refused(self):
        original = row('A', '一') + row('B', '二')
        for changed in (row('B', 'Два') + row('A', 'Один'), row('A', 'Один') + row('C', 'Два'),
                        row('A', 'Один') + row('B', 'Два') + row('C', 'Три')):
            with self.subTest(changed_bytes=len(changed)), self.assertRaises(CodecError):
                compat.make_reuse('global-localize', original, changed)

    def test_root_and_row_numeric_or_unknown_changes_are_refused(self):
        original = row('A', '一', integer(7, 9)) + integer(8, 17)
        for changed in (row('A', 'Один', integer(7, 10)) + integer(8, 17),
                        row('A', 'Один', integer(7, 9)) + integer(8, 18),
                        row('A', 'Один', field(7, b'changed')) + integer(8, 17)):
            with self.subTest(changed_bytes=len(changed)), self.assertRaises(CodecError):
                compat.make_reuse('global-localize', original, changed)

    def test_canonicalizing_unchanged_text_or_key_frames_is_refused(self):
        original = row('A', '一二', long_length=True)
        with self.assertRaisesRegex(CodecError, 'framing-drift'):
            compat.make_reuse('global-localize', original, row('A', '一二'))
        original = field(1, field(1, b'A', long_length=True) + field(2, '一'.encode()))
        with self.assertRaisesRegex(CodecError, 'key-or-unknown'):
            compat.make_reuse('global-localize', original, row('A', 'Два'))

    def test_duplicate_keys_fields_wrong_wire_and_invalid_utf8_are_refused(self):
        valid = row('A', '一')
        invalid = [valid + valid,
                   field(1, field(1, b'A') + field(1, b'A') + field(2, b'one')),
                   field(1, field(1, b'A') + field(2, b'one') + field(2, b'two')),
                   valid + integer(1, 4),
                   field(1, field(1, b'A') + field(2, b'one') + integer(2, 3)),
                   field(1, field(1, b'\xff') + field(2, b'one')),
                   field(1, field(1, b'A') + field(2, b'\xff')),
                   valid[:-1], b'']
        for value in invalid:
            with self.subTest(value_bytes=len(value)), self.assertRaises(CodecError):
                compat.make_reuse('global-localize', value, value)

    def test_lang_must_use_unique_hash_keys(self):
        for value in (row('A', '一'), row('#0123456789abcdef#', '一') * 2,
                      row('#0123456789abcdef#', '一') + integer(1, 3)):
            with self.subTest(value_bytes=len(value)), self.assertRaises(CodecError):
                compat.make_reuse('lang', value, value)

    def test_key_value_row_field_and_document_budgets_are_enforced(self):
        for value in (row('A' * 513, '一'), row('A', 'a' * 16385),
                      b''.join(row(str(index), 'x') for index in range(32769)),
                      row('A', 'x') + integer(3, 0) * 99998,
                      b'x' * (2 * 1024 * 1024 + 1)):
            with self.subTest(value_bytes=len(value)), self.assertRaises(CodecError):
                compat.make_reuse('global-localize', value, value)


GAME = Path(os.environ.get('WXQ_GAME_ROOT', r'C:\Program Files\WXQLauncher\WXQ(1)'))
CANDIDATE = PROJECT / 'build/carrier-2026-10-20-v13-1.6.0.1'


@unittest.skipUnless((GAME / 'WXQ_Data/Package/builtin/1/1_0.db').is_file() and (CANDIDATE / 'manifest.json').is_file(),
                     'Нет локальных prime и проверенного carrier 1.6.0.1')
class CurrentCarrierMetadataTest(unittest.TestCase):
    def test_all_current_lang_and_global_resources_replay_without_original_text_export(self):
        import pilot_contract
        from game_inspect import QtsContainer
        manifest = json.loads((CANDIDATE / 'manifest.json').read_text('utf-8-sig'))
        resources = [resource for resource in manifest['resources'] if resource['kind'] in compat.CODECS]
        self.assertEqual(len(resources), 297)
        self.assertEqual(sum(resource['kind'] == 'lang' for resource in resources), 296)
        reader = pilot_contract.make_prime_reader(GAME, QtsContainer)
        changed_rows = metadata_bytes = maximum_rows = maximum_value = group_fields = 0
        try:
            with QtsContainer(CANDIDATE / 'packages/1/1_0.db') as payloads:
                for resource in resources:
                    ident = int(resource['id_hex'], 16)
                    original = reader('1', ident)
                    translated = payloads.decode_entry(ident)
                    self.assertIsNotNone(original, resource['id_hex'])
                    self.assertEqual(hashlib.sha256(original).hexdigest(), resource['original_sha256'], resource['id_hex'])
                    self.assertEqual(hashlib.sha256(translated).hexdigest(), resource['sha256'], resource['id_hex'])
                    try:
                        metadata = compat.make_reuse(resource['kind'], original, translated)
                    except CodecError as error:
                        self.fail(resource['id_hex'] + ': ' + str(error))
                    self.assertEqual(set(metadata), {'schema', 'codec', 'rows'})
                    self.assertTrue(all(set(item) == {'key', 'source_sha256', 'target'} for item in metadata['rows']))
                    changed_rows += len(metadata['rows'])
                    metadata_bytes += len(json.dumps(metadata, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
                    maximum_rows = max(maximum_rows, len(metadata['rows']))
                    maximum_value = max(maximum_value, max((len(item['target'].encode('utf-8')) for item in metadata['rows']), default=0))
                    document = compat._parse(resource['kind'], original)
                    group_fields += sum(item.wire == 3 for item in document.fields)
                    group_fields += sum(item.wire == 3 for line in document.rows for item in line.fields)
        finally:
            reader.close()
        print(f'Current carrier: {len(resources)} ресурсов, {changed_rows} строк; replay/inverse PASS; '
              f'compact_metadata_bytes={metadata_bytes}, max_resource_rows={maximum_rows}, '
              f'max_target_utf8_bytes={maximum_value}, unknown_groups={group_fields}', flush=True)


if __name__ == '__main__':
    unittest.main()
