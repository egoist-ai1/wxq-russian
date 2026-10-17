"""Check captured native SFNT cmaps without loading game DLLs or font fallbacks."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import unicodedata

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
MAIN_FONT_IDS = ("c236135428ec9686", "2cd6e0b2794705e0")
RUNTIME = re.compile(
    r"\{[^{}\r\n]*\}|</?[A-Za-z][^<>]*>"
    r"|%(?:\d+\$)?[-+ #0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[diuoxXfFeEgGaAcCsSpn%]"
    r"|\[(?:[0-9A-Fa-f]{6,8}|/?[biu]|-)\]"
    r"|\[[0-9A-Za-z_$().+-][0-9A-Za-z_$().+*/%?:=<>|& \t-]*\]"
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def project_path(path: str | Path, project: Path = PROJECT) -> Path:
    result = (project / path).resolve()
    if not result.is_relative_to(project.resolve()):
        raise ValueError(f"path outside project: {path}")
    return result


def read_slice(data: bytes, offset: int, size: int) -> bytes:
    if offset < 0 or size < 0 or offset + size > len(data):
        raise ValueError(f"binary range outside file: {offset}+{size}/{len(data)}")
    return data[offset:offset + size]


def u16(data: bytes, offset: int) -> int:
    return struct.unpack(">H", read_slice(data, offset, 2))[0]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack(">I", read_slice(data, offset, 4))[0]


def cmap4(data: bytes) -> set[int]:
    length = u16(data, 2)
    data = read_slice(data, 0, length)
    n = u16(data, 6) // 2
    if not n or u16(data, 6) % 2:
        raise ValueError("invalid cmap4 segment count")
    end_at, start_at, delta_at, range_at = 14, 16 + 2 * n, 16 + 4 * n, 16 + 6 * n
    read_slice(data, range_at, 2 * n)
    result = set()
    previous_end = -1
    for index in range(n):
        start, end = u16(data, start_at + 2 * index), u16(data, end_at + 2 * index)
        delta, range_offset = u16(data, delta_at + 2 * index), u16(data, range_at + 2 * index)
        if start > end or end <= previous_end:
            raise ValueError("invalid cmap4 segment order")
        previous_end = end
        for codepoint in range(start, end + 1):
            if codepoint == 0xFFFF:
                continue
            if range_offset:
                address = range_at + 2 * index + range_offset + 2 * (codepoint - start)
                glyph = u16(data, address)
                glyph = (glyph + delta) & 0xFFFF if glyph else 0
            else:
                glyph = (codepoint + delta) & 0xFFFF
            if glyph and not 0xD800 <= codepoint <= 0xDFFF:
                result.add(codepoint)
    return result


def cmap12(data: bytes) -> set[int]:
    length = u32(data, 4)
    data = read_slice(data, 0, length)
    n = u32(data, 12)
    read_slice(data, 16, 12 * n)
    result = set()
    previous_end = -1
    for index in range(n):
        start, end, glyph = struct.unpack(">III", read_slice(data, 16 + 12 * index, 12))
        if start > end or end > 0x10FFFF or start <= previous_end:
            raise ValueError("invalid cmap12 group range/order")
        previous_end = end
        for codepoint in range(start, end + 1):
            if glyph + codepoint - start and not 0xD800 <= codepoint <= 0xDFFF:
                result.add(codepoint)
    return result


def sfnt_cmap(data: bytes) -> tuple[set[int], list[dict]]:
    if read_slice(data, 0, 4) not in (b"OTTO", b"\0\1\0\0", b"true"):
        raise ValueError("unsupported SFNT signature")
    count = u16(data, 4)
    read_slice(data, 12, 16 * count)
    directory = {}
    for index in range(count):
        record = read_slice(data, 12 + 16 * index, 16)
        tag = record[:4]
        offset, length = struct.unpack(">II", record[8:])
        if tag in directory:
            raise ValueError("duplicate SFNT table")
        directory[tag] = read_slice(data, offset, length)
    if b"cmap" not in directory:
        raise ValueError("SFNT has no cmap")
    cmap = directory[b"cmap"]
    if u16(cmap, 0) != 0:
        raise ValueError("unsupported cmap version")
    n = u16(cmap, 2)
    read_slice(cmap, 4, 8 * n)
    codepoints, evidence = set(), []
    for index in range(n):
        platform, encoding, offset = struct.unpack(">HHI", read_slice(cmap, 4 + 8 * index, 8))
        format_ = u16(cmap, offset)
        unicode_record = platform == 0 or (platform == 3 and encoding in (1, 10))
        record = {"platform": platform, "encoding": encoding, "format": format_, "offset_in_cmap": offset}
        if unicode_record:
            if format_ == 4:
                current = cmap4(cmap[offset:])
            elif format_ == 12:
                current = cmap12(cmap[offset:])
            else:
                raise ValueError(f"unhandled Unicode cmap format {format_}")
            codepoints.update(current)
            record["mapped_nonzero_glyph_codepoints"] = len(current)
        record["used"] = unicode_record
        evidence.append(record)
    if not codepoints:
        raise ValueError("font has no supported nonempty Unicode cmap")
    return codepoints, evidence


def load_main_fonts(project: Path = PROJECT) -> tuple[list[dict], dict[str, str]]:
    descriptor_path = "source/game/descriptors/native-font-assets.json"
    descriptor_bytes = project_path(descriptor_path, project).read_bytes()
    descriptor = json.loads(descriptor_bytes)
    pins = {descriptor_path: sha256(descriptor_bytes)}
    fonts = []
    for resource_id in MAIN_FONT_IDS:
        matches = [a for a in descriptor["selected_source_assets"] if a["entry_id_hex"] == resource_id]
        if len(matches) != 1:
            raise ValueError(f"main font descriptor ambiguity: {resource_id}")
        asset = matches[0]
        data = project_path(asset["project_asset"], project).read_bytes()
        if len(data) != asset["decoded_bytes"] or sha256(data) != asset["decoded_sha256"]:
            raise ValueError(f"captured font asset changed: {resource_id}")
        pins[asset["project_asset"]] = sha256(data)
        objects = [obj for obj in asset["objects"] if obj.get("font")]
        if len(objects) != 1:
            raise ValueError(f"main font object ambiguity: {resource_id}")
        obj = objects[0]
        raw_object = read_slice(data, obj["byte_offset"], obj["byte_size"])
        if sha256(raw_object) != obj["object_sha256"]:
            raise ValueError(f"captured font object changed: {resource_id}")
        font_data = obj["fields"]["m_FontData"]
        sfnt = read_slice(raw_object, font_data["byte_offset_in_object"], font_data["bytes"])
        if sha256(sfnt) != font_data["sha256"] or sfnt[:4].hex() != font_data["signature_hex"]:
            raise ValueError(f"captured embedded SFNT changed: {resource_id}")
        coverage, records = sfnt_cmap(sfnt)
        fonts.append({"resource_id_hex": resource_id, "name": obj["name"], "project_asset": asset["project_asset"],
                      "asset_sha256": sha256(data), "object_path_id": obj["path_id"],
                      "font_absolute_byte_offset": obj["byte_offset"] + font_data["byte_offset_in_object"],
                      "font_bytes": len(sfnt), "font_sha256": sha256(sfnt),
                      "cmap_records": records, "codepoints": coverage})
    return fonts, pins


def native_icon(char: str) -> bool:
    return 0x2500 <= ord(char) <= 0x257F or 0xE000 <= ord(char) <= 0xF8FF


def visible_text(source: str, target: str) -> tuple[str, Counter]:
    """Remove exact preserved runtime tokens and source-owned icon markers only."""
    source_tokens = Counter(RUNTIME.findall(source))
    excluded = Counter()

    def remove_token(match: re.Match) -> str:
        token = match.group()
        if source_tokens[token]:
            source_tokens[token] -= 1
            excluded["exact_source_runtime_token"] += 1
            return ""
        return token

    visible = RUNTIME.sub(remove_token, target)
    source_icons = Counter(ch for ch in source if native_icon(ch))
    source_controls = Counter(ch for ch in source if unicodedata.category(ch) in ("Cc", "Cf"))
    filtered = []
    for char in visible:
        if native_icon(char) and source_icons[char]:
            source_icons[char] -= 1
            excluded[f"source_native_icon_U+{ord(char):04X}"] += 1
        elif unicodedata.category(char) in ("Cc", "Cf") and source_controls[char]:
            source_controls[char] -= 1
            excluded[f"source_control_U+{ord(char):04X}"] += 1
        else:
            filtered.append(char)
    return "".join(filtered), excluded


def remove_verified_protocol_spans(source: str, target: str, proof: dict) -> tuple[str, str, int]:
    if sha256(source.encode("utf-8")) != proof["source_sha256"] or sha256(target.encode("utf-8")) != proof["target_sha256"]:
        raise ValueError("protocol span source/target SHA changed")
    aliases = []
    masked = []
    for side, text in (("source", source), ("target", target)):
        result, end, side_aliases = [], 0, []
        for span in proof[side + "_spans"]:
            start, stop, alias = span["start"], span["end"], span["text"]
            if not 0 <= end <= start < stop <= len(text) or text[start:stop] != alias:
                raise ValueError("protocol span is stale, overlapping or out of bounds")
            result.extend((text[end:start], " " * (stop - start)))
            side_aliases.append(alias)
            end = stop
        result.append(text[end:])
        masked.append("".join(result))
        aliases.append(side_aliases)
    if not aliases[0] or aliases[0] != aliases[1]:
        raise ValueError("protocol source/target alias span sequence differs")
    return masked[0], masked[1], len(aliases[0])


def coverage_report(samples: list[dict], fonts: list[dict],
                    source_protocol_aliases: dict[str, str] | None = None,
                    source_protocol_spans: dict[str, dict] | None = None) -> dict:
    required, added, examples, excluded = Counter(), Counter(), defaultdict(list), Counter()
    protocol_proofs = source_protocol_aliases or {}
    span_proofs = source_protocol_spans or {}
    verified_protocol_ids = set()
    verified_span_ids = set()
    if set(protocol_proofs) & set(span_proofs):
        raise ValueError("protocol whole-alias and span exclusions overlap")
    for sample in samples:
        source, target = sample["source"], sample["target"]
        if sample["id"] in protocol_proofs:
            if source != target or sha256(source.encode("utf-8")) != protocol_proofs[sample["id"]]:
                raise ValueError("nonvisible protocol alias must match exact source bytes and bound source SHA256")
            if sample["id"] in verified_protocol_ids:
                raise ValueError("duplicate nonvisible protocol sample identity")
            verified_protocol_ids.add(sample["id"])
            excluded["source_exact_nonvisible_protocol_alias"] += 1
            continue
        if sample["id"] in span_proofs:
            source, target, count = remove_verified_protocol_spans(source, target, span_proofs[sample["id"]])
            if sample["id"] in verified_span_ids:
                raise ValueError("duplicate protocol span sample identity")
            verified_span_ids.add(sample["id"])
            excluded["exact_source_native_protocol_alias_span"] += count
        visible, masked = visible_text(source, target)
        source_visible, _ = visible_text(source, source)
        source_chars = set(source_visible)
        excluded.update(masked)
        for char in visible:
            required[ord(char)] += 1
            if char not in source_chars:
                added[ord(char)] += 1
            if sample["id"] not in examples[ord(char)] and len(examples[ord(char)]) < 8:
                examples[ord(char)].append(sample["id"])
    if verified_protocol_ids != set(protocol_proofs):
        raise ValueError("protocol font exclusion proof has no matching sample")
    if verified_span_ids != set(span_proofs):
        raise ValueError("protocol span font exclusion proof has no matching sample")
    reports = []
    for font in fonts:
        missing_all = sorted(set(required) - font["codepoints"])
        missing_new = sorted(set(added) - font["codepoints"])
        describe = lambda cp: {"character": chr(cp), "codepoint": f"U+{cp:04X}", "occurrences": required[cp],
                               "introduced_occurrences": added[cp], "example_ids": examples[cp]}
        reports.append({**{k: v for k, v in font.items() if k != "codepoints"},
                        "cmap_nonzero_glyph_codepoint_count": len(font["codepoints"]),
                        "all_66_Russian_letters_present": all(ord(ch) in font["codepoints"] for ch in
                                                             "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя"),
                        "missing_visible_codepoints": [describe(cp) for cp in missing_all],
                        "missing_introduced_visible_codepoints": [describe(cp) for cp in missing_new],
                        "introduced_glyphs_pass": not missing_new})
    return {"schema_version": 1, "sample_count": len(samples), "required_visible_codepoints": len(required),
            "introduced_visible_codepoints": len(added), "source_preserved_exclusions": dict(sorted(excluded.items())),
            "source_exact_nonvisible_protocol_alias_count": len(verified_protocol_ids),
            "source_exact_nonvisible_protocol_alias_ids": sorted(verified_protocol_ids),
            "source_native_protocol_span_sample_count": len(verified_span_ids),
            "source_native_protocol_span_sample_ids": sorted(verified_span_ids),
            "fonts": reports, "introduced_glyphs_pass": all(f["introduced_glyphs_pass"] for f in reports),
            "limits": ["Each main font is checked independently; fallback is not assumed.",
                       "Only mapped nonzero glyph IDs are evidence; atlas availability, font metrics, wrapping and clipping need runtime QA.",
                       "Exact source runtime tokens, unchanged native icon markers and source controls are listed separately; their rendering is not proved by SFNT cmap.",
                       "Whole protocol samples are excluded only with finite source-SHA proofs, byte-exact source targets and no unresolved visible context.",
                       "Compound protocol masks bind full source/target SHA and exact matching alias spans; ordinary prose remains in the font requirements."]}


def self_test() -> dict:
    # cmap4 exercises both delta segments and idRangeOffset relative addressing.
    n = 3
    data = struct.pack(">7H", 4, 44, 0, 2 * n, 4, 1, 2)
    data += struct.pack(">3HH3H3H3H2H", 0x42, 0x44, 0xFFFF, 0, 0x41, 0x43, 0xFFFF,
                        (1 - 0x41) & 0xFFFF, 1, 1, 0, 4, 0, 2, 0)
    assert cmap4(data) == {0x41, 0x42, 0x43}
    data12 = struct.pack(">HHIIIIII", 12, 0, 28, 0, 1, 0x10000, 0x10002, 0)
    assert cmap12(data12) == {0x10001, 0x10002}
    rejected = 0
    for broken in (data[:18], data12[:-1]):
        try:
            (cmap4 if broken[:2] == b"\0\4" else cmap12)(broken)
        except ValueError:
            rejected += 1
    assert rejected == 2
    visible, excluded = visible_text("╬{推演高级战术:-20/-10}\v[194030p1q1]", "╬{推演高级战术:-20/-10}\v[194030p1q1]Эволюция")
    assert visible == "Эволюция" and excluded["exact_source_runtime_token"] == 2
    assert visible_text("A", "A╬\v")[0] == "A╬\v"
    alias = {"id": "fixture-alias", "source": "[月亮]", "target": "[月亮]"}
    proof = {alias["id"]: sha256(alias["source"].encode("utf-8"))}
    protocol_report = coverage_report([alias], [{"name": "fixture", "codepoints": set()}], proof)
    assert protocol_report["required_visible_codepoints"] == 0 and protocol_report["source_exact_nonvisible_protocol_alias_count"] == 1
    for sample, bad_proof in (({**alias, "target": "[Луна]"}, proof), (alias, {alias["id"]: "0" * 64})):
        try:
            coverage_report([sample], [{"name": "fixture", "codepoints": set()}], bad_proof)
        except ValueError:
            pass
        else:
            raise AssertionError("changed protocol alias or stale SHA accepted for font masking")
    source, target = "[月亮]说明", "[月亮]Текст"
    spans = {"source_sha256": sha256(source.encode()), "target_sha256": sha256(target.encode()),
             "source_spans": [{"start": 0, "end": 4, "text": "[月亮]"}],
             "target_spans": [{"start": 0, "end": 4, "text": "[月亮]"}]}
    assert remove_verified_protocol_spans(source, target, spans) == ("    说明", "    Текст", 1)
    try:
        remove_verified_protocol_spans(source, target, {**spans, "target_spans": [{"start": 1, "end": 4, "text": "[月亮]"}]})
    except ValueError:
        pass
    else:
        raise AssertionError("incorrect protocol span position accepted")
    return {"passed": True, "checks": 11, "scope": "cmap4 delta/range-offset/glyph0; cmap12 supplementary/glyph0; truncated rejection; exact source masking/no introduced masking; finite source-exact protocol masking and changed-target/stale-SHA rejection; source/target-SHA-bound compound span mask and invalid position rejection"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, help="Project JSON list of {id,source,target}")
    parser.add_argument("--output", type=Path, help="Project JSON report path")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False))
    if args.samples:
        fonts, pins = load_main_fonts()
        report = coverage_report(json.loads(project_path(args.samples).read_bytes()), fonts)
        report["input_sha256"] = pins
        if args.output:
            path = project_path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"samples": report["sample_count"], "introduced_glyphs_pass": report["introduced_glyphs_pass"],
                          "fonts": [{"name": f["name"], "missing_new": f["missing_introduced_visible_codepoints"]} for f in report["fonts"]]}, ensure_ascii=False))
        return 0 if report["introduced_glyphs_pass"] else 2
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
