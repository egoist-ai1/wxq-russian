"""Тесты сборки и подписи релиза. Запуск:
python -I -m unittest discover -s scripts/tests_release -t scripts/tests_release -v
(или python -I scripts/tests_release/test_release.py)

python -I scripts/tests_release/test_release.py --regen-vectors
  пересоздаёт vectors/ (data.bin, sig.b64, public.pem) для будущего C#-теста.
"""
import base64
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pr = _load("publish_release")
PASSWORD = b"unit-test-password"


def _public_pem_text(pub):
    return "# key_id: %s\n%s" % (
        pr.key_id_of(pub),
        pub.public_bytes(serialization.Encoding.PEM,
                         serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii"),
    )


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.priv = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        cls.priv2 = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        cls.pub_pem = cls.root / "update-public.pem"
        cls.pub_pem.write_text(_public_pem_text(cls.priv.public_key()) + "\n"
                               + _public_pem_text(cls.priv2.public_key()), encoding="ascii")
        cls.key_path = cls.root / "signing-test.pem"
        cls.key_path.write_bytes(cls.priv.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(PASSWORD)))
        # фиктивные артефакты
        cls.src = cls.root / "src"
        (cls.src / "payload").mkdir(parents=True)
        (cls.src / "WXQ-Setup-1.2.3.exe").write_bytes(b"MZ-fake-setup" * 100)
        (cls.src / "install.json").write_text('{"a":1}', encoding="utf-8")
        (cls.src / "payload" / "lua.bin").write_bytes(b"lua" * 50)
        (cls.src / "payload" / "ru.pak").write_bytes(b"pak" * 70)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _build(self, out_name="out", version="1.2.3"):
        out = self.root / out_name
        os.environ["WXQ_RU_SIGNING_KEY"] = str(self.key_path)
        os.environ["WXQ_RU_SIGNING_PASS"] = PASSWORD.decode()
        try:
            rc = pr.main(["build", "--version", version,
                          "--setup", str(self.src / "WXQ-Setup-1.2.3.exe"),
                          "--install-json", str(self.src / "install.json"),
                          "--payload-dir", str(self.src / "payload"),
                          "--out", str(out), "--base-url", "https://example.invalid/dl/",
                          "--public", str(self.pub_pem), "--notes", "тест"])
        finally:
            os.environ.pop("WXQ_RU_SIGNING_KEY", None)
            os.environ.pop("WXQ_RU_SIGNING_PASS", None)
        return rc, out

    def test_build_and_verify(self):
        rc, out = self._build()
        self.assertEqual(rc, 0)
        m = json.loads((out / "update.json").read_text(encoding="utf-8"))
        self.assertEqual(m["version"], "1.2.3")
        self.assertEqual(m["product"], "wxq-russian")
        self.assertEqual(m["schema"], 1)
        self.assertEqual(m["game_versions"], ["1.6.0.1"])
        self.assertEqual([f["name"] for f in m["payload"]["files"]], ["lua.bin", "ru.pak"])
        self.assertEqual(m["setup"]["bytes"], 1300)
        self.assertEqual(m["key_id"], pr.key_id_of(self.priv.public_key()))
        keys = pr.load_public_keys(self.pub_pem)
        pr.verify_artifacts(pr.verify_manifest(out / "update.json", out / "update.json.sig", keys), out)
        self.assertEqual(pr.main(["verify", "--manifest", str(out / "update.json"),
                                  "--sig", str(out / "update.json.sig"), "--dir", str(out),
                                  "--public", str(self.pub_pem)]), 0)

    def test_tampered_manifest_rejected(self):
        _, out = self._build("out_tm")
        data = (out / "update.json").read_bytes()
        (out / "update.json").write_bytes(data.replace(b"1.2.3", b"9.9.9"))
        keys = pr.load_public_keys(self.pub_pem)
        with self.assertRaises(pr.ReleaseError):
            pr.verify_manifest(out / "update.json", out / "update.json.sig", keys)

    def test_tampered_or_foreign_signature_rejected(self):
        _, out = self._build("out_ts")
        data = (out / "update.json").read_bytes()
        pub = self.priv.public_key()
        good = pr.sign_bytes(self.priv, data)
        self.assertTrue(pr.verify_bytes(pub, data, pr.sig_to_text(good)))
        bad = bytearray(good)
        bad[10] ^= 1
        self.assertFalse(pr.verify_bytes(pub, data, pr.sig_to_text(bytes(bad))))
        self.assertFalse(pr.verify_bytes(pub, data, pr.sig_to_text(pr.sign_bytes(self.priv2, data))))
        self.assertFalse(pr.verify_bytes(pub, data, "не base64!"))
        # один лишний байт в данных (например CRLF) ломает подпись
        self.assertFalse(pr.verify_bytes(pub, data + b"\n", pr.sig_to_text(good)))

    def test_bad_versions_rejected(self):
        for v in ["1.2.3d", "1.2", "v1.2.3", "1.2.3.4", "01.2.3", "1.2.3-diag", ""]:
            with self.assertRaises(pr.ReleaseError, msg=v):
                pr.check_version(v)
        os.environ["WXQ_RU_SIGNING_KEY"] = str(self.key_path)
        try:
            self.assertEqual(pr.main(["build", "--version", "1.2.3d",
                                      "--setup", str(self.src / "WXQ-Setup-1.2.3.exe"),
                                      "--out", str(self.root / "out_badver"),
                                      "--base-url", "https://example.invalid/dl/"]), 1)
        finally:
            os.environ.pop("WXQ_RU_SIGNING_KEY", None)
        self.assertFalse((self.root / "out_badver" / "update.json").exists())

    def test_sha_mismatch_rejected(self):
        _, out = self._build("out_sha")
        keys = pr.load_public_keys(self.pub_pem)
        m = pr.verify_manifest(out / "update.json", out / "update.json.sig", keys)
        (out / "ru.pak").write_bytes(b"pak" * 69 + b"XXX")  # тот же размер, другой хеш
        with self.assertRaises(pr.ReleaseError) as cm:
            pr.verify_artifacts(m, out)
        self.assertIn("SHA-256", str(cm.exception))
        (out / "ru.pak").write_bytes(b"short")
        with self.assertRaises(pr.ReleaseError):
            pr.verify_artifacts(m, out)

    def test_unknown_key_id_rejected(self):
        _, out = self._build("out_kid")
        other = self.root / "other.pem"
        other.write_text(_public_pem_text(rsa.generate_private_key(65537, 2048).public_key()), encoding="ascii")
        with self.assertRaises(pr.ReleaseError):
            pr.verify_manifest(out / "update.json", out / "update.json.sig", pr.load_public_keys(other))

    def test_wrong_password_rejected(self):
        with self.assertRaises(pr.ReleaseError):
            pr.load_private_key(self.key_path, b"wrong")

    def test_check_finds_leaks(self):
        clean = self.root / "clean"
        clean.mkdir()
        (clean / "README.md").write_text("Обычный текст https://example.com/a", encoding="utf-8")
        self.assertEqual(pr.run_check(clean), [])
        dirty = self.root / "dirty"
        dirty.mkdir()
        (dirty / "a.txt").write_text("path " + "C:" + "\\Users" + "\\bob\\x", encoding="utf-8")
        (dirty / "b.txt").write_text("t=" + "gh" + "p_" + "A" * 20, encoding="utf-8")
        (dirty / ("." + "env")).write_text("X=1", encoding="utf-8")
        (dirty / "k.pem").write_text("x", encoding="utf-8")
        labels = {f[2] for f in pr.run_check(dirty)}
        self.assertTrue({"личный путь", "токен GitHub", "файл окружения", "посторонний PEM"} <= labels)
        self.assertEqual(pr.main(["--check", "--root", str(dirty)]), 1)
        self.assertEqual(pr.main(["--check", "--root", str(clean)]), 0)

    def test_cli_subprocess_with_env(self):
        out = self.root / "out_cli"
        env = dict(os.environ, WXQ_RU_SIGNING_KEY=str(self.key_path),
                   WXQ_RU_SIGNING_PASS=PASSWORD.decode())
        r = subprocess.run([sys.executable, "-I", str(SCRIPTS / "publish_release.py"), "build",
                            "--version", "1.2.3", "--setup", str(self.src / "WXQ-Setup-1.2.3.exe"),
                            "--out", str(out), "--base-url", "https://example.invalid/dl/",
                            "--public", str(self.pub_pem), "--repo", "owner/repo"],
                           env=dict(env, PYTHONIOENCODING="utf-8"), capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(PASSWORD.decode(), r.stdout + r.stderr)
        self.assertIn("gh release create v1.2.3 --draft", r.stdout)
        lines = [l for l in r.stdout.splitlines() if l.strip().startswith("gh ")]
        self.assertTrue(lines[-2].endswith("--repo owner/repo") and "update.json\"" in lines[-2])
        self.assertIn("--draft=false", lines[-1])

    def test_vectors_valid(self):
        vec = HERE / "vectors"
        if not (vec / "data.bin").exists():
            self.skipTest("vectors не созданы")
        pub = serialization.load_pem_public_key((vec / "public.pem").read_bytes())
        self.assertTrue(pr.verify_bytes(pub, (vec / "data.bin").read_bytes(),
                                        (vec / "sig.b64").read_text(encoding="ascii")))


def regen_vectors():
    vec = HERE / "vectors"
    vec.mkdir(exist_ok=True)
    priv = rsa.generate_private_key(public_exponent=65537, key_size=3072)  # закрытый ключ не сохраняется
    data = ('{"schema":1,"product":"wxq-russian","version":"1.2.3","notes":"тест ✓"}\n').encode("utf-8")
    (vec / "data.bin").write_bytes(data)
    (vec / "sig.b64").write_text(pr.sig_to_text(pr.sign_bytes(priv, data)) + "\n", encoding="ascii", newline="\n")
    (vec / "public.pem").write_bytes(priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    (vec / "README.md").write_text(
        "Вектор для C#-теста (.NET Framework). Проверка: RSACryptoServiceProvider, импорт public.pem "
        "(SubjectPublicKeyInfo), VerifyData(data.bin, \"SHA256\", Convert.FromBase64String(sig.b64)) "
        "должен вернуть true; после изменения любого байта data.bin - false. "
        "Закрытый ключ вектора не сохранён.\n", encoding="utf-8", newline="\n")
    print("vectors обновлены:", vec)


if __name__ == "__main__":
    if "--regen-vectors" in sys.argv:
        regen_vectors()
    else:
        unittest.main()
