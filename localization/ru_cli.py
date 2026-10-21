"""CLI движка русификации: extract / diff / reconcile / queue.

Запуск из корня проекта: python -X utf8 -B -m localization.ru_cli <команда> ...
Игру, лаунчер и SDK не трогает; существующие выходные файлы не перезаписывает.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import delta, latin_allow, tm_store

GLOSSARY = Path(__file__).resolve().parent.parent / "glossary" / "glossary.json"


def _read_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str, data) -> None:
    target = Path(path)
    if target.exists():
        raise SystemExit("output-exists: " + path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((json.dumps(data, sort_keys=True, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))


def _adapter_lang(path: str, resource: str, namespace: str) -> list[dict]:
    """PB .lang: (resource_id, hash_key). Читает только байты файла через game_lang_codec."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from game_lang_codec import LangDocument  # noqa: PLC0415
    doc = LangDocument.parse(Path(path).read_bytes())
    return [{"namespace": namespace, "resource": resource, "key": r.key, "source_text": r.text} for r in doc.rows]


def _adapter_global(path: str, resource: str, namespace: str) -> list[dict]:
    """Global string-map: (dd5b5edfbb69feed, human_key)."""
    from .global_map_codec import GlobalMapDocument  # noqa: PLC0415
    doc = GlobalMapDocument.parse(Path(path).read_bytes())
    return [{"namespace": namespace, "resource": resource, "key": r.key, "source_text": r.text} for r in doc.rows]


def cmd_extract(args) -> int:
    if args.format == "json":
        items = _read_json(args.input)
    elif args.format == "lang":
        if not args.resource:
            raise SystemExit("--resource обязателен для --format lang (resource_id)")
        items = _adapter_lang(args.input, args.resource, args.namespace or "pb-lang")
    else:
        items = _adapter_global(args.input, args.resource or "dd5b5edfbb69feed", args.namespace or "global")
    entries, rejects = delta.normalize_entries(items)
    out = [{**{k: e[k] for k in ("namespace", "resource", "key", "consumer", "variant")}, "source_text": e["source"],
            "formatter": e["formatter"], "source_sha256": e["source_sha256"]} for e in entries]
    out.sort(key=lambda e: (e["namespace"], e["resource"], e["key"], e["consumer"], e["variant"]))
    _write_json(args.output, {"schema": "wxq-extract/v1", "entries": out, "rejects": rejects})
    return 0


def _entries(path: str) -> list[dict]:
    data = _read_json(path)
    return data["entries"] if isinstance(data, dict) else data


def cmd_diff(args) -> int:
    old, _ = delta.normalize_entries(_entries(args.old))
    new, _ = delta.normalize_entries(_entries(args.new))
    if args.renames:
        old = delta.apply_renames(old, delta.load_renames(_read_json(args.renames)))
    _write_json(args.output, delta.diff(old, new))
    return 0


def _reconcile(args) -> dict:
    memory, tm_rejects = tm_store.load(Path(args.tm))
    renames = delta.load_renames(_read_json(args.renames)) if args.renames else None
    result = delta.run(_entries(args.current), memory, renames=renames,
                       engine_latin=latin_allow.default_allowlist(GLOSSARY))
    result["tm_rejects"] = tm_rejects
    return result


def cmd_reconcile(args) -> int:
    result = _reconcile(args)
    _write_json(args.output, result)
    if args.update_tm:  # единственная запись в память: переименования и пометка removed
        memory, _ = tm_store.load(Path(args.tm))
        if args.renames:
            memory = delta.apply_renames(memory, delta.load_renames(_read_json(args.renames)))
        tm_store.save(Path(args.tm), delta.mark_removed(memory, _entries(args.current)))
    return 0


def cmd_queue(args) -> int:
    queue = _reconcile(args)["queue"]
    _write_json(args.output, {"schema": "wxq-review-queue/v1", "count": len(queue), "queue": queue})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ru_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("extract")
    p.add_argument("--format", choices=("json", "lang", "global"), default="json")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--resource", default="")
    p.add_argument("--namespace", default="")
    p.set_defaults(func=cmd_extract)
    p = sub.add_parser("diff")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--output", required=True)
    p.add_argument("--renames")
    p.set_defaults(func=cmd_diff)
    for name, func in (("reconcile", cmd_reconcile), ("queue", cmd_queue)):
        p = sub.add_parser(name)
        p.add_argument("--current", required=True)
        p.add_argument("--tm", required=True)
        p.add_argument("--output", required=True)
        p.add_argument("--renames")
        if name == "reconcile":
            p.add_argument("--update-tm", action="store_true")
        p.set_defaults(func=func)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
