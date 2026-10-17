#!/usr/bin/env python3
"""Сборка и проверка релиза автообновления WXQ Russian.

  publish_release.py --check [--root DIR]
      Ищет в исходниках будущего репозитория утечки (личные пути, токены,
      файлы окружения, приватные ключи). Ненулевой код при находках.

  publish_release.py build --version X.Y.Z --setup EXE [--install-json F]
        [--payload-dir DIR] --out DIR [--base-url URL ...] [--repo owner/name]
      Считает SHA-256 и размеры, пишет update.json, подписывает (RSA-3072,
      PKCS#1 v1.5, SHA-256 над точными байтами update.json), проверяет подпись
      публичным ключом, пишет update.json.sig (base64) и печатает команды gh.
      Команды НЕ выполняются.

  publish_release.py verify --manifest update.json --sig update.json.sig
        [--dir DIR] [--public PEM]
      Проверяет подпись и (с --dir) размеры и SHA-256 файлов.

Закрытый ключ: путь в env WXQ_RU_SIGNING_KEY. Пароль: env WXQ_RU_SIGNING_PASS
либо файл key-pass.txt рядом с ключом (или env WXQ_RU_SIGNING_PASS_FILE).
Пароль никуда не печатается.
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

SCHEMA = 1
PRODUCT = "wxq-russian"
VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
DEFAULT_GAME_VERSIONS = ["1.6.0.1"]
SIGNING_KEY_ENV = "WXQ_RU_SIGNING_KEY"


class ReleaseError(Exception):
    pass


# ---------------------------------------------------------------- ключи

def key_id_of(public_key) -> str:
    der = public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(der).hexdigest()[:16]


def parse_public_pem_bundle(text: str):
    out = []
    pattern = r"-----BEGIN PUBLIC KEY-----.*?-----END PUBLIC KEY-----"
    for block in re.findall(pattern, text, flags=re.S):
        key = serialization.load_pem_public_key(block.encode("ascii"))
        out.append((key_id_of(key), key))
    return out


def default_public_pem() -> Path:
    return Path(__file__).resolve().parent.parent / "keys" / "update-public.pem"


def load_public_keys(pem_path: Path):
    if not pem_path.is_file():
        raise ReleaseError("нет файла публичных ключей: %s" % pem_path)
    keys = parse_public_pem_bundle(pem_path.read_text(encoding="ascii"))
    if not keys:
        raise ReleaseError("в %s нет публичных ключей" % pem_path)
    return dict(keys)


def _read_password(key_path: Path):
    pw = os.environ.get("WXQ_RU_SIGNING_PASS")
    if pw:
        return pw.encode("utf-8")
    pf = os.environ.get("WXQ_RU_SIGNING_PASS_FILE")
    pass_file = Path(pf) if pf else key_path.parent / "key-pass.txt"
    if pass_file.is_file():
        return pass_file.read_text(encoding="utf-8").strip().encode("utf-8")
    return None


def load_private_key(key_path: Path, password=None):
    if not key_path.is_file():
        raise ReleaseError("нет файла закрытого ключа: %s" % key_path)
    if password is None:
        password = _read_password(key_path)
    try:
        return serialization.load_pem_private_key(key_path.read_bytes(), password=password)
    except (TypeError, ValueError):
        raise ReleaseError("не удалось открыть закрытый ключ (нет или неверный пароль)")


# ---------------------------------------------------------------- подпись

def sign_bytes(private_key, data: bytes) -> bytes:
    """RSA PKCS#1 v1.5 + SHA-256; результат - сырая подпись."""
    return private_key.sign(data, padding.PKCS1v15(), hashes.SHA256())


def sig_to_text(sig: bytes) -> str:
    return base64.b64encode(sig).decode("ascii")


def verify_bytes(public_key, data: bytes, sig_text) -> bool:
    try:
        if isinstance(sig_text, bytes):
            sig_text = sig_text.decode("ascii")
        sig = base64.b64decode(sig_text.strip(), validate=True)
        public_key.verify(sig, data, padding.PKCS1v15(), hashes.SHA256())
        return True
    except (InvalidSignature, ValueError, UnicodeDecodeError):
        return False


# ---------------------------------------------------------------- манифест

def check_version(version: str) -> str:
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise ReleaseError("версия должна быть строго X.Y.Z (получено %r)" % (version,))
    return version


def sha256_file(path: Path):
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
            size += len(chunk)
    return size, h.hexdigest()


def _file_entry(path: Path, name: str) -> dict:
    if not ASSET_NAME_RE.match(name):
        raise ReleaseError("недопустимое имя файла для релиза: %r" % name)
    size, digest = sha256_file(path)
    return {"name": name, "bytes": size, "sha256": digest}


def build_manifest(version, setup, install_json, payload_files, key_id, base_urls,
                   game_versions=None, min_updater=1, notes="", issued_utc=None) -> dict:
    """setup/install_json/payload_files - пути Path; payload_files - список Path (плоский)."""
    check_version(version)
    if not base_urls:
        raise ReleaseError("нужен хотя бы один base_url")
    for u in base_urls:
        if not u.startswith("https://") or not u.endswith("/"):
            raise ReleaseError("base_url должен быть https://.../ (с завершающим /): %r" % u)
    setup = Path(setup)
    if not setup.is_file():
        raise ReleaseError("нет установщика: %s" % setup)
    files = [_file_entry(Path(p), Path(p).name) for p in payload_files]
    names = [f["name"] for f in files] + [setup.name, "update.json", "update.json.sig"]
    if install_json is not None:
        names.append("install.json")
    if len(set(n.lower() for n in names)) != len(names):
        raise ReleaseError("повторяющиеся имена файлов релиза")
    payload = {"files": sorted(files, key=lambda f: f["name"])}
    if install_json is not None:
        entry = _file_entry(Path(install_json), "install.json")
        payload["install_json"] = {"bytes": entry["bytes"], "sha256": entry["sha256"]}
    setup_entry = _file_entry(setup, setup.name)
    if issued_utc is None:
        issued_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "schema": SCHEMA,
        "product": PRODUCT,
        "version": version,
        "issued_utc": issued_utc,
        "key_id": key_id,
        "game_versions": list(game_versions or DEFAULT_GAME_VERSIONS),
        "min_updater": int(min_updater),
        "base_urls": list(base_urls),
        "setup": setup_entry,
        "payload": payload,
        "notes": notes,
    }


def manifest_bytes(manifest: dict) -> bytes:
    return (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def verify_manifest(manifest_path: Path, sig_path: Path, public_keys: dict) -> dict:
    """Проверяет подпись над точными байтами файла. Возвращает манифест."""
    data = Path(manifest_path).read_bytes()
    sig_text = Path(sig_path).read_text(encoding="ascii")
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ReleaseError("update.json не разбирается как JSON")
    kid = manifest.get("key_id")
    key = public_keys.get(kid)
    if key is None:
        raise ReleaseError("key_id %r не найден среди публичных ключей" % (kid,))
    if not verify_bytes(key, data, sig_text):
        raise ReleaseError("подпись НЕ совпадает")
    if manifest.get("schema") != SCHEMA or manifest.get("product") != PRODUCT:
        raise ReleaseError("неверные schema/product")
    check_version(manifest.get("version"))
    return manifest


def verify_artifacts(manifest: dict, directory: Path) -> None:
    """Размеры и SHA-256 всех файлов манифеста в directory."""
    directory = Path(directory)
    entries = [manifest["setup"]] + list(manifest["payload"].get("files", []))
    ij = manifest["payload"].get("install_json")
    if ij:
        entries.append({"name": "install.json", "bytes": ij["bytes"], "sha256": ij["sha256"]})
    for e in entries:
        p = directory / e["name"]
        if not p.is_file():
            raise ReleaseError("нет файла %s" % e["name"])
        size, digest = sha256_file(p)
        if size != e["bytes"]:
            raise ReleaseError("размер %s не совпадает (%d != %d)" % (e["name"], size, e["bytes"]))
        if digest != e["sha256"].lower():
            raise ReleaseError("SHA-256 %s не совпадает" % e["name"])


def gh_commands(manifest: dict, out_dir: Path, repo=None) -> list:
    v = manifest["version"]
    tag = "v" + v
    r = (" --repo %s" % repo) if repo else ""
    assets = [manifest["setup"]["name"]]
    if manifest["payload"].get("install_json"):
        assets.append("install.json")
    assets += [f["name"] for f in manifest["payload"]["files"]]
    q = lambda n: '"%s"' % (Path(out_dir) / n)
    cmds = [
        'gh release create %s --draft --title "WXQ Russian %s" --notes-file %s%s %s'
        % (tag, v, q("notes.md"), r, " ".join(q(a) for a in assets)),
        "gh release upload %s %s%s" % (tag, q("update.json.sig"), r),
        # update.json - последним: до него клиенты не видят новую версию
        "gh release upload %s %s%s" % (tag, q("update.json"), r),
        "gh release edit %s --draft=false%s" % (tag, r),
    ]
    return cmds


# ---------------------------------------------------------------- build

def do_build(a) -> int:
    check_version(a.version)
    key_env = os.environ.get(SIGNING_KEY_ENV)
    if not key_env:
        raise ReleaseError("не задан env %s (путь к закрытому ключу)" % SIGNING_KEY_ENV)
    priv = load_private_key(Path(key_env))
    pub_keys = load_public_keys(a.public or default_public_pem())
    kid = key_id_of(priv.public_key())
    if kid not in pub_keys:
        raise ReleaseError("публичного ключа для key_id %s нет в keys/update-public.pem" % kid)

    base_urls = list(a.base_url or [])
    if not base_urls and a.repo:
        base_urls = ["https://github.com/%s/releases/download/v%s/" % (a.repo, a.version)]

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    setup = Path(a.setup)
    payload_files = []
    if a.payload_dir:
        pd = Path(a.payload_dir)
        payload_files = sorted(p for p in pd.iterdir() if p.is_file() and p.name != "install.json")
        if any(p.is_dir() for p in pd.iterdir()):
            print("ВНИМАНИЕ: вложенные папки payload игнорируются (имена плоские)", file=sys.stderr)
    install_json = Path(a.install_json) if a.install_json else None
    notes = a.notes or ""
    if a.notes_file:
        notes = Path(a.notes_file).read_text(encoding="utf-8").strip()

    manifest = build_manifest(a.version, setup, install_json, payload_files, kid, base_urls,
                              game_versions=a.game_version or None, min_updater=a.min_updater,
                              notes=notes)
    # артефакты кладём рядом с манифестом: выгрузка и verify --dir
    for src in [setup] + ([install_json] if install_json else []) + payload_files:
        dst = out / (src.name if src != install_json else "install.json")
        if dst.resolve() != src.resolve():
            shutil.copyfile(src, dst)
    (out / "notes.md").write_text((notes or "WXQ Russian %s" % a.version) + "\n", encoding="utf-8", newline="\n")

    mpath, spath = out / "update.json", out / "update.json.sig"
    mpath.write_bytes(manifest_bytes(manifest))
    spath.write_text(sig_to_text(sign_bytes(priv, mpath.read_bytes())) + "\n", encoding="ascii", newline="\n")
    # самопроверка публичным ключом и артефактов
    verified = verify_manifest(mpath, spath, pub_keys)
    verify_artifacts(verified, out)
    print("OK: %s v%s подписан ключом %s, самопроверка пройдена" % (PRODUCT, a.version, kid))
    print("Файлы: %s" % out)
    print("\nКоманды (НЕ выполнены; порядок важен, update.json последним):")
    for c in gh_commands(manifest, out, a.repo):
        print("  " + c)
    return 0


def do_verify(a) -> int:
    pub_keys = load_public_keys(a.public or default_public_pem())
    manifest = verify_manifest(a.manifest, a.sig, pub_keys)
    if a.dir:
        verify_artifacts(manifest, a.dir)
    print("OK: подпись верна, версия %s, key_id %s%s"
          % (manifest["version"], manifest["key_id"], ", файлы совпадают" if a.dir else ""))
    return 0


# ---------------------------------------------------------------- --check

# Шаблоны собраны из частей, чтобы сам скрипт не срабатывал на себя.
_P = [
    ("личный путь", re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+", re.I)),
    ("токен GitHub", re.compile("gh" + r"[pousr]_[A-Za-z0-9]{10,}|github" + "_pat_")),
    ("файл окружения", re.compile(r"(?<![\w-])\." + r"env\b", re.I)),
    ("абсолютный путь Windows", re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]{1,2}(?![\\/])(?!Windows\b|Program Files)[^\s\"'<>|]")),
    ("закрытый ключ", re.compile("-----BEGIN [A-Z ]*" + "PRIVATE KEY-----")),
]
_SKIP_DIRS = {".git", "build", "dist", "rollback", "__pycache__", "node_modules"}
_BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".exe", ".dll", ".zip", ".7z", ".ttf",
               ".otf", ".woff", ".woff2", ".db", ".bin", ".pak", ".pyc"}
_MAX_SCAN = 4 * 1024 * 1024


def run_check(root: Path) -> list:
    root = Path(root)
    findings = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in _SKIP_DIRS for part in rel.parts) or not path.is_file():
            continue
        if path.name.lower() == "." + "env" or path.name.lower().startswith("." + "env."):
            findings.append((str(rel), 0, "файл окружения"))
            continue
        if path.suffix.lower() == ".pem" and rel.as_posix() not in ("keys/update-public.pem", "scripts/tests_release/vectors/public.pem"):
            findings.append((str(rel), 0, "посторонний PEM"))
        if path.suffix.lower() in _BINARY_EXT or path.stat().st_size > _MAX_SCAN:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for label, rx in _P:
                if rx.search(line):
                    findings.append((str(rel), n, label))
    return findings


def do_check(root: Path) -> int:
    findings = run_check(root)
    if not findings:
        print("OK: утечек не найдено в %s" % root)
        return 0
    for rel, n, label in findings:
        print("%s:%s: %s" % (rel, n or "-", label))  # содержимое строк не печатаем
    print("Найдено: %d" % len(findings))
    return 1


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true", help="проверка исходников на утечки")
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    sub = p.add_subparsers(dest="cmd")
    b = sub.add_parser("build")
    b.add_argument("--version", required=True)
    b.add_argument("--setup", required=True)
    b.add_argument("--install-json")
    b.add_argument("--payload-dir")
    b.add_argument("--out", required=True)
    b.add_argument("--base-url", action="append")
    b.add_argument("--repo", help="owner/name для gh и base_url по умолчанию")
    b.add_argument("--game-version", action="append")
    b.add_argument("--min-updater", type=int, default=1)
    b.add_argument("--notes", default="")
    b.add_argument("--notes-file")
    b.add_argument("--public", type=Path)
    v = sub.add_parser("verify")
    v.add_argument("--manifest", required=True, type=Path)
    v.add_argument("--sig", required=True, type=Path)
    v.add_argument("--dir", type=Path)
    v.add_argument("--public", type=Path)
    a = p.parse_args(argv)
    try:
        if a.check:
            return do_check(a.root)
        if a.cmd == "build":
            return do_build(a)
        if a.cmd == "verify":
            return do_verify(a)
        p.print_help()
        return 2
    except ReleaseError as exc:
        print("ОШИБКА: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
