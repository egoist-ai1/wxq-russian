"""Дельта между сборками игры и перенос памяти переводов поверх core.reconcile.

Правило: перевод применяется только при точном совпадении идентичности записи и
SHA исходника (это делает core.reconcile). Всё остальное остаётся оригиналом и
попадает в очередь ревью. Текстовая память (тот же CN -> ранее утверждённый RU)
служит подсказкой в очереди и никогда не применяется автоматически.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from . import latin_allow
from .core import IDENTITY_FIELDS, digest, identity, reconcile, text_check

WILDCARD = "*"  # consumer/variant, когда потребитель не доказан
SCHEMA_DELTA = "wxq-delta/v1"
SCHEMA_RESULT = "wxq-ru-engine-result/v1"


def ident(row: dict) -> tuple:
    return tuple(row[k] for k in IDENTITY_FIELDS)


def _where(item) -> dict:
    """Кто именно отброшен: по одному индексу в сотнях тысяч строк запись не найти."""
    if not isinstance(item, dict):
        return {}
    return {k: item[k] for k in ("namespace", "resource", "key") if isinstance(item.get(k), str)}


def normalize_entries(items: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """Универсальный вход extract -> записи core (source, formatter). Плохие уходят в rejects."""
    entries: list[dict] = []
    rejects: list[dict] = []
    for index, item in enumerate(items):
        try:
            if not isinstance(item, dict):
                raise ValueError("entry-not-object")
            entry = {"namespace": item.get("namespace"), "resource": item.get("resource"), "key": item.get("key"),
                     "consumer": item.get("consumer", WILDCARD), "variant": item.get("variant", WILDCARD),
                     "source": text_check(item.get("source_text", item.get("source"))),
                     "formatter": item.get("formatter", {})}
            if not isinstance(entry["formatter"], dict):
                raise ValueError("formatter-contract-required")
            identity(entry)
            entry["source_sha256"] = digest(entry["source"])
            entries.append(entry)
        except (ValueError, TypeError) as error:
            rejects.append({"index": index, "error": str(error), **_where(item)})
    return entries, rejects


def _valid_memory(rows: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    good: list[dict] = []
    bad: list[dict] = []
    for index, row in enumerate(rows):
        try:
            identity(row)
            text_check(row.get("source"))
            if not isinstance(row.get("formatter"), dict):
                raise ValueError("formatter-contract-required")
            good.append(row)
        except (ValueError, TypeError, AttributeError) as error:
            bad.append({"index": index, "error": str(error), **_where(row)})
    return good, bad


def load_renames(items: Iterable[dict]) -> dict[tuple, tuple]:
    """Явный mapping переименований: [{"from": {...identity}, "to": {...identity}}]."""
    result: dict[tuple, tuple] = {}
    for item in items:
        sides = []
        for side in (dict(item["from"]), dict(item["to"])):
            side.setdefault("consumer", WILDCARD)
            side.setdefault("variant", WILDCARD)
            sides.append(ident(side))
        result[sides[0]] = sides[1]
    return result


def apply_renames(rows: Iterable[dict], renames: dict[tuple, tuple]) -> list[dict]:
    out = []
    for row in rows:
        target = renames.get(ident(row))
        out.append({**row, **dict(zip(IDENTITY_FIELDS, target))} if target else row)
    return out


def diff(old: Iterable[dict], new: Iterable[dict]) -> dict:
    """new/changed/removed/unchanged по идентичности и SHA исходника."""
    def index(rows):
        result = defaultdict(list)
        for row in rows:
            result[ident(row)].append(digest(row["source"]))
        return {k: sorted(v) for k, v in result.items()}
    before, after = index(old), index(new)
    out = {"new": [], "changed": [], "removed": [], "unchanged": []}
    for key in sorted(set(before) | set(after)):
        record = dict(zip(IDENTITY_FIELDS, key))
        if key not in before:
            out["new"].append(record)
        elif key not in after:
            out["removed"].append(record)
        elif before[key] == after[key]:
            out["unchanged"].append(record)
        else:
            out["changed"].append(record)
    return {"schema": SCHEMA_DELTA, "counts": {k: len(v) for k, v in out.items()}, **out}


def text_hints(memory: Iterable[dict]) -> dict[str, list[dict]]:
    """Текстовая TM: SHA исходника -> утверждённые переводы из любых контекстов (только подсказка)."""
    hints: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in memory:
        if row.get("review") == "approved" and isinstance(row.get("target"), str):
            origin = "/".join((row["namespace"], row["resource"], row["key"]))
            hints[digest(row["source"])].setdefault(row["target"], {"target": row["target"], "origin": "text-tm", "from": origin})
    return {k: sorted(v.values(), key=lambda h: (h["target"], h["from"])) for k, v in hints.items()}


def run(current_items: Iterable[dict], memory_rows: Iterable[dict], *, renames: dict | None = None,
        semantic_proofs: set | None = None, engine_latin: Iterable[str] = latin_allow.BUILTIN) -> dict:
    """Записи сборки + память -> carrier и очередь ревью. Память не изменяется."""
    entries, rejects = normalize_entries(current_items)
    memory, bad_memory = _valid_memory(memory_rows)
    if renames:
        memory = apply_renames(memory, renames)
    result = reconcile(entries, memory, semantic_proofs, engine_latin)
    versions: dict[tuple, list[dict]] = defaultdict(list)
    for row in memory:
        versions[ident(row)].append(row)
    sources = {(ident(e), e["source_sha256"]): e["source"] for e in entries}
    hints = text_hints(memory)
    carrier: list[dict] = []
    queue: list[dict] = []
    for rec in result["records"]:
        key = ident(rec)
        if rec["status"] == "translated-exact":
            carrier.append({**{k: rec[k] for k in IDENTITY_FIELDS}, "source_sha256": rec["source_sha256"], "text": rec["text"]})
            continue
        known = versions.get(key, [])
        if rec["status"] == "source-new":
            reason = "new"
        elif rec["status"] == "source-changed-or-unreviewed":
            reason = "changed" if any(digest(m["source"]) != rec["source_sha256"] for m in known) else "unreviewed"
        elif rec["status"] == "source-ambiguous":
            reason = "ambiguous"
        elif rec["status"] == "source-invalid-target":
            reason = "invalid-target"
        else:
            reason = "contract-review"
        item_hints = list(hints.get(rec["source_sha256"], ()))
        if reason == "changed":  # прежний перевод этой же записи — тоже только подсказка
            item_hints += [{"target": m["target"], "origin": "previous-version", "from": "same-identity"}
                           for m in sorted(known, key=lambda m: digest(m["source"]))
                           if m.get("review") == "approved" and isinstance(m.get("target"), str)]
        queue.append({**{k: rec[k] for k in IDENTITY_FIELDS}, "source_sha256": rec["source_sha256"],
                      "source_text": sources[(key, rec["source_sha256"])], "reason": reason,
                      "issues": rec["issues"], **({"issue_details": rec["issue_details"]} if "issue_details" in rec else {}),
                      "hints": item_hints, "hints_applied": False})
    queue.sort(key=lambda q: (ident(q), q["source_sha256"]))
    carrier.sort(key=ident)
    removed = [dict(zip(IDENTITY_FIELDS, k)) for k in sorted(set(versions) - {ident(e) for e in entries})]
    return {"schema": SCHEMA_RESULT, "carrier": carrier, "queue": queue, "removed": removed,
            "counts": {"carrier": len(carrier), "queue": len(queue), "removed": len(removed),
                       "rejected_entries": len(rejects), "rejected_memory": len(bad_memory)},
            "rejects": rejects, "memory_rejects": bad_memory, "memory_modified": False,
            "native_install_allowed": False}


def mark_removed(memory: Iterable[dict], current_items: Iterable[dict]) -> list[dict]:
    """Копия памяти: записи вне актуальной сборки получают removed=true (в carrier не попадают)."""
    entries, _ = normalize_entries(current_items)
    present = {ident(e) for e in entries}
    return [{**m, "removed": True} if ident(m) not in present else {k: v for k, v in m.items() if k != "removed"}
            for m in memory]
