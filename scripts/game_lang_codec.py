"""Lossless text-only codec for verified Qts zh_cn .lang protobuf resources."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata
from typing import Mapping

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
MAX_BYTES = 128 * 1024 * 1024
HASH_KEY = re.compile(r'#[0-9A-Fa-f]{16}#')
TOKEN = re.compile(r'\{[^{}\r\n]*\}|</?[A-Za-z][^<>]*>|%(?:\d+\$)?[-+ #0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[diuoxXfFeEgGaAcCsSpn%]|\[(?:[0-9A-Fa-f]{6,8}|/?[biu]|-)\]')
MARKUP = re.compile(r'</?[A-Za-z][^<>]*>')
NUMBER = re.compile(r'[-+\u2212]?[0-9]+(?:[.,][0-9]+)?(?:[eE][-+]?[0-9]+)?(?:[ \t]*[%\uff05])?')
NEWLINE = re.compile(r'\r\n|[\r\n\u2028\u2029]')
EXPRESSION = re.compile(r'\[[0-9A-Za-z_$().+-][0-9A-Za-z_$().+*/%?:=<>|& \t-]*\]')


class CodecError(ValueError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_varint(data: bytes, position: int) -> tuple[int, int]:
    result = 0
    for index in range(10):
        if position >= len(data):
            raise CodecError('Truncated protobuf varint')
        byte = data[position]
        position += 1
        if index == 9 and byte > 1:
            raise CodecError('Protobuf varint exceeds uint64')
        result |= (byte & 127) << (index * 7)
        if byte < 128:
            return result, position
    raise CodecError('Oversized protobuf varint')


def varint(value: int) -> bytes:
    if not 0 <= value < 1 << 64:
        raise CodecError('Invalid uint64 varint')
    output = bytearray()
    while value >= 128:
        output.append((value & 127) | 128)
        value >>= 7
    output.append(value)
    return bytes(output)


@dataclass(frozen=True)
class WireField:
    number: int
    wire: int
    tag: bytes
    length: bytes
    payload: bytes
    raw: bytes

    def replace_payload(self, payload: bytes) -> bytes:
        if self.wire != 2:
            raise CodecError('Only a known length-delimited field may be edited')
        if payload == self.payload:
            return self.raw
        length = self.length if len(payload) == len(self.payload) else varint(len(payload))
        return self.tag + length + payload


def _field(data: bytes, position: int, depth: int = 0) -> tuple[WireField, int]:
    if depth > 64:
        raise CodecError('Protobuf group nesting exceeds limit')
    start = position
    tag_value, position = read_varint(data, position)
    number, wire = tag_value >> 3, tag_value & 7
    if not 0 < number <= 536870911:
        raise CodecError('Invalid protobuf field number')
    tag = data[start:position]
    length = b''
    payload_start = position
    if wire == 0:
        _, position = read_varint(data, position)
    elif wire in (1, 5):
        position += 8 if wire == 1 else 4
    elif wire == 2:
        size, size_end = read_varint(data, position)
        length = data[position:size_end]
        payload_start = size_end
        position = size_end + size
    elif wire == 3:
        while True:
            end_tag, end_position = read_varint(data, position)
            if end_tag & 7 == 4:
                if end_tag >> 3 != number:
                    raise CodecError('Mismatched protobuf end-group field')
                position = end_position
                break
            _, position = _field(data, position, depth + 1)
    else:
        raise CodecError('Unexpected end-group or invalid protobuf wire type')
    if position > len(data):
        raise CodecError('Protobuf field exceeds input bounds')
    return WireField(number, wire, tag, length, data[payload_start:position], data[start:position]), position


def fields(data: bytes) -> tuple[WireField, ...]:
    if len(data) > MAX_BYTES:
        raise CodecError('Payload exceeds codec size limit')
    position = 0
    result = []
    while position < len(data):
        field, position = _field(data, position)
        result.append(field)
    return tuple(result)


@dataclass(frozen=True)
class LangRow:
    outer_index: int
    fields: tuple[WireField, ...]
    key_index: int
    text_index: int
    key: str
    text: str

    @property
    def unknown_fields(self) -> tuple[WireField, ...]:
        return tuple(field for index, field in enumerate(self.fields) if index not in (self.key_index, self.text_index))


def numeric_components(text: str) -> Counter:
    return Counter(value.replace(',', '.').replace('\u2212', '-').replace('\uff05', '%').replace(' ', '').replace('\t', '') for value in NUMBER.findall(TOKEN.sub(' ', text)))


class ReviewedUnitConversions:
    """Allow only exact reviewed fixed literals whose base quantities are equal."""
    def __init__(self, records: list[dict]):
        self.pairs = {}
        for record in records:
            source, target = record['source'], record['target']
            if record['id'] != 'nt-' + sha(source.encode('utf-8'))[:12]:
                raise CodecError('Unit conversion source identity differs')
            spans = []
            for side, text, units in (
                ('source_span', source, {'万': 10000, '亿': 100000000}),
                ('target_span', target, {'тыс.': 1000, 'млн': 1000000, 'млрд': 1000000000}),
            ):
                span = record[side]
                start, end = span['start'], span['end']
                if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text) or text[start:end] != span['text']:
                    raise CodecError('Unit conversion span changed')
                if span['unit'] not in units or span['unit_factor'] != units[span['unit']]:
                    raise CodecError('Unit conversion has an unsupported unit')
                pattern = r'([0-9]+(?:[.,][0-9]+)?)[ \t]*' + re.escape(span['unit'])
                match = re.fullmatch(pattern, span['text'])
                if not match:
                    raise CodecError('Unit conversion must be a fixed display literal')
                number = Decimal(match[1].replace(',', '.'))
                if isinstance(span['display_number'], bool) or number != Decimal(str(span['display_number'])):
                    raise CodecError('Unit conversion display number differs')
                base = number * units[span['unit']]
                if isinstance(span['base_value'], bool) or base != Decimal(str(span['base_value'])):
                    raise CodecError('Unit conversion base value differs')
                spans.append((start, end, base))
            if spans[0][2] != spans[1][2]:
                raise CodecError('Unit conversion changed the quantity')
            key = (sha(source.encode('utf-8')), sha(target.encode('utf-8')))
            pair = self.pairs.setdefault(key, {'source': source, 'target': target, 'spans': []})
            pair['spans'].append((spans[0][:2], spans[1][:2]))
        for pair in self.pairs.values():
            masked = []
            for side in (0, 1):
                text = pair['source' if side == 0 else 'target']
                positions = sorted(span[side] for span in pair['spans'])
                if any(first[1] > second[0] for first, second in zip(positions, positions[1:])):
                    raise CodecError('Unit conversion spans overlap')
                for start, end in reversed(positions):
                    text = text[:start] + ' ' + text[end:]
                masked.append(text)
            if numeric_components(masked[0]) != numeric_components(masked[1]):
                raise CodecError('Unit conversion changed another numeric component')

    def covers(self, source: str, target: str) -> bool:
        return (sha(source.encode('utf-8')), sha(target.encode('utf-8'))) in self.pairs


def load_unit_conversions(proofs: list[dict]) -> ReviewedUnitConversions | None:
    if not proofs:
        return None
    records = []
    for proof in proofs:
        path = (PROJECT / proof['project_path']).resolve(strict=True)
        if not path.is_relative_to((PROJECT / 'translations/game-lang').resolve()) or sha(path.read_bytes()) != proof['sha256']:
            raise CodecError('Reviewed unit conversion proof path/hash differs')
        document = json.loads(path.read_text('utf-8-sig'), object_pairs_hook=unique_object)
        if document.get('schema_version') != 1 or not isinstance(document.get('conversions'), list):
            raise CodecError('Invalid reviewed unit conversion proof')
        records.extend(document['conversions'])
    return ReviewedUnitConversions(records)


def validate_text(source: str, translated: str, *, unit_conversions: ReviewedUnitConversions | None = None) -> list[str]:
    if not isinstance(translated, str):
        return ['non-string replacement']
    if translated == source:
        return []
    issues = []
    if not translated.strip():
        issues.append('empty replacement')
    if Counter(TOKEN.findall(source)) != Counter(TOKEN.findall(translated)):
        issues.append('placeholder/printf/markup token mismatch')
    if MARKUP.findall(source) != MARKUP.findall(translated):
        issues.append('markup order/nesting mismatch')
    if EXPRESSION.findall(source) != EXPRESSION.findall(translated):
        issues.append('runtime expression/order mismatch')
    if NEWLINE.findall(source) != NEWLINE.findall(translated):
        issues.append('newline sequence mismatch')
    def icons(text):
        return Counter(char for char in text if '\u2500' <= char <= '\u257f' or '\ue000' <= char <= '\uf8ff')
    if icons(source) != icons(translated):
        issues.append('native icon-marker mismatch')
    masked_source, masked_target = (TOKEN.sub(' ', text) for text in (source, translated))
    if numeric_components(source) != numeric_components(translated) and not (unit_conversions and unit_conversions.covers(source, translated)):
        issues.append('literal number/percentage mismatch')
    def non_ascii_numbers(text):
        return Counter(char for char in text if char not in '0123456789' and unicodedata.category(char) in ('Nd', 'Nl', 'No'))
    if non_ascii_numbers(masked_source) != non_ascii_numbers(masked_target):
        issues.append('non-ASCII number mismatch')
    if Counter(char for char in masked_source if char in '{}\t\x00') != Counter(char for char in masked_target if char in '{}\t\x00'):
        issues.append('escaped-brace/control-character mismatch')
    return issues


@dataclass(frozen=True)
class LangDocument:
    original: bytes
    fields: tuple[WireField, ...]
    rows: tuple[LangRow, ...]

    @classmethod
    def parse(cls, data: bytes) -> LangDocument:
        outer = fields(data)
        rows = []
        seen = set()
        for index, field in enumerate(outer):
            if (field.number, field.wire) != (1, 2):
                continue
            row_fields = fields(field.payload)
            key_indices = [i for i, f in enumerate(row_fields) if (f.number, f.wire) == (1, 2)]
            text_indices = [i for i, f in enumerate(row_fields) if (f.number, f.wire) == (2, 2)]
            if len(key_indices) != 1 or len(text_indices) != 1:
                raise CodecError('Lang row must have exactly one UTF8 key1 and text2')
            key_index, text_index = key_indices[0], text_indices[0]
            try:
                key, text = row_fields[key_index].payload.decode('utf-8'), row_fields[text_index].payload.decode('utf-8')
            except UnicodeDecodeError as error:
                raise CodecError('Invalid UTF8 lang key/text') from error
            if not HASH_KEY.fullmatch(key) or key in seen:
                raise CodecError('Lang keys must be unique #16hex# identities')
            seen.add(key)
            rows.append(LangRow(index, row_fields, key_index, text_index, key, text))
        if data and not rows:
            raise CodecError('No lang text rows; numerical/config payload refused')
        return cls(data, outer, tuple(rows))

    def pairs(self) -> list[dict]:
        return [{'key': row.key, 'text': row.text} for row in self.rows]

    @property
    def unknown_fields(self) -> tuple[WireField, ...]:
        row_indices = {row.outer_index for row in self.rows}
        return tuple(field for index, field in enumerate(self.fields) if index not in row_indices)

    def replace(self, translations: Mapping[str, str], *, unit_conversions: ReviewedUnitConversions | None = None) -> bytes:
        known = {row.key for row in self.rows}
        if set(translations) - known:
            raise CodecError('Replacement keys are absent from the source lang resource')
        replacements = {}
        for row in self.rows:
            if row.key not in translations:
                continue
            text = translations[row.key]
            issues = validate_text(row.text, text, unit_conversions=unit_conversions)
            if issues:
                raise CodecError(f'{row.key}: ' + '; '.join(issues))
            if text == row.text:
                continue
            try:
                encoded = text.encode('utf-8')
            except UnicodeEncodeError as error:
                raise CodecError('Replacement text is not encodable as UTF8') from error
            payload = b''.join(field.replace_payload(encoded) if index == row.text_index else field.raw for index, field in enumerate(row.fields))
            replacements[row.outer_index] = self.fields[row.outer_index].replace_payload(payload)
        return b''.join(replacements.get(index, field.raw) for index, field in enumerate(self.fields))


def validate_rewrite(source: LangDocument, result: LangDocument, translations: Mapping[str, str], *, unit_conversions: ReviewedUnitConversions | None = None) -> None:
    """Check that a wire rewrite changed only the selected text payloads and lengths."""
    if len(source.fields) != len(result.fields) or len(source.rows) != len(result.rows):
        raise CodecError('Rebuilt lang field/row count changed')
    if [row.key for row in source.rows] != [row.key for row in result.rows]:
        raise CodecError('Rebuilt lang key/order changed')
    row_indices = {row.outer_index for row in source.rows}
    for index, (before, after) in enumerate(zip(source.fields, result.fields)):
        if index not in row_indices and before.raw != after.raw:
            raise CodecError('Unknown top-level protobuf wire field changed')
    for before, after in zip(source.rows, result.rows):
        expected = translations.get(before.key, before.text)
        if after.text != expected or validate_text(before.text, after.text, unit_conversions=unit_conversions):
            raise CodecError('Rebuilt lang text/invariant mismatch')
        if (before.outer_index, before.key_index, before.text_index) != (after.outer_index, after.key_index, after.text_index) or len(before.fields) != len(after.fields):
            raise CodecError('Rebuilt lang row field order changed')
        for index, (old, new) in enumerate(zip(before.fields, after.fields)):
            if index != before.text_index and old.raw != new.raw:
                raise CodecError('Key or unknown row protobuf wire field changed')
            if index == before.text_index and old.tag != new.tag:
                raise CodecError('Known text field tag encoding changed')
        old_outer, new_outer = source.fields[before.outer_index], result.fields[after.outer_index]
        if old_outer.tag != new_outer.tag or (before.text == after.text and old_outer.raw != new_outer.raw):
            raise CodecError('Row tag or untouched row bytes changed')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CodecError('Duplicate JSON identity: ' + key)
        result[key] = value
    return result


def verified_source(path: Path) -> tuple[LangDocument, dict]:
    from game_inspect import qts_path_id
    path = path.resolve(strict=True)
    manifest = json.loads((PROJECT / 'source/game-coverage/native-cn-manifest.json').read_text('utf-8-sig'))
    records = [row for row in manifest['resources'] if (PROJECT / row['project_path']).resolve() == path]
    if len(records) != 1:
        raise CodecError('Input is not a captured native CN lang resource')
    record = records[0]
    if record['locale'] != 'zh_cn' or record['package'] != '1' or not record['virtual_path'].endswith('.zh_cn.lang') or qts_path_id(record['virtual_path']) != int(record['resource_id_hex'], 16):
        raise CodecError('Input lang locale/path/resource identity mismatch')
    data = path.read_bytes()
    if sha(data) != record['decoded_sha256'] or len(data) != record['decoded_bytes']:
        raise CodecError('Captured source bytes differ from native provenance')
    document = LangDocument.parse(data)
    if len(document.rows) != record['pair_count'] or sha('\n'.join(row.key for row in document.rows).encode('utf-8')) != record['ordered_keys_sha256']:
        raise CodecError('Native lang row count mismatch')
    return document, record


def output_path(path: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to((PROJECT / 'build/game-lang').resolve()):
        raise CodecError('Codec outputs must remain under project build/game-lang')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inspect', 'apply'])
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--translations', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    document, origin = verified_source(args.input)
    report = {'schema': 'qts-cn-lang/v1', 'origin': origin, 'source_sha256': sha(document.original), 'row_count': len(document.rows), 'unknown_top_fields': len(document.unknown_fields), 'unknown_row_fields': sum(len(row.unknown_fields) for row in document.rows), 'source_wire_roundtrip_exact': document.replace({}) == document.original}
    if args.action == 'inspect':
        output_path(args.output).write_text(json.dumps(report | {'pairs': document.pairs()}, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    else:
        if not args.translations or not args.translations.resolve(strict=True).is_relative_to(PROJECT):
            parser.error('apply requires a reviewed project JSON key→text translation map')
        translations = json.loads(args.translations.read_text('utf-8-sig'), object_pairs_hook=unique_object)
        if not isinstance(translations, dict):
            raise CodecError('Translations must be a key→text JSON object')
        modified = document.replace(translations)
        result = LangDocument.parse(modified)
        validate_rewrite(document, result, translations)
        report.update({'output_sha256': sha(modified), 'changed_rows': sum(a.text != b.text for a, b in zip(document.rows, result.rows)), 'keys_order_preserved': True, 'unknown_wire_fields_preserved': True, 'text_invariant_issues': 0, 'numerical_config_edits': 0})
        output_path(args.output).write_bytes(modified)
        if args.report:
            output_path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'origin'}))


if __name__ == '__main__':
    main()
