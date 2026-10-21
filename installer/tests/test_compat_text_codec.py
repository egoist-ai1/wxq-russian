"""Lossless перенос старых строк в новый protobuf-контейнер, без игры и установки.

    python -X utf8 -B installer/tests/test_compat_text_codec.py

Штатный C#5 код собирается с warnaserror. Независимые Python codecs проверяют
выходные байты; все запросы и исполняемый стенд лежат только в tempfile.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
from scripts.game_lang_codec import LangDocument, validate_rewrite, varint  # noqa: E402
from localization.global_map_codec import GlobalMapDocument, prove_text_only  # noqa: E402

CSC = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
HARNESS = r'''
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;
using Egoist.WXQRussian;
class CompatHarness {
    static string Str(Dictionary<string,object> row, string key) {
        object value;
        return row.TryGetValue(key, out value) ? value as string : null;
    }
    static int Main(string[] args) {
        var json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
        try {
            var request = (Dictionary<string,object>)json.DeserializeObject(File.ReadAllText(args[0], new UTF8Encoding(false, true)));
            var rows = new List<CompatTextRow>();
            foreach (var item in (IEnumerable)request["rows"]) {
                var value = item as Dictionary<string,object>;
                rows.Add(value == null ? null : new CompatTextRow {
                    Key = Str(value,"key"), SourceSha = Str(value,"source_sha256"), Target = Str(value,"target")
                });
            }
            // JavaScriptSerializer заменяет одиночные суррогаты ещё до codec.
            // Этот отрицательный случай создаётся непосредственно в .NET string.
            if (Str(request,"invalid_surrogate") == "target") rows[0].Target = new string((char)0xd800, 1);
            if (Str(request,"invalid_surrogate") == "key") rows[0].Key = new string((char)0xdfff, 1);
            string codec = Str(request,"codec");
            if (Str(request,"mode") == "validate") {
                CompatTextMap.ValidateRows(codec, rows);
                Console.WriteLine(json.Serialize(new { ok = true, validated = true }));
            } else {
                byte[] data = request.ContainsKey("source_size")
                    ? new byte[Convert.ToInt32(request["source_size"])] : Convert.FromBase64String(Str(request,"source_b64"));
                var result = CompatTextMap.Reuse(data, codec, rows);
                Console.WriteLine(json.Serialize(new {
                    ok = true, payload_b64 = Convert.ToBase64String(result.Payload),
                    applied = result.Applied, changed = result.SourceChanged, missing = result.Missing,
                    current_rows = result.CurrentRows, same_reference = Object.ReferenceEquals(data, result.Payload),
                    source_unchanged = Str(request,"source_b64") == Convert.ToBase64String(data)
                }));
            }
        } catch (Exception error) {
            Console.WriteLine(json.Serialize(new { ok = false, error = error.GetType().Name, code = error.Message }));
        }
        return 0;
    }
}
'''


def sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def length(value: int, noncanonical: bool = False) -> bytes:
    encoded = varint(value)
    return encoded[:-1] + bytes([encoded[-1] | 128, 0]) if noncanonical else encoded


def wire(number: int, kind: int, payload: bytes | int, *, weird: bool = False) -> bytes:
    tag = length(number * 8 + kind, weird)
    if kind == 2:
        assert isinstance(payload, bytes)
        return tag + length(len(payload), weird) + payload
    if kind == 0:
        assert isinstance(payload, int)
        return tag + length(payload, weird)
    assert isinstance(payload, bytes)
    return tag + payload


def entry(key: str, text: str, *, extra: bytes = b'', weird: bool = False, text_first: bool = False) -> bytes:
    key_field = wire(1, 2, key.encode(), weird=weird)
    text_field = wire(2, 2, text.encode(), weird=weird)
    body = (text_field + extra + key_field) if text_first else (key_field + extra + text_field)
    return wire(1, 2, body, weird=weird)


def translation(key: str, source: str, target: str) -> dict:
    return {'key': key, 'source_sha256': sha(source), 'target': target}


class CompatTextCodecTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = Path(tempfile.mkdtemp(prefix='wxq-compat-codec-'))
        print('Песочница: ' + str(cls.work), flush=True)
        (cls.work / 'Harness.cs').write_text(HARNESS, 'utf-8')
        cls.exe = cls.work / 'CompatHarness.exe'
        process = subprocess.run([str(CSC), '/nologo', '/utf8output', '/warnaserror+', '/optimize+',
                                  '/platform:x64', '/target:exe', '/codepage:65001', '/r:System.Web.Extensions.dll',
                                  '/out:' + str(cls.exe), str(PROJECT / 'installer/WXQCompatText.cs'),
                                  str(cls.work / 'Harness.cs')], capture_output=True, text=True, encoding='utf-8')
        if process.returncode:
            raise RuntimeError(process.stdout + process.stderr)
        cls.sequence = 0

    def invoke(self, data: bytes | None, rows: list, codec: str = 'global-map-v1', **extra) -> dict:
        type(self).sequence += 1
        request = {'codec': codec, 'rows': rows, **extra}
        if data is not None:
            request['source_b64'] = base64.b64encode(data).decode('ascii')
        path = self.work / f'request-{self.sequence:03}.json'
        path.write_text(json.dumps(request, ensure_ascii=True), 'utf-8')
        process = subprocess.run([str(self.exe), str(path)], capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        (self.work / f'result-{self.sequence:03}.json').write_text(json.dumps(result, ensure_ascii=False), 'utf-8')
        return result

    def payload(self, result: dict) -> bytes:
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['source_unchanged'], result)
        return base64.b64decode(result['payload_b64'])

    def refused(self, data: bytes | None, rows: list, codec: str = 'global-map-v1', **extra) -> dict:
        result = self.invoke(data, rows, codec, **extra)
        diagnostic = {key: value for key, value in result.items() if key != 'payload_b64'}
        self.assertFalse(result['ok'], diagnostic)
        self.assertEqual(result['error'], 'FormatException', diagnostic)
        self.assertTrue(result['code'].startswith('compat-text-'), diagnostic)
        return result

    def test_partial_update_keeps_new_changed_and_unknown_fields(self):
        numeric = wire(3, 0, 917) + wire(4, 1, b'12345678') + wire(5, 5, b'ABCD')
        opaque = wire(8, 2, b'\xff\x00\x81')
        data = wire(7, 2, b'new root bytes') + entry('new-key', '新字符串')
        data += entry('stable-key', '胜利 {0}', extra=numeric + opaque, text_first=True)
        data += entry('changed-key', '新原文') + wire(9, 0, 123456789)
        rows = [translation('stable-key', '胜利 {0}', 'Победа {0}'),
                translation('changed-key', '旧原文', 'Старый перевод'),
                translation('missing-key', '消失', 'Исчезло')]
        result = self.invoke(data, rows)
        actual = self.payload(result)
        expected = GlobalMapDocument.parse(data).replace_text({'stable-key': 'Победа {0}'})
        self.assertEqual(actual, expected)
        self.assertEqual((result['applied'], result['changed'], result['missing'], result['current_rows']), (1, 1, 1, 3))
        self.assertTrue(prove_text_only(data, actual, {'stable-key': 'Победа {0}'})['controlled_inverse_byte_exact'])

    def test_lang_hash_key_and_unicode_tokens_preserved(self):
        key, other = '#0123456789ABCDEF#', '#FEDCBA9876543210#'
        data = wire(12, 0, 42) + entry(other, '未来', extra=wire(17, 2, b'\xff'))
        data += entry(key, '胜利 {0}\n<color=red>100%</color>', extra=wire(3, 5, b'\x00\xff\x00\x01'), text_first=True)
        target = 'Победа {0}\n<color=red>100%</color>'
        actual = self.payload(self.invoke(data, [translation(key, '胜利 {0}\n<color=red>100%</color>', target)], 'lang-map-v1'))
        before, after = LangDocument.parse(data), LangDocument.parse(actual)
        expected = before.replace({key: target})
        self.assertEqual(actual, expected)
        validate_rewrite(before, after, {key: target})

    def test_reordered_rows_use_keys_and_current_schema(self):
        data = entry('B', '乙', extra=wire(31, 0, 999)) + entry('new', '新的') + entry('A', '甲')
        rows = [translation('A', '甲', 'А'), translation('B', '乙', 'Б')]
        result = self.invoke(data, rows)
        actual = self.payload(result)
        self.assertEqual(actual, GlobalMapDocument.parse(data).replace_text({'A': 'А', 'B': 'Б'}))
        self.assertEqual([row.key for row in GlobalMapDocument.parse(actual).rows], ['B', 'new', 'A'])
        self.assertEqual(result['applied'], 2)

    def test_noncanonical_lengths_tags_equal_size(self):
        data = entry('K', '胜', weird=True, extra=wire(7, 0, 17, weird=True))
        actual = self.payload(self.invoke(data, [translation('K', '胜', 'Я!')]))
        self.assertEqual(len(actual), len(data))
        self.assertEqual(actual, GlobalMapDocument.parse(data).replace_text({'K': 'Я!'}))
        prove_text_only(data, actual, {'K': 'Я!'})

    def test_noncanonical_lengths_resize_only_needed_frames(self):
        data = entry('K', '胜', weird=True, extra=wire(7, 2, b'keep', weird=True))
        actual = self.payload(self.invoke(data, [translation('K', '胜', 'Победа')]))
        self.assertEqual(actual, GlobalMapDocument.parse(data).replace_text({'K': 'Победа'}))
        prove_text_only(data, actual, {'K': 'Победа'})

    def test_empty_metadata_is_exact_noop(self):
        data = entry('K', '原文', weird=True) + wire(8, 2, b'\xff\x00')
        result = self.invoke(data, [])
        self.assertEqual(self.payload(result), data)
        self.assertTrue(result['same_reference'])
        self.assertEqual((result['applied'], result['changed'], result['missing']), (0, 0, 0))

    def test_existing_target_and_second_application_are_noop(self):
        data = entry('K', '原文')
        rows = [translation('K', '原文', 'Перевод')]
        first = self.payload(self.invoke(data, rows))
        result = self.invoke(first, rows)
        self.assertEqual(self.payload(result), first)
        self.assertTrue(result['same_reference'])
        self.assertEqual((result['applied'], result['changed']), (0, 1))
        unchanged = self.invoke(data, [translation('K', '原文', '原文')])
        self.assertEqual(self.payload(unchanged), data)
        self.assertTrue(unchanged['same_reference'])

    def test_hash_case_is_irrelevant_but_key_case_is_exact(self):
        key = '#ABCDEF0123456789#'
        data = entry(key, '原文')
        row = translation(key, '原文', 'Перевод')
        row['source_sha256'] = row['source_sha256'].upper()
        result = self.invoke(data, [row], 'lang-map-v1')
        self.assertEqual(result['applied'], 1)
        wrong_key = translation(key.lower(), '原文', 'Перевод')
        result = self.invoke(data, [wrong_key], 'lang-map-v1')
        self.assertEqual(self.payload(result), data)
        self.assertEqual(result['missing'], 1)

    def test_null_codepoint_is_preserved_as_data(self):
        data = entry('K', '\x00原文')
        target = '\x00Перевод'
        actual = self.payload(self.invoke(data, [translation('K', '\x00原文', target)]))
        self.assertEqual(actual, GlobalMapDocument.parse(data).replace_text({'K': target}))
        prove_text_only(data, actual, {'K': target})

    def test_global_empty_target_is_wire_compatible(self):
        data = entry('K', '原文')
        actual = self.payload(self.invoke(data, [translation('K', '原文', '')]))
        self.assertEqual(actual, GlobalMapDocument.parse(data).replace_text({'K': ''}))

    def test_invalid_metadata_is_refused(self):
        valid = translation('K', '原文', 'Перевод')
        variants = [
            ('null-row', [None]), ('duplicate', [valid, valid]),
            ('no-hash', [{'key': 'K', 'target': 'RU'}]),
            ('bad-hash', [{**valid, 'source_sha256': '0' * 63}]),
            ('hash-newline', [{**valid, 'source_sha256': '0' * 64 + '\n'}]),
            ('empty-key', [{**valid, 'key': ''}]),
            ('large-key', [{**valid, 'key': 'я' * 257}]),
            ('null-target', [{**valid, 'target': None}]),
            ('large-target', [{**valid, 'target': 'x' * (16 * 1024 + 1)}]),
        ]
        for label, rows in variants:
            with self.subTest(label=label):
                self.refused(entry('K', '原文'), rows)
        for role in ('key', 'target'):
            with self.subTest(invalid_utf8=role):
                self.refused(entry('K', '原文'), [valid], invalid_surrogate=role)
        for key in ('K', '#0123456789ABCDEF#\n'):
            with self.subTest(lang_key=key):
                self.refused(entry('#0123456789ABCDEF#', '原文'), [{**valid, 'key': key}], 'lang-map-v1')
        self.refused(entry('K', '原文'), [valid], codec='unsupported-map')

    def test_invalid_known_schema_and_duplicates_are_refused(self):
        key = wire(1, 2, b'K')
        text = wire(2, 2, '原文'.encode())
        variants = {
            'empty': b'', 'non-row': wire(9, 0, 17),
            'duplicate-key': entry('K', '甲') + entry('K', '乙'),
            'root-wire': wire(1, 0, 99),
            'row-key-wire': wire(1, 2, wire(1, 0, 7) + text),
            'row-text-wire': wire(1, 2, key + wire(2, 5, b'1234')),
            'duplicate-key-field': wire(1, 2, key + key + text),
            'duplicate-text-field': wire(1, 2, key + text + text),
            'missing-text': wire(1, 2, key), 'missing-key': wire(1, 2, text),
            'invalid-key-utf8': wire(1, 2, wire(1, 2, b'\xff') + text),
            'invalid-text-utf8': wire(1, 2, key + wire(2, 2, b'\xff')),
            'large-key': entry('K' * 513, '原文'),
            'large-text': entry('K', 'x' * (256 * 1024 + 1)),
        }
        for label, data in variants.items():
            with self.subTest(label=label):
                self.refused(data, [])

    def test_truncation_invalid_tags_and_groups_are_refused(self):
        variants = {
            'field-zero': b'\x00', 'truncated-varint': b'\x80',
            'uint64-overflow': b'\x80' * 9 + b'\x02',
            'huge-field-number': varint(536870912 * 8),
            'truncated-body': b'\x0a\x05AB', 'truncated-fixed': wire(8, 5, b'A'),
            'invalid-wire': b'\x0f',
            'root-group': b'\x43\x48\x01\x44' + entry('K', '原文'),
            'row-group': entry('K', '原文', extra=b'\x1b\x20\x01\x1c'),
        }
        for label, data in variants.items():
            with self.subTest(label=label):
                self.refused(data, [])

    def test_field_row_and_document_budgets_are_refused(self):
        self.refused(wire(3, 0, 0) * 300001 + entry('K', '原文'), [])
        self.refused(b''.join(entry(f'K{index}', '') for index in range(65537)), [])
        self.refused(None, [], source_size=8 * 1024 * 1024 + 1)
        self.refused(None, [], codec='lang-map-v1', source_size=128 * 1024 * 1024 + 1)
        self.refused(entry('K', '原文'), [None] * 32769)

    def test_current_limits_preserve_unknown_rows_and_fields(self):
        unknown_field = wire(11, 2, b'\x00\xfffuture-field')
        unknown_text = 'x' * (32 * 1024)
        known_row = entry('known-key', '原文')
        unknown_row = entry('future-key', unknown_text, extra=unknown_field)
        later_rows = b''.join(entry(f'future-{index}', '') for index in range(32767))
        data = known_row + unknown_row + later_rows

        result = self.invoke(data, [translation('known-key', '原文', 'Перевод')])
        actual = self.payload(result)

        self.assertEqual(result['current_rows'], 32769)
        self.assertEqual(result['applied'], 1)
        self.assertEqual(actual, entry('known-key', 'Перевод') + unknown_row + later_rows)
        self.assertIn(unknown_field, actual)
        self.assertIn(entry('future-key', unknown_text, extra=unknown_field), actual)

    def test_output_budget_is_enforced_before_writing(self):
        data = wire(9, 2, b'x' * (8 * 1024 * 1024 - 200)) + entry('K', '原文')
        self.assertLess(len(data), 8 * 1024 * 1024)
        result = self.refused(data, [translation('K', '原文', 'Я' * 8192)])
        self.assertEqual(result['code'], 'compat-text-output-budget')

    def test_largest_allowed_target_and_key_work(self):
        key = 'Я' * 256
        data = entry(key, '原文')
        target = 'Я' * 8192
        actual = self.payload(self.invoke(data, [translation(key, '原文', target)]))
        self.assertEqual(actual, GlobalMapDocument.parse(data).replace_text({key: target}))
        prove_text_only(data, actual, {key: target})


if __name__ == '__main__':
    unittest.main(verbosity=2)
