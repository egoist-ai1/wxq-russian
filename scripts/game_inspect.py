"""Read-only QtsVFS container index and bounded LZ4 resource inspection.

QtsVFS layout researched against Studio-HoK QtsVFSFile.cs by yarik0chka,
which attributes CUE4Parse/FabianFG and Apache-2.0 for this file:
https://raw.githubusercontent.com/yarik0chka/Studio-HoK/
9deed74d3f61c0b28ad6747184281b6adc1b0710/AssetStudio/QtsVFSFile.cs
This independent inspector reads source files through ACCESS_READ and never
writes them. No game execution, injection, update manifest modification,
or anti-cheat changes are performed.
"""

from __future__ import annotations
import argparse
import bisect
import collections
import hashlib
import json
import mmap
import pathlib
import re
import struct
from dataclasses import asdict, dataclass


class FormatError(ValueError):
    pass


@dataclass(frozen=True)
class Chunk:
    record_offset: int
    payload_offset: int
    compressed_size: int
    uncompressed_size: int
    main_block: int
    sub_block: int
    codec: str


def lz4_block_decode(source: bytes, expected: int) -> bytes:
    """Decode an independent raw LZ4 block with explicit output bounds."""
    result = bytearray()
    pos = 0
    length = len(source)

    def extended(base: int) -> int:
        nonlocal pos
        if base == 15:
            while True:
                if pos >= length:
                    raise FormatError('Truncated LZ4 extended length')
                value = source[pos]
                pos += 1
                base += value
                if value != 255:
                    break
        return base

    while pos < length:
        token = source[pos]
        pos += 1
        literal_count = extended(token >> 4)
        if pos + literal_count > length or len(result) + literal_count > expected:
            raise FormatError('LZ4 literal exceeds input or declared output bounds')
        result.extend(source[pos:pos + literal_count])
        pos += literal_count
        if pos == length:
            break
        if pos + 2 > length:
            raise FormatError('Truncated LZ4 match offset')
        offset = source[pos] | (source[pos + 1] << 8)
        pos += 2
        if offset == 0 or offset > len(result):
            raise FormatError('Invalid LZ4 match offset')
        match_count = extended(token & 15) + 4
        if len(result) + match_count > expected:
            raise FormatError('LZ4 match exceeds declared output size')
        seed = bytes(result[-offset:])
        result.extend((seed * ((match_count + len(seed) - 1) // len(seed)))[:match_count])
    if len(result) != expected:
        raise FormatError(f'LZ4 output length {len(result)} differs from declared {expected}')
    return bytes(result)


def codec_of(prefix: bytes) -> str:
    if prefix.startswith(b'\x28\xb5\x2f\xfd'):
        return 'zstd'
    if len(prefix) >= 2 and prefix[0] in (0x8c, 0xcc) and prefix[1] == 0x0c:
        return 'oodle'
    return 'lz4'


def qts_path_id(text: str, add_slash: bool = False) -> int:
    """Compute QtsVFS's observed case-insensitive UTF-16 path identifier.

    Public algorithm reference is QtsVFSFile.Compute at the pinned source
    linked in this module. ASCII and Chinese paths avoid differences between
    Python lower() and .NET ToLowerInvariant for unusual Unicode characters.
    """
    text = ('/' if add_slash else '') + text.lower()
    raw = text.encode('utf-16-le', errors='surrogatepass')
    units = struct.unpack('<' + 'H' * (len(raw) // 2), raw)
    forward, backward = 0x5BD1E995, 0xAB9423A7
    for left, right in zip(units, reversed(units)):
        forward = ((forward * 33) ^ left) & 0xffffffff
        backward = ((backward * 33) ^ right) & 0xffffffff
    return (backward << 32) | forward


def protobuf_varint(data: bytes, position: int):
    value = 0
    for number in range(10):
        if position >= len(data):
            raise FormatError('Truncated protobuf varint')
        byte = data[position]
        position += 1
        if number == 9 and byte > 1:
            raise FormatError('Protobuf varint exceeds 64 bits')
        value |= (byte & 127) << (7 * number)
        if byte < 128:
            return value, position
    raise FormatError('Oversized protobuf varint')


def protobuf_fields(data: bytes):
    """Read bounded wire fields without inventing a protobuf message schema."""
    position = 0
    result = []
    while position < len(data):
        tag, position = protobuf_varint(data, position)
        field, wire = tag >> 3, tag & 7
        if not 0 < field <= 536870911:
            raise FormatError('Invalid protobuf field number')
        if wire == 0:
            value, position = protobuf_varint(data, position)
        elif wire in (1, 2, 5):
            if wire == 2:
                size, position = protobuf_varint(data, position)
            else:
                size = 8 if wire == 1 else 4
            if position + size > len(data):
                raise FormatError('Protobuf field exceeds input bounds')
            value = data[position:position + size]
            position += size
        else:
            raise FormatError(f'Unsupported protobuf wire type {wire}')
        result.append((field, wire, value))
    return result


class QtsGlobalCacheIndex:
    """Read the observed native GlobalIndexPrime.data compact lookup cache.

    This format is distinct from the Oodle-compressed QING source record.
    Lookup keys are the low 32 bits of resource IDs; 0xffff package ordinals
    require the source QING's full-64-bit collision exception table.
    """
    def __init__(self, path: pathlib.Path):
        self.path = path.resolve(strict=True)
        data = self.path.read_bytes()
        if len(data) < 56 or data[:4] != b'INGQ':
            raise FormatError('Not an observed native QING lookup cache')
        self.version = struct.unpack_from('<I', data, 4)[0]
        self.package_count, self.slot_count = struct.unpack_from('<II', data, 32)
        if not 0 < self.slot_count < 10_000_000 or len(data) != 56 + 6 * self.slot_count:
            raise FormatError('QING compact cache size does not match declared slot count')
        self.keys = struct.unpack_from('<' + str(self.slot_count) + 'I', data, 56)
        self.ordinals = struct.unpack_from('<' + str(self.slot_count) + 'H', data, 56 + 4 * self.slot_count)
        if any(self.keys[i] >= self.keys[i+1] for i in range(self.slot_count-1)):
            raise FormatError('QING compact cache keys are not strictly sorted')
        if any(value >= self.package_count and value != 0xffff for value in self.ordinals):
            raise FormatError('QING compact cache has invalid package ordinals')

    def lookup_package_ordinals(self, identifier: int, collision_map: dict[int, int] | None = None):
        low = identifier & 0xffffffff
        position = bisect.bisect_left(self.keys, low)
        if position == self.slot_count or self.keys[position] != low:
            return []
        ordinal = self.ordinals[position]
        if ordinal == 0xffff:
            if collision_map is None:
                raise FormatError('Full-64-bit QING collision table required for this ID')
            if identifier not in collision_map:
                return []
            ordinal = collision_map[identifier]
            if not 0 <= ordinal < self.package_count:
                raise FormatError('Collision exception package ordinal is invalid')
        return [ordinal]


class QtsContainer:
    def __init__(self, path: pathlib.Path, *, raw_values: bool = False):
        self.path = path.resolve(strict=True)
        self.handle = self.path.open('rb')
        self.data = mmap.mmap(self.handle.fileno(), 0, access=mmap.ACCESS_READ)
        self.entries: dict[int, list[Chunk]] = collections.defaultdict(list)
        self.invalid_record_count = 0
        self.header = {}
        self.raw_values = raw_values
        self._index()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.data.close()
        self.handle.close()

    def unpack(self, fmt: str, offset: int):
        length = struct.calcsize(fmt)
        if offset < 0 or offset + length > len(self.data):
            raise FormatError(f'Out of bounds metadata read at 0x{offset:x}')
        return struct.unpack_from(fmt, self.data, offset)

    def tables(self, offset: int):
        if offset == -1:
            return []
        maximum, count = self.unpack('<ii', offset)
        if not 0 <= count <= 1_000_000 or offset + 24 + 24 * count > len(self.data):
            raise FormatError('Invalid QtsVFS table count')
        return [self.unpack('<6i', offset + 24 + 24 * i) for i in range(count)]

    def _index(self):
        magic, used, allocated = self.unpack('<Qii', 0)
        if magic != 0x0403020102000001:
            raise FormatError('Not the observed ACE QtsVFS header')
        names = ['unknown0', 'unknown1', 'index', 'index_data', 'entries1', 'entries2', 'entries3', 'unknown2']
        offsets = {name: self.unpack('<ii', 16 + 8 * i) for i, name in enumerate(names)}
        self.header = {'magic': f'{magic:016x}', 'used_size': used, 'allocated_size': allocated, 'actual_size': len(self.data), 'offset_size_pairs': offsets}
        tables = self.tables(offsets['entries2'][0]) + self.tables(offsets['entries3'][0])
        block_offsets = {v for row in tables for v in row[:3] if v >= 0}
        record_offsets = set()
        for block in block_offsets:
            own, unknown, next_block, count, block_type = self.unpack('<iiiHH', block + 4080)
            if block_type == 2:
                continue
            if not 0 <= count <= 510:
                raise FormatError('QtsVFS block entry count exceeds the offset array')
            record_offsets.update(self.unpack('<' + 'i' * count, block + 2040))
        pending = sorted(v for v in record_offsets if v >= 0)
        visited = set()
        while pending:
            offset = pending.pop()
            if offset in visited:
                continue
            visited.add(offset)
            own, next_offset = self.unpack('<ii', offset)
            if own != offset:
                self.invalid_record_count += 1
                continue
            if self.raw_values:
                value_size = self.unpack('<I', offset + 24)[0]
                payload = offset + 28
                if payload + value_size > len(self.data):
                    raise FormatError('Generic Qts record exceeds file bounds')
                trailer = (payload + value_size + 3) & ~3
                identifier = self.unpack('<Q', trailer)[0]
                self.entries[identifier].append(Chunk(offset, payload, value_size, value_size, 0, 0, 'raw'))
                if next_offset != -1 and next_offset not in visited:
                    pending.append(next_offset)
                continue
            stored_compressed_size, uncompressed_size = self.unpack('<ii', offset + 24)
            compressed_size = stored_compressed_size - 4
            payload = offset + 32
            if not 0 <= compressed_size <= len(self.data) or not 0 <= uncompressed_size <= 256 * 1024 * 1024:
                raise FormatError(f'Unsupported chunk at 0x{offset:x}: compressed={compressed_size}, uncompressed={uncompressed_size}')
            trailer = (payload + compressed_size + 3) & ~3
            identifier, main, sub = self.unpack('<Qii', trailer)
            if payload + compressed_size > len(self.data):
                raise FormatError('QtsVFS chunk exceeds file bounds')
            chunk = Chunk(offset, payload, compressed_size, uncompressed_size, main, sub, codec_of(self.data[payload:payload+4]))
            self.entries[identifier].append(chunk)
            if next_offset != -1 and next_offset not in visited:
                pending.append(next_offset)
        for chunks in self.entries.values():
            chunks.sort(key=lambda c: (c.main_block, c.sub_block))

    def decode_entry(self, identifier: int, maximum: int = 64 * 1024 * 1024):
        chunks = self.entries[identifier]
        total = sum(c.uncompressed_size for c in chunks)
        if total > maximum:
            raise FormatError(f'Entry requires {total} bytes, above inspection bound {maximum}')
        decoded = []
        for chunk in chunks:
            if chunk.codec != 'lz4':
                raise FormatError(f'Codec {chunk.codec} not implemented by dependency-free inspector')
            payload = self.data[chunk.payload_offset:chunk.payload_offset+chunk.compressed_size]
            decoded.append(lz4_block_decode(payload, chunk.uncompressed_size))
        return b''.join(decoded)

    def raw_record_values(self, identifier: int):
        """Read generic Qts record values without assuming compressed payloads.

        Some nested Qts resources hold Unity type trees directly. The first
        four value bytes are then a node count, rather than decoded size.
        """
        values = []
        for chunk in self.entries[identifier]:
            value_size = self.unpack('<I', chunk.record_offset + 24)[0]
            begin = chunk.record_offset + 28
            if begin + value_size > len(self.data):
                raise FormatError('Raw record value exceeds source bounds')
            values.append(bytes(self.data[begin:begin+value_size]))
        return values

    def entry_summary(self, identifier: int):
        chunks = self.entries[identifier]
        return {'id': str(identifier), 'id_hex': f'{identifier:016x}', 'chunk_count': len(chunks), 'compressed_bytes': sum(c.compressed_size for c in chunks), 'decoded_bytes': sum(c.uncompressed_size for c in chunks), 'codecs': sorted({c.codec for c in chunks}), 'chunks': [asdict(c) for c in chunks]}


def classify(content: bytes):
    result = {'length': len(content), 'sha256': hashlib.sha256(content).hexdigest(), 'prefix_hex': content[:32].hex()}
    if content.startswith(b'UnityFS'):
        result['kind'] = 'UnityFS'
    elif len(content) >= 48 and struct.unpack_from('>I', content, 8)[0] == 22 and struct.unpack_from('>Q', content, 24)[0] == len(content):
        result['kind'] = 'UnitySerializedFile_v22'
        end = content.find(b'\0', 48)
        result['engine'] = content[48:end].decode('ascii', errors='replace')
    elif content.startswith(b'\x1bLua'):
        result['kind'] = 'Lua_bytecode'
    else:
        try:
            text = content.decode('utf-8-sig')
            printable = sum(c.isprintable() or c in '\r\n\t' for c in text) / max(len(text), 1)
            result['utf8_printable_ratio'] = round(printable, 3)
            if printable > .96:
                result['kind'] = 'Lua_text' if re.search(r'\b(function|return|local)\b', text) else 'utf8_text'
                result['text_preview'] = text[:320]
                result['chinese_character_count'] = len(re.findall(r'[\u4e00-\u9fff]', text))
            else:
                result['kind'] = 'binary'
        except UnicodeDecodeError:
            result['kind'] = 'binary'
        result['QTSF_PACKAGE_offset'] = content.find(b'QTSF_PACKAGE')
        result['ascii_tokens_first_15'] = [x.decode('ascii') for x in re.findall(rb'[A-Za-z_./][A-Za-z0-9_./ -]{5,}', content)[:15]]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=pathlib.Path, required=True)
    parser.add_argument('--output', type=pathlib.Path, help='Write index JSON to an explicit analysis output path')
    parser.add_argument('--sample', type=int, default=0, help='Decode the smallest N entries up to 64 MiB each')
    parser.add_argument('--entry', help='Decode this decimal/0xhex identifier instead of sampling')
    parser.add_argument('--extract-dir', type=pathlib.Path, help='Explicit independent output directory for selected decoded entries')
    args = parser.parse_args()
    with QtsContainer(args.db) as container:
        ordered = sorted(container.entries, key=lambda i: sum(c.uncompressed_size for c in container.entries[i]))
        selected = [int(args.entry, 0)] if args.entry else ordered[:args.sample]
        result = {'source_db': str(container.path), 'header': container.header, 'entry_count': len(container.entries), 'chunk_count': sum(map(len, container.entries.values())), 'invalid_record_count': container.invalid_record_count, 'entries': [container.entry_summary(i) for i in sorted(container.entries)], 'decoded_samples': []}
        codec_counts = collections.Counter(c.codec for chunks in container.entries.values() for c in chunks)
        result['codec_chunk_counts'] = dict(codec_counts)
        if args.extract_dir:
            extract_root = args.extract_dir.resolve()
            if extract_root == container.path.parent or container.path.is_relative_to(extract_root):
                raise FormatError('Extraction output must be separate from source container directory')
            extract_root.mkdir(parents=True, exist_ok=True)
        for identifier in selected:
            item = {'id': str(identifier), 'id_hex': f'{identifier:016x}'}
            try:
                decoded = container.decode_entry(identifier)
                item.update(classify(decoded))
                if args.extract_dir:
                    destination = extract_root / f'{identifier:016x}.bin'
                    destination.write_bytes(decoded)
                    item['extracted_path'] = str(destination)
            except (FormatError, KeyError, struct.error) as exc:
                item['error'] = str(exc)
            result['decoded_samples'].append(item)
        if args.output:
            target = args.output.resolve()
            if target == container.path:
                raise FormatError('Analysis output cannot overwrite source container')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        compact = {k:v for k,v in result.items() if k != 'entries'}
        print(json.dumps(compact, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
