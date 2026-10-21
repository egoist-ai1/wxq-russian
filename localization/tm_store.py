"""Хранилище памяти переводов: tm/<namespace>/<resource>.jsonl, одна запись на строку.

Сортировка и сериализация детерминированы, поэтому git-дифф показывает только
реальные изменения. Модуль ничего не знает об игре и не применяет переводы.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from .core import IDENTITY_FIELDS, digest

TM_FORMAT = "wxq-tm/v1"


def _segment(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("invalid-path-segment")
    return quote(value, safe="-_").replace(".", "%2E")  # без "..", "/" и вложенности


def tm_path(root: Path, namespace: str, resource: str) -> Path:
    return Path(root) / _segment(namespace) / (_segment(resource) + ".jsonl")


def sort_key(row: dict) -> tuple:
    return (row["key"], row.get("consumer", ""), row.get("variant", ""),
            row.get("source_sha256") or digest(row.get("source", "")), row.get("target") or "")


def dumps_row(row: dict) -> str:
    return json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def with_sha(row: dict) -> dict:
    return {**row, "source_sha256": digest(row["source"])}


def load(root: Path) -> tuple[list[dict], list[dict]]:
    """Возвращает (строки, отброшенные). Битая строка не мешает остальным."""
    rows: list[dict] = []
    rejects: list[dict] = []
    base = Path(root)
    if not base.is_dir():
        return rows, rejects
    for path in sorted(base.glob("*/*.jsonl")):
        # не splitlines(): оно рвёт запись, в которой есть U+2028/U+0085
        for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item.get(k) for k in IDENTITY_FIELDS):
                    raise ValueError("invalid-tm-row")
                rows.append(item)
            except ValueError as error:
                rejects.append({"file": path.as_posix(), "line": number, "error": str(error)})
    return rows, rejects


def save(root: Path, rows: Iterable[dict]) -> list[str]:
    """Пишет память группами; файл переписывается только при изменении содержимого.

    Возвращает список изменённых файлов. Файлы групп, которых нет в rows, не удаляются.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        groups.setdefault((row["namespace"], row["resource"]), []).append(with_sha(row) if "source" in row else row)
    changed: list[str] = []
    for (namespace, resource), items in sorted(groups.items()):
        unique = {dumps_row(item): item for item in items}
        text = "".join(dumps_row(item) + "\n" for item in sorted(unique.values(), key=sort_key))
        path = tm_path(root, namespace, resource)
        if path.exists() and path.read_bytes() == text.encode("utf-8"):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))  # только LF, без BOM
        changed.append(path.as_posix())
    return changed
