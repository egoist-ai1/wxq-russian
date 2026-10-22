"""Сборка payload русификации лаунчера: profiles.json, launcher-ru.json, манифест и launcher-expected.txt.

Лаунчер только читается (хеши файлов и подпись exe); ничего в нём не меняется и не запускается.
Результат: build/launcher-ru-<дата>-<хеш>/ со всеми файлами для launcher-ru-runtime.cjs.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Callable

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_LAUNCHER_ROOT = Path(r"C:\Program Files\WXQLauncher")
CONTRACT = PROJECT / "scripts" / "launcher-normal-start-contract.json"

# Пути внутри каталога сборки лаунчера (read-only).
EXE_REL = "王者万象棋.exe"
APP_ASAR_REL = "resources/app.asar"
NATIVE_LIB_REL = "resources/app.asar.unpacked/node_modules/@tencent/native-resource/dist/lib"
SKIN_ZIP_REL = f"{NATIVE_LIB_REL}/skin.zip"
LAUNCHER_JSON_REL = f"{NATIVE_LIB_REL}/locales-i18n/launcher.json"

# Файлы payload: имя в payload -> путь источника относительно проекта.
PAYLOAD_SOURCES = {
    "launcher-ru-runtime.cjs": "scripts/launcher-ru-runtime.cjs",
    "launcher-entry-public-ru.draft.json": "localization/examples/launcher-entry-public-ru.draft.json",
    "launcher-layout.css": "scripts/launcher-layout.css",
    "main-logo-title.a73b14e6.png": "source/launcher-brand/main-logo-title.a73b14e6.png",
}
DICTIONARY_PARTS = (
    "translations/launcher.render.ru.json",
    "translations/launcher-ui-supplement.ru.json",
    "source/launcher-ru/launcher-ru-additions.ru.json",
)
NEWS_FILE = "source/launcher-ru/launcher-ru-news.json"
# Словарь удалённых страниц camp.qq.com (раздел "guest" в launcher-ru.json).
GUEST_FILE = "source/launcher-ru/launcher-ru-guest.ru.json"
# Подписи нативных меню, трея и диалогов главного процесса (раздел "menu" в launcher-ru.json); файл необязателен.
MENU_FILE = "source/launcher-ru/launcher-ru-menu.ru.json"
THUMBPRINT = re.compile(r"^[0-9A-F]{40}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def authenticode_thumbprint(exe: Path) -> str:
    """Отпечаток подписи exe через PowerShell (только чтение)."""
    script = (
        "$s = Get-AuthenticodeSignature -LiteralPath $env:WXQ_EXE_PATH; "
        "if ($s.Status -ne 'Valid') { [Console]::Error.WriteLine('signature ' + $s.Status); exit 3 }; "
        "[Console]::Out.Write($s.SignerCertificate.Thumbprint)"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "WXQ_EXE_PATH": str(exe)}, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"подпись exe не проверена: {(result.stderr or "").strip() or result.returncode}")
    return (result.stdout or "").strip().upper()


def build_dictionary(project: Path) -> dict:
    """Объединённый словарь: render -> supplement -> additions (поздние перекрывают ранние)."""
    strings: dict[str, str] = {}
    for part in DICTIONARY_PARTS:
        data = read_json(project / part)
        if not isinstance(data, dict):
            raise ValueError(f"{part}: ожидается объект")
        for key, value in data.items():
            if not isinstance(key, str) or not isinstance(value, str) or not key.strip() or not value.strip():
                raise ValueError(f"{part}: пустой или нестроковый элемент {key!r}")
            strings[key] = value
    news_doc = read_json(project / NEWS_FILE)
    news: dict[str, str] = {}
    for key in news_doc["keys"]:
        if key not in strings:
            raise ValueError(f"новостной заголовок без перевода: {key!r}")
        news[key] = strings[key]
    guest_doc = read_json(project / GUEST_FILE)
    if not isinstance(guest_doc, dict):
        raise ValueError(f"{GUEST_FILE}: ожидается объект")
    guest: dict[str, str] = {}
    for key, value in guest_doc.items():
        if not isinstance(key, str) or not isinstance(value, str) or not key.strip() or not value.strip():
            raise ValueError(f"{GUEST_FILE}: пустой или нестроковый элемент {key!r}")
        if re.search(r"Ваньсян|тратег", value, re.I):
            raise ValueError(f"{GUEST_FILE}: запрещённый термин в {key!r}")
        guest[key] = value
    menu: dict[str, str] = {}
    if (project / MENU_FILE).is_file():
        menu_doc = read_json(project / MENU_FILE)
        if not isinstance(menu_doc, dict):
            raise ValueError(f"{MENU_FILE}: ожидается объект")
        for key, value in menu_doc.items():
            if not isinstance(key, str) or not isinstance(value, str) or not key.strip() or not value.strip():
                raise ValueError(f"{MENU_FILE}: пустой или нестроковый элемент {key!r}")
            menu[key] = value
    result = {"schema": 1, "strings": strings, "news": news, "guest": guest}
    if menu:
        result["menu"] = menu
    return result


def dictionary_bytes(dictionary: dict) -> bytes:
    return (json.dumps(dictionary, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def build_profile(launcher_root: Path, build: str, signer: Callable[[Path], str] = authenticode_thumbprint) -> dict:
    base = launcher_root / build
    files = {
        "exe": base / EXE_REL,
        "app_asar": base / APP_ASAR_REL,
        "skin_zip": base / SKIN_ZIP_REL,
        "launcher_json": base / LAUNCHER_JSON_REL,
    }
    for name, path in files.items():
        if not path.is_file():
            raise FileNotFoundError(f"{name}: нет файла {path}")
    thumbprint = signer(files["exe"]).strip().upper()
    if not THUMBPRINT.match(thumbprint):
        raise ValueError(f"неверный отпечаток подписи: {thumbprint!r}")
    return {
        "schema": 1,
        "builds": [{
            "build": build,
            "exe_rel": f"{build}\\{EXE_REL}",
            "sha256": {name: sha256_file(path) for name, path in files.items()},
            "signer_thumbprint": thumbprint,
        }],
    }


def build_payload(project: Path, launcher_root: Path, build: str, out_root: Path, date: str,
                  signer: Callable[[Path], str] = authenticode_thumbprint) -> Path:
    for name, rel in PAYLOAD_SOURCES.items():
        if not (project / rel).is_file():
            raise FileNotFoundError(f"{name}: нет источника {project / rel}")
    dictionary = dictionary_bytes(build_dictionary(project))
    profiles = build_profile(launcher_root, build, signer)
    module_sha = sha256_file(project / PAYLOAD_SOURCES["launcher-ru-runtime.cjs"])
    dictionary_sha = hashlib.sha256(dictionary).hexdigest()
    tag = hashlib.sha256((module_sha + dictionary_sha).encode()).hexdigest()[:8]
    target = out_root / f"launcher-ru-{date}-{tag}"
    if target.exists():
        raise FileExistsError(f"каталог уже существует: {target}")
    target.mkdir(parents=True)
    try:
        for name, rel in PAYLOAD_SOURCES.items():
            shutil.copyfile(project / rel, target / name)
        (target / "launcher-ru.json").write_bytes(dictionary)
        (target / "profiles.json").write_text(json.dumps(profiles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (target / "launcher-expected.txt").write_text(f"module={module_sha}\ndictionary={dictionary_sha}\n", encoding="utf-8", newline="\n")
        manifest = {
            "schema": 1,
            "date": date,
            "tag": tag,
            "build": build,
            "module_sha256": module_sha,
            "dictionary_sha256": dictionary_sha,
            "dictionary_entries": len(json.loads(dictionary)["strings"]),
            "files": {path.name: sha256_file(path) for path in sorted(target.iterdir()) if path.is_file()},
        }
        (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def main(argv: list[str] | None = None) -> int:
    contract = read_json(CONTRACT) if CONTRACT.is_file() else {}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launcher-root", type=Path, default=DEFAULT_LAUNCHER_ROOT)
    parser.add_argument("--build", default=contract.get("validatedBuild", "26083.0.7.30004"))
    parser.add_argument("--out-root", type=Path, default=PROJECT / "build")
    parser.add_argument("--date", default=dt.date.today().isoformat())
    args = parser.parse_args(argv)
    target = build_payload(PROJECT, args.launcher_root, args.build, args.out_root, args.date)
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
