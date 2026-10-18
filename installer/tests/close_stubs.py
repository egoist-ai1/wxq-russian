"""Заглушки процессов для тестов закрытия игры и лаунчера (Closer в WXQTool.cs).

Настоящую игру и лаунчер тесты не трогают: они копируют заглушки в свои папки под нужными именами
и ограничивают поиск помощника своей папкой через WXQ_RU_TEST_CLOSE_ROOT.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

CSC = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')

# Консольная заглушка (окна нет): спит. С аргументом tree запускает дочерний процесс WXQ_STUB_CHILD и пишет его PID в WXQ_STUB_PIDFILE.
CONSOLE_SRC = r'''
using System; using System.Diagnostics; using System.IO; using System.Threading;
class P {
    static void Main(string[] a) {
        if (a.Length > 0 && a[0] == "tree") {
            var p = Process.Start(Environment.GetEnvironmentVariable("WXQ_STUB_CHILD"), "sleep");
            File.WriteAllText(Environment.GetEnvironmentVariable("WXQ_STUB_PIDFILE"), p.Id.ToString());
        }
        Thread.Sleep(Timeout.Infinite);
    }
}
'''

# Оконная заглушка: видимое главное окно. С аргументом ignore не закрывается по CloseMainWindow (только принудительно).
WINDOW_SRC = r'''
using System; using System.Windows.Forms;
class P {
    [STAThread] static void Main(string[] a) {
        var f = new Form(); f.Text = "stub";
        if (a.Length > 0 && a[0] == "ignore") f.FormClosing += (s, e) => { e.Cancel = true; };
        Application.Run(f);
    }
}
'''


def compile_stub(source: str, out: Path, window: bool) -> None:
    src = out.with_suffix('.cs')
    src.write_text(source, 'utf-8')
    args = [str(CSC), '/nologo', '/target:' + ('winexe' if window else 'exe'), '/platform:x64', '/codepage:65001']
    if window:
        args.append('/r:System.Windows.Forms.dll')
    done = subprocess.run([*args, f'/out:{out}', str(src)], capture_output=True)
    if done.returncode:
        raise SystemExit('заглушка не собралась: ' + (done.stdout + done.stderr).decode('cp866', errors='replace'))
    src.unlink()


def build_stubs(folder: Path) -> tuple[Path, Path]:
    """Возвращает (консольная, оконная)."""
    folder.mkdir(parents=True, exist_ok=True)
    console, window = folder / 'stub-console.exe', folder / 'stub-window.exe'
    compile_stub(CONSOLE_SRC, console, False)
    compile_stub(WINDOW_SRC, window, True)
    return console, window
