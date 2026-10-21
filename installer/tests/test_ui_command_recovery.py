"""Регрессия команд installed-экрана после ответа обновления, без окна.

    python -X utf8 -B installer/tests/test_ui_command_recovery.py [--work <Temp>]

Собирает настоящий WXQSetupUI.cs с отдельной STA entry point. Создаются только
WPF-элементы в памяти: Window, Show, dispatcher loop и desktop input не нужны.
Команды проверяются по событию MainView.Command, а не по полю updatePending.
Production-исходник, компилятор, harness и результаты закрепляются SHA-256.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[2]
UI = PROJECT / 'installer/ui'
CSC = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')
WPF = CSC.parent / 'WPF'

HARNESS = r'''
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using WXQSetup;

static class UiCommandRecoveryHarness
{
    static readonly List<object> results = new List<object>();
    static int failures;

    static void Check(string name, bool passed, int expected, int actual)
    {
        results.Add(new { name = name, passed = passed, expected = expected, actual = actual });
        Console.WriteLine((passed ? "PASS " : "FAIL ") + name + " expected=" + expected + " actual=" + actual);
        if (!passed) failures++;
    }

    static MainView Installed(string updateState, List<string> commands)
    {
        var view = new MainView();
        view.S.UpdateEnabled = true;
        view.S.UpdateState = updateState;
        view.S.UpdateVersion = "0.6.7";
        view.S.TranslationVersion = "0.6.4";
        view.S.ManagerVersion = "0.6.7";
        view.S.GameVersion = "1.6.0.1";
        view.Show("installed");
        view.Command += delegate(string command) { commands.Add(command); };
        return view;
    }

    static string VisibleText(DependencyObject node)
    {
        var value = new StringBuilder();
        var text = node as TextBlock;
        if (text != null) value.AppendLine(text.Text);
        for (int i = 0; i < VisualTreeHelper.GetChildrenCount(node); i++)
            value.Append(VisibleText(VisualTreeHelper.GetChild(node, i)));
        return value.ToString();
    }

    static void RenderInstalled(string path)
    {
        var view = Installed("current", new List<string>());
        var host = new Border { Child = view, Background = Tk.Bg, Width = MainView.W, Height = MainView.H };
        host.Measure(new Size(MainView.W, MainView.H));
        host.Arrange(new Rect(0, 0, MainView.W, MainView.H));
        host.UpdateLayout();
        var bitmap = new RenderTargetBitmap((int)MainView.W, (int)MainView.H, 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(host);
        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using (var output = File.Create(path)) encoder.Save(output);
        File.WriteAllText(Path.ChangeExtension(path, ".txt"), VisibleText(view), new UTF8Encoding(false));
    }

    static void PendingReply(string finalState)
    {
        var commands = new List<string>();
        var view = Installed("available", commands);
        view.Do("update");
        view.Do("update");
        Check(finalState + ": update dispatched once before response", commands.Count == 1 && commands[0] == "update", 1, commands.Count);
        view.S.UpdateState = finalState;
        view.AcceptUpdateState();
        view.Do("install");
        int installs = commands.FindAll(delegate(string command) { return command == "install"; }).Count;
        Check(finalState + ": reinstall command recovers after terminal response", installs == 1, 1, installs);
        view.Do("remove");
        int removes = commands.FindAll(delegate(string command) { return command == "remove"; }).Count;
        Check(finalState + ": rollback command recovers after terminal response", removes == 1, 1, removes);
    }

    [STAThread]
    static int Main(string[] args)
    {
        var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        Tk.Still = true;
        Tk.LoadFonts(Path.Combine(Path.GetDirectoryName(args[0]), "fonts"));
        try
        {
            var baselineCommands = new List<string>();
            var baseline = Installed("current", baselineCommands);
            baseline.Do("install");
            Check("current without pending update dispatches reinstall", baselineCommands.Count == 1 && baselineCommands[0] == "install", 1, baselineCommands.Count);

            // Ответ current может прийти, если выпуск уже установлен другим
            // процессом; available — если download не был принят на границе check.
            // Оба ответа завершили запрос, следующее действие не должно исчезать.
            PendingReply("current");
            PendingReply("available");
            PendingReply("failed");

            foreach (string busy in new[] { "downloading", "handoff" })
            {
                var commands = new List<string>();
                var view = Installed("available", commands);
                view.Do("update");
                view.S.UpdateState = busy;
                view.AcceptUpdateState();
                view.Do("install"); view.Do("remove"); view.Do("update");
                Check(busy + ": conflicting actions stay blocked", commands.Count == 1, 1, commands.Count);
                view.Do("close");
                Check(busy + ": close remains available", commands.Count == 2 && commands[1] == "close", 2, commands.Count);
            }

            var checkCommands = new List<string>();
            var checking = Installed("current", checkCommands);
            checking.Do("checkupdate");
            checking.Do("install");
            Check("background check does not swallow reinstall", checkCommands.Count == 2 && checkCommands[1] == "install", 2, checkCommands.Count);

            var failedCommands = new List<string>();
            var failedView = Installed("current", failedCommands);
            bool firstWriteFails = true;
            int delivered = 0;
            failedView.Command += delegate(string command)
            {
                if (firstWriteFails) { firstWriteFails = false; failedView.CommandFailed(command); }
                else if (command == "install") delivered++;
            };
            failedView.Do("install");
            string message = VisibleText(failedView);
            Check("failed install delivery exposes action error and retry text", message.Contains("Действие не началось") && message.Contains("Повторить"), 1, message.Contains("Действие не началось") && message.Contains("Повторить") ? 1 : 0);
            failedView.Do("install");
            Check("retry after failed install delivery dispatches command", delivered == 1, 1, delivered);

            var versions = new UiState();
            long revision = 0;
            string screen, signature;
            bool parsed = Proto.TryApply("{\"rev\":1,\"screen\":\"installed\",\"manager_version\":\"0.6.7\",\"translation_version\":\"0.6.4\",\"game_version\":\"1.6.0.1\"}", versions, ref revision, out screen, out signature);
            bool distinct = parsed && versions.ManagerVersion == "0.6.7" && versions.TranslationVersion == "0.6.4" && versions.GameVersion == "1.6.0.1";
            Check("manager, translation and game versions remain distinct", distinct, 1, distinct ? 1 : 0);

            RenderInstalled(Path.Combine(Path.GetDirectoryName(args[0]), "installed-96.png"));

            File.WriteAllText(args[0], new JavaScriptSerializer().Serialize(new { passed = failures == 0, failed = failures, checks = results }), new UTF8Encoding(false));
            return failures == 0 ? 0 : 1;
        }
        finally { app.Shutdown(); }
    }
}
'''


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path)
    options = parser.parse_args()
    work = options.work or Path(tempfile.mkdtemp(prefix='wxq-ui-command-recovery-'))
    work.mkdir(parents=True, exist_ok=True)
    # Снимок исключает смешанную сборку, если production меняют параллельно.
    production = UI / 'WXQSetupUI.cs'
    source = work / 'WXQSetupUI.cs'
    source.write_bytes(production.read_bytes())
    harness = work / 'UiCommandRecoveryHarness.cs'
    harness.write_text(HARNESS, encoding='utf-8')
    executable = work / 'UiCommandRecovery.exe'
    result = work / 'result.json'
    command = [str(CSC), '/nologo', '/target:exe', '/main:UiCommandRecoveryHarness',
               '/platform:anycpu', '/optimize+', '/codepage:65001',
               f'/out:{executable}', f'/lib:{WPF}']
    for assembly in ('PresentationCore.dll', 'PresentationFramework.dll', 'WindowsBase.dll', 'System.Xaml.dll'):
        reference = WPF / assembly
        command.append('/r:' + (str(reference) if reference.is_file() else assembly))
    command += ['/r:System.dll', '/r:System.Web.Extensions.dll']
    for name in ('Unbounded.ttf', 'Onest-Cyr.ttf', 'Onest-Lat.ttf'):
        command.append('/resource:' + str(UI / 'fonts' / name) + ',fonts.' + name)
    for name in ('header.png', 'mark.png'):
        command.append('/resource:' + str(UI / 'assets' / name) + ',assets.' + name)
    command += [str(source), str(harness)]
    built = subprocess.run(command, capture_output=True, timeout=60)
    (work / 'compile.log').write_bytes(built.stdout + built.stderr)
    if built.returncode:
        print((built.stdout + built.stderr).decode('cp866', errors='replace'))
        return built.returncode
    done = subprocess.run([str(executable), str(result)], capture_output=True, timeout=30,
                          creationflags=subprocess.CREATE_NO_WINDOW)
    (work / 'run.log').write_bytes(done.stdout + done.stderr)
    print((done.stdout + done.stderr).decode('utf-8', errors='replace'))
    proof = {'source_file': 'installer/ui/WXQSetupUI.cs', 'source_sha256': sha(source),
             'source_changed_during_run': sha(production) != sha(source),
             'harness_sha256': sha(harness), 'executable_sha256': sha(executable),
             'compiler_sha256': sha(CSC), 'exit_code': done.returncode,
             'no_window_created': True, 'work': str(work)}
    if result.is_file():
        proof['result_sha256'] = sha(result)
        proof['result'] = json.loads(result.read_text(encoding='utf-8'))
    (work / 'proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: proof[k] for k in ('source_sha256', 'exit_code', 'work')}, ensure_ascii=False))
    return done.returncode


if __name__ == '__main__':
    raise SystemExit(main())
