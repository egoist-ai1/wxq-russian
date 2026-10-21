"""Проверка сохранения EXE и отказов замены; только Temp и TESTBUILD."""
from pathlib import Path
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "installer"))
import build_installer as bi


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    work = Path(tempfile.mkdtemp(prefix="wxq-manager-update-"))
    tool = work / "tool"
    tool.mkdir()
    bi.build_tool(tool, test_build=True)
    sources = {}
    for version in ("0.6.4", "0.6.7"):
        source = work / ("source-" + version + ".cs")
        source.write_text(
            'using System.Reflection;[assembly:AssemblyFileVersion("' + version
            + '.0")]class P{static int Main(){return 0;}}', encoding="utf-8")
        exe = source.with_suffix(".exe")
        result = subprocess.run([str(bi.CSC), "/nologo", "/target:winexe", "/platform:x64",
                                 "/out:" + str(exe), str(source)], capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
        sources[version] = exe
    app = work / "app"
    app.mkdir()
    target = app / "WXQ-Russian.exe"
    checks = []

    def check(name, ok):
        checks.append({"name": name, "passed": bool(ok)})
        print(("PASS " if ok else "FAIL ") + name, flush=True)

    def run(source, *, digest=None, fault=None):
        out = work / "out.txt"
        out.unlink(missing_ok=True)
        env = dict(os.environ)
        env.pop("WXQ_RU_TEST_MANAGER_REPLACE_FAILURE", None)
        if fault:
            env["WXQ_RU_TEST_MANAGER_REPLACE_FAILURE"] = fault
        result = subprocess.run(
            [str(tool / "WXQTool.exe"), "manager-update", "--app", str(app),
             "--source", str(source), "--sha", digest or sha(source),
             "--data", str(work / "data"), "--out", str(out)],
            env=env, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        rows = dict(line.split("=", 1) for line in out.read_text("utf-8-sig").splitlines() if "=" in line)
        return result.returncode, rows

    code, rows = run(sources["0.6.7"])
    check("новый EXE сохранён с точным SHA", code == 0 and sha(target) == sha(sources["0.6.7"]))
    code, rows = run(sources["0.6.4"])
    check("понижение EXE запрещено", code == 2 and rows.get("code") == "downgrade"
          and sha(target) == sha(sources["0.6.7"]))
    code, rows = run(sources["0.6.7"], digest="0" * 64)
    check("чужой SHA не меняет приложение", code == 2 and rows.get("code") == "sha_mismatch"
          and sha(target) == sha(sources["0.6.7"]))
    target.write_bytes(sources["0.6.4"].read_bytes())
    code, rows = run(sources["0.6.7"], fault="1177")
    check("ошибка ReplaceFile после переноса старого файла восстанавливает имя",
          code != 0 and target.is_file() and sha(target) == sha(sources["0.6.4"]))
    check("после восстановленного отказа нет мусора",
          not list(app.glob("manager-*.new")) and not list(app.glob("manager-*.previous")))
    code, rows = run(sources["0.6.7"])
    check("после отказа повторная установка успешна", code == 0 and sha(target) == sha(sources["0.6.7"]))
    bad = work / "not-an-executable"
    bad.write_bytes(b"not a Windows application")
    code, rows = run(bad)
    check("файл без PE-версии не заменяет EXE", code == 2 and rows.get("code") == "version"
          and sha(target) == sha(sources["0.6.7"]))
    target.write_bytes(sources["0.6.4"].read_bytes())
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(target), 0x80000000, 1, None, 3, 0x80, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    try:
        code, rows = run(sources["0.6.7"])
        check("занятый старый EXE остаётся доступен после отказа",
              code != 0 and target.is_file() and sha(target) == sha(sources["0.6.4"]))
    finally:
        kernel.CloseHandle(handle)
    code, rows = run(sources["0.6.7"])
    check("после освобождения файла повтор успешен", code == 0 and sha(target) == sha(sources["0.6.7"]))
    check("после успеха backup и pending убраны",
          not list(app.glob("manager-*.new")) and not list(app.glob("manager-*.previous")))
    report = {"work": str(work), "source_sha256": sha(ROOT / "installer/WXQTool.cs"),
              "checks": checks, "passed": all(c["passed"] for c in checks)}
    (work / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(work))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
