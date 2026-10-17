#!/usr/bin/env python3
"""Ключи подписи автообновления WXQ Russian.

generate  - создать основной и запасной RSA-3072 ключи.
            Приватные PEM (с паролем) -> %USERPROFILE%\\.wxq-ru\\ (вне репозитория),
            пароль -> key-pass.txt там же (в вывод не печатается),
            публичные -> release-repo\\keys\\update-public.pem и keys.json.
list      - показать key_id публичных ключей из репозитория.

Запуск: python -I release_keys.py generate
Запасной приватный ключ после создания унесите офлайн (на флешку) и удалите с ПК.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization  # noqa: F401
from cryptography.hazmat.primitives.asymmetric import rsa

KEY_BITS = 3072
PRIVATE_NAMES = {"primary": "signing-primary.pem", "backup": "signing-backup.pem"}
PASS_NAME = "key-pass.txt"


def default_secret_dir() -> Path:
    return Path(os.path.expanduser("~")) / ".wxq-ru"


def default_repo_keys_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "keys"


def key_id_of(public_key) -> str:
    """key_id = первые 16 hex SHA-256 от DER SubjectPublicKeyInfo."""
    der = public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(der).hexdigest()[:16]


def public_pem(public_key) -> str:
    return public_key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")


def parse_public_pem_bundle(text: str):
    """Достаёт все публичные ключи из update-public.pem -> [(key_id, key)]."""
    out = []
    pattern = r"-----BEGIN PUBLIC KEY-----.*?-----END PUBLIC KEY-----"
    for block in re.findall(pattern, text, flags=re.S):
        key = serialization.load_pem_public_key(block.encode("ascii"))
        out.append((key_id_of(key), key))
    return out


def _restrict_to_owner(path: Path) -> None:
    """Права только владельцу."""
    try:
        if os.name == "nt":
            user = os.environ.get("USERNAME", "")
            if not user:
                raise RuntimeError("USERNAME не задан")
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", user + ":F"],
                check=True, capture_output=True,
            )
        else:
            os.chmod(path, 0o600)
    except Exception as exc:  # не фатально, но сообщаем
        print("ВНИМАНИЕ: не удалось ограничить права на %s (%s)" % (path.name, exc), file=sys.stderr)


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    _restrict_to_owner(path)


def generate(secret_dir: Path, repo_keys_dir: Path) -> int:
    secret_dir.mkdir(parents=True, exist_ok=True)
    _restrict_to_owner(secret_dir)
    targets = [secret_dir / n for n in PRIVATE_NAMES.values()] + [secret_dir / PASS_NAME]
    existing = [t.name for t in targets if t.exists()]
    if existing:
        print("Отказ: уже существуют %s в %s. Ключи не перезаписываю." % (", ".join(existing), secret_dir))
        return 2

    password = secrets.token_urlsafe(32)
    _write_private(secret_dir / PASS_NAME, (password + "\n").encode("ascii"))
    enc = serialization.BestAvailableEncryption(password.encode("ascii"))

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries = []
    for role, fname in PRIVATE_NAMES.items():
        priv = rsa.generate_private_key(public_exponent=65537, key_size=KEY_BITS)
        pem = priv.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc
        )
        _write_private(secret_dir / fname, pem)
        pub = priv.public_key()
        entries.append({
            "key_id": key_id_of(pub),
            "role": role,
            "algorithm": "RSA-%d" % KEY_BITS,
            "signature": "PKCS1v15-SHA256",
            "created_utc": now,
            "public_key_pem": public_pem(pub),
        })

    repo_keys_dir.mkdir(parents=True, exist_ok=True)
    bundle = []
    for e in entries:
        bundle.append("# key_id: %s role: %s\n%s" % (e["key_id"], e["role"], e["public_key_pem"]))
    (repo_keys_dir / "update-public.pem").write_text("\n".join(bundle), encoding="ascii", newline="\n")
    (repo_keys_dir / "keys.json").write_text(
        json.dumps({"schema": 1, "keys": entries}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    for e in entries:
        print("%s key_id=%s" % (e["role"], e["key_id"]))
    print("Приватные ключи и key-pass.txt: %s" % secret_dir)
    print("Публичные: %s" % repo_keys_dir)
    print("Запасной ключ (%s) унесите офлайн и удалите с ПК." % PRIVATE_NAMES["backup"])
    return 0


def list_keys(repo_keys_dir: Path) -> int:
    pem = repo_keys_dir / "update-public.pem"
    if not pem.is_file():
        print("Нет %s" % pem)
        return 2
    for kid, _ in parse_public_pem_bundle(pem.read_text(encoding="ascii")):
        print(kid)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="создать пару ключей (основной + запасной)")
    g.add_argument("--secret-dir", type=Path, default=None)
    g.add_argument("--repo-keys", type=Path, default=None)
    l = sub.add_parser("list", help="показать key_id публичных ключей")
    l.add_argument("--repo-keys", type=Path, default=None)
    a = p.parse_args(argv)
    repo_keys = a.repo_keys or default_repo_keys_dir()
    if a.cmd == "generate":
        return generate(a.secret_dir or default_secret_dir(), repo_keys)
    return list_keys(repo_keys)


if __name__ == "__main__":
    sys.exit(main())
