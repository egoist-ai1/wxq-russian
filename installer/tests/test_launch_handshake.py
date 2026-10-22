"""Регрессия UI → launch → ответ установщика без игры, UAC и desktop input.

    python -X utf8 -B installer/tests/test_launch_handshake.py [--work <Temp>]

Настоящий UI собирается с отдельной STA entry point. Каждый harness EXE
запускается только через isolated_test_run.py: private desktop и kill-on-close
Job до ResumeThread. Inno не запускается: ответы родителя пишет тестовая fixture.
Для RED можно передать сохранённые исходники через --ui-source/--inno-source.
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
RUNNER = PROJECT / 'installer/tests/isolated_test_run.py'
CSC = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')
WPF = CSC.parent / 'WPF'
CASES = ('protocol', 'wait-for-quit', 'write-failure', 'parent-failure', 'uia-invoke')

HARNESS = r'''
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Automation.Peers;
using System.Windows.Automation.Provider;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using WXQSetup;

static class LaunchHandshakeHarness
{
    static readonly List<object> results = new List<object>();
    static readonly JavaScriptSerializer json = new JavaScriptSerializer();
    static int failures;

    // Только чтение состояния фокуса собственного UI-потока, без input/activation.
    [DllImport("user32.dll")] static extern IntPtr GetFocus();
    [DllImport("user32.dll")] static extern IntPtr GetActiveWindow();

    static void Check(string name, bool passed, string detail)
    {
        results.Add(new { name = name, passed = passed, detail = detail });
        Console.WriteLine((passed ? "PASS " : "FAIL ") + name + ": " + detail);
        if (!passed) failures++;
    }

    static string Field(UiState state, string name)
    {
        var field = typeof(UiState).GetField(name);
        return field == null ? "<missing>" : Convert.ToString(field.GetValue(state));
    }

    static string State(int revision, string launchState, string launchMessage)
    {
        var state = new Dictionary<string, object> {
            { "rev", revision }, { "screen", "done" }, { "state", "finished" },
            { "has_launcher_shortcut", true }, { "launcher_installed", true }
        };
        if (launchState != null) state.Add("launch_state", launchState);
        if (launchMessage != null) state.Add("launch_message", launchMessage);
        return json.Serialize(state);
    }

    static bool Apply(UiState state, ref long revision, string text)
    {
        string screen, signature;
        return Proto.TryApply(text, state, ref revision, out screen, out signature);
    }

    static string VisibleText(DependencyObject node)
    {
        var element = node as UIElement;
        if (element != null && element.Visibility != Visibility.Visible) return "";
        var value = new StringBuilder();
        var text = node as TextBlock;
        if (text != null) value.AppendLine(text.Text);
        for (int i = 0; i < VisualTreeHelper.GetChildrenCount(node); i++)
            value.Append(VisibleText(VisualTreeHelper.GetChild(node, i)));
        return value.ToString();
    }

    static int Count(string path, string command)
    {
        if (!File.Exists(path)) return 0;
        int count = 0;
        foreach (string line in File.ReadAllLines(path))
        {
            var item = json.DeserializeObject(line) as Dictionary<string, object>;
            if (item != null && Convert.ToString(item["cmd"]) == command) count++;
        }
        return count;
    }

    static void Protocol()
    {
        var state = new UiState();
        long revision = 0;
        Check("launch fields default to none/empty", Field(state, "LaunchState") == "none" && Field(state, "LaunchMessage") == "",
            Field(state, "LaunchState") + "/" + Field(state, "LaunchMessage"));
        bool optional = Apply(state, ref revision, State(1, null, null));
        Check("state without launch fields remains compatible", optional && Field(state, "LaunchState") == "none" && Field(state, "LaunchMessage") == "", "parsed=" + optional);
        bool starting = Apply(state, ref revision, State(2, "starting", "Открываю лаунчер"));
        Check("starting blocks launch and permits close", starting && Field(state, "LaunchState") == "starting" && !Proto.CanSend(state, "launch") && Proto.CanSend(state, "close"), Field(state, "LaunchState"));
        bool failed = Apply(state, ref revision, State(3, "failed", "Тестовая причина 740"));
        Check("failed state preserves reason and permits retry", failed && Field(state, "LaunchState") == "failed" && Field(state, "LaunchMessage") == "Тестовая причина 740" && Proto.CanSend(state, "launch"), Field(state, "LaunchState") + "/" + Field(state, "LaunchMessage"));
        bool cleared = Apply(state, ref revision, State(4, null, null));
        Check("optional fields reset after prior launch result", cleared && Field(state, "LaunchState") == "none" && Field(state, "LaunchMessage") == "", Field(state, "LaunchState"));

        var commands = new List<string>();
        var view = new MainView();
        view.S.State = "finished";
        view.Show("done");
        view.Command += delegate(string command) { commands.Add(command); };
        view.Do("launch"); view.Do("launch");
        Check("MainView suppresses repeated launch before parent response", commands.Count == 1 && commands[0] == "launch", "commands=" + string.Join(",", commands));
        view.Do("close");
        Check("MainView close remains available while launch is pending", commands.FindAll(delegate(string command) { return command == "close"; }).Count == 1, "commands=" + string.Join(",", commands));
    }

    static void LiveCase(Application app, string kind, string dir)
    {
        string statePath = Path.Combine(dir, "state.json");
        string commandPath = Path.Combine(dir, "commands.jsonl");
        File.WriteAllText(statePath, State(1, null, null), new UTF8Encoding(false));
        if (kind == "write-failure") Directory.CreateDirectory(commandPath);
        int phase = 0;
        long phaseAt = 0;
        bool acknowledged = false;
        string failureReason = "Тест: Windows отклонила запуск (740).";
        var elapsed = Stopwatch.StartNew();
        var timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(100) };
        timer.Tick += delegate
        {
            if (elapsed.ElapsedMilliseconds > 9000)
            {
                Check("scenario finishes within deadline", false, kind);
                timer.Stop(); app.Shutdown(70); return;
            }
            var window = app.MainWindow as SetupWindow;
            if (window == null || !window.IsVisible || window.View.Current != "done") return;
            var view = window.View;
            if (phase == 0)
            {
                phaseAt = elapsed.ElapsedMilliseconds;
                view.Do("launch");
                if (kind == "write-failure")
                {
                    string text = VisibleText(view);
                    bool reason = text.Contains("Не удалось") || text.Contains("Действие не началось");
                    bool retry = text.Contains("Повтор") || text.Contains("Нажмите ещё");
                    Check("failed command write exposes visible reason and retry", reason && retry, text.Replace('\n', ' '));
                    Directory.Delete(commandPath);
                    view.Do("launch");
                    Check("failed command write allows a successful retry", Count(commandPath, "launch") == 1, "launches=" + Count(commandPath, "launch"));
                }
                else
                {
                    view.Do("launch");
                    Check("Live writes only one pending launch", Count(commandPath, "launch") == 1, "launches=" + Count(commandPath, "launch"));
                }
                if (kind == "parent-failure") File.WriteAllText(statePath, State(2, "starting", "Открываю лаунчер"), new UTF8Encoding(false));
                phase = 1;
                return;
            }
            if (kind == "parent-failure" && phase == 1 && elapsed.ElapsedMilliseconds - phaseAt >= 300)
            {
                view.Do("launch");
                Check("parent starting response keeps duplicate launch blocked", Field(view.S, "LaunchState") == "starting" && Count(commandPath, "launch") == 1, "state=" + Field(view.S, "LaunchState") + "; launches=" + Count(commandPath, "launch"));
                File.WriteAllText(statePath, State(3, "failed", failureReason), new UTF8Encoding(false));
                phaseAt = elapsed.ElapsedMilliseconds; phase = 2;
                return;
            }
            if (kind == "parent-failure" && phase == 2 && elapsed.ElapsedMilliseconds - phaseAt >= 300)
            {
                string text = VisibleText(view);
                Check("parent launch failure remains on done with visible reason", view.Current == "done" && text.Contains(failureReason), text.Replace('\n', ' '));
                view.Do("launch"); view.Do("launch");
                Check("failed parent response releases pending for one retry", Count(commandPath, "launch") == 2, "launches=" + Count(commandPath, "launch"));
            }
            else if (kind != "parent-failure" && phase == 1 && elapsed.ElapsedMilliseconds - phaseAt >= 1800)
            {
                Check("Live remains open beyond old 1200ms launch timeout", window.IsVisible, "elapsed_since_launch_ms=" + (elapsed.ElapsedMilliseconds - phaseAt));
            }
            else return;

            if (kind != "wait-for-quit")
            {
                view.Do("close");
                Check("Live forwards close after pending launch", Count(commandPath, "close") == 1, "closes=" + Count(commandPath, "close"));
            }
            acknowledged = true;
            File.WriteAllText(statePath, "{\"rev\":4,\"screen\":\"quit\"}", new UTF8Encoding(false));
            phase = 3; timer.Stop();
        };
        timer.Start();
        int exit = Live.Run(app, dir, "LaunchHandshakeFixture", Process.GetCurrentProcess().Id);
        timer.Stop();
        Check("Live waits for parent quit acknowledgement", acknowledged && phase == 3, "ack=" + acknowledged + "; phase=" + phase + "; exit=" + exit);
        Check("Live exits cleanly after fixture quit", exit == 0, "exit=" + exit);
    }

    static void DrainDispatcher()
    {
        var frame = new DispatcherFrame();
        Dispatcher.CurrentDispatcher.BeginInvoke(DispatcherPriority.ContextIdle, new Action(delegate { frame.Continue = false; }));
        Dispatcher.PushFrame(frame);
    }

    static void UiaInvoke()
    {
        int clicks = 0;
        var button = new Btn(BK.Primary, "Тест запуска", "play");
        button.Click += delegate { clicks++; };
        var window = new Window {
            Content = button, Width = 320, Height = 180,
            ShowActivated = false, ShowInTaskbar = false
        };
        try
        {
            window.Show();
            DrainDispatcher();
            var peer = UIElementAutomationPeer.CreatePeerForElement(button);
            Check("Btn creates an automation peer", peer != null, peer == null ? "null" : peer.GetType().Name);
            if (peer == null) return;
            Check("Btn peer exposes Button control type", peer.GetAutomationControlType() == AutomationControlType.Button, peer.GetAutomationControlType().ToString());
            Check("Btn peer preserves accessible Name", peer.GetName() == "Тест запуска", peer.GetName());
            var invoke = peer.GetPattern(PatternInterface.Invoke) as IInvokeProvider;
            Check("Btn peer exposes IInvokeProvider", invoke != null, invoke == null ? "null" : "IInvokeProvider");
            if (invoke == null) return;

            var keyboardBefore = Keyboard.FocusedElement;
            var logicalBefore = FocusManager.GetFocusedElement(window);
            IntPtr nativeBefore = GetFocus(), activeBefore = GetActiveWindow();
            Func<bool> sameFocus = delegate {
                return Object.ReferenceEquals(keyboardBefore, Keyboard.FocusedElement)
                    && Object.ReferenceEquals(logicalBefore, FocusManager.GetFocusedElement(window))
                    && nativeBefore == GetFocus() && activeBefore == GetActiveWindow();
            };
            invoke.Invoke();
            int beforeDispatch = clicks;
            DrainDispatcher();
            Check("visible enabled Invoke dispatches the same Click exactly once", button.IsVisible && button.IsEnabled && beforeDispatch == 0 && clicks == 1, "before_dispatch=" + beforeDispatch + "; clicks=" + clicks);
            Check("Invoke preserves keyboard logical and native focus", sameFocus() && !window.IsActive, "same_focus=" + sameFocus() + "; window_active=" + window.IsActive);

            button.IsEnabledBtn = false;
            bool rejected = false;
            try { invoke.Invoke(); }
            catch (ElementNotEnabledException) { rejected = true; }
            DrainDispatcher();
            Check("disabled Invoke throws and never dispatches Click", !button.IsEnabled && !peer.IsEnabled() && rejected && clicks == 1, "actual_enabled=" + button.IsEnabled + "; peer_enabled=" + peer.IsEnabled() + "; rejected=" + rejected + "; clicks=" + clicks);

            button.IsEnabledBtn = true;
            invoke.Invoke();
            button.IsEnabledBtn = false;
            DrainDispatcher();
            Check("queued Invoke is cancelled when the button becomes disabled", clicks == 1, "clicks=" + clicks);

            button.IsEnabledBtn = true;
            invoke.Invoke();
            DrainDispatcher();
            Check("re-enabled Invoke restores the same Click handler", button.IsEnabled && peer.IsEnabled() && clicks == 2, "actual_enabled=" + button.IsEnabled + "; clicks=" + clicks);
            Check("all Invoke paths preserve focus without activating the window", sameFocus() && !window.IsActive, "same_focus=" + sameFocus() + "; window_active=" + window.IsActive);
        }
        finally { window.Close(); }
    }

    [STAThread]
    static int Main(string[] args)
    {
        // Эта entry point принимает только имя сценария и выделенный Temp-каталог.
        // Production Main не вызывается; внешние программы тест не запускает.
        if (args.Length != 2 || !Directory.Exists(args[1])) return 2;
        string kind = args[0], dir = args[1];
        if (kind != "protocol" && kind != "wait-for-quit" && kind != "write-failure" && kind != "parent-failure" && kind != "uia-invoke") return 2;
        var app = new Application { ShutdownMode = kind == "protocol" || kind == "uia-invoke" ? ShutdownMode.OnExplicitShutdown : ShutdownMode.OnMainWindowClose };
        Tk.Still = true;
        Tk.LoadFonts(Path.Combine(dir, "fonts"));
        try
        {
            if (kind == "protocol") Protocol();
            else if (kind == "uia-invoke") UiaInvoke();
            else LiveCase(app, kind, dir);
        }
        catch (Exception error) { Check("unhandled fixture exception", false, error.ToString()); }
        File.WriteAllText(Path.Combine(dir, "result.json"), json.Serialize(new { passed = failures == 0, failed = failures, checks = results }), new UTF8Encoding(false));
        return failures == 0 ? 0 : 1;
    }
}
'''


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path)
    parser.add_argument('--ui-source', type=Path, default=UI / 'WXQSetupUI.cs')
    parser.add_argument('--inno-source', type=Path, default=PROJECT / 'installer/WXQRussian.iss')
    parser.add_argument('--report', type=Path)
    options = parser.parse_args()
    work = (options.work or Path(tempfile.mkdtemp(prefix='wxq-launch-handshake-'))).resolve()
    if not work.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        parser.error('--work must stay in Temp')
    work.mkdir(parents=True, exist_ok=True)
    source, inno = work / 'WXQSetupUI.cs', work / 'WXQRussian.iss'
    source.write_bytes(options.ui_source.read_bytes())
    inno.write_bytes(options.inno_source.read_bytes())
    harness = work / 'LaunchHandshakeHarness.cs'
    harness.write_text(HARNESS, encoding='utf-8')
    executable = work / 'LaunchHandshakeHarness.exe'
    command = [str(CSC), '/nologo', '/target:exe', '/main:LaunchHandshakeHarness',
               '/platform:anycpu', '/optimize+', '/codepage:65001',
               f'/out:{executable}', f'/lib:{WPF}']
    for assembly in ('PresentationCore.dll', 'PresentationFramework.dll', 'WindowsBase.dll',
                     'System.Xaml.dll', 'UIAutomationProvider.dll', 'UIAutomationTypes.dll'):
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

    cases = []
    for kind in CASES:
        case_dir = work / kind
        case_dir.mkdir()
        isolation_dir = work / (kind + '-isolation')
        done = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(RUNNER),
                               '--work', str(isolation_dir), '--timeout', '20', '--',
                               str(executable), kind, str(case_dir)],
                              capture_output=True, timeout=30)
        (work / (kind + '-runner.log')).write_bytes(done.stdout + done.stderr)
        result_file, isolation_file = case_dir / 'result.json', isolation_dir / 'isolation.json'
        result = json.loads(result_file.read_text(encoding='utf-8')) if result_file.is_file() else None
        isolation = json.loads(isolation_file.read_text(encoding='utf-8')) if isolation_file.is_file() else {}
        safe = (isolation.get('job_assigned_before_resume') is True and
                isolation.get('job_kill_on_close') is True and
                isolation.get('switch_desktop_called') is False and
                bool(isolation.get('private_desktop')))
        passed = done.returncode == 0 and result is not None and result['passed'] and safe
        cases.append({'name': kind, 'passed': passed, 'exit_code': done.returncode,
                      'result': result, 'isolation': isolation})
        print(('PASS ' if passed else 'FAIL ') + kind)
        if result is not None:
            for check in result['checks']:
                if not check['passed']:
                    print('  ' + check['name'] + ': ' + check['detail'])
        else:
            print((done.stdout + done.stderr).decode('utf-8', errors='replace'))

    proof = {'passed': all(case['passed'] for case in cases), 'work': str(work),
             'ui_source': str(options.ui_source), 'ui_source_sha256': sha(source),
             'inno_source': str(options.inno_source), 'inno_source_sha256': sha(inno),
             'harness_sha256': sha(harness), 'executable_sha256': sha(executable),
             'compiler_sha256': sha(CSC), 'runner_sha256': sha(RUNNER),
             'inno_execution': False,
             'boundary': 'Real UI Live.Run, protocol and Btn automation peer; fake parent state only; no game, launcher or UAC. UIA provider is invoked in-process; no external UIA client.',
             'cases': cases}
    report = json.dumps(proof, ensure_ascii=False, indent=2) + '\n'
    (work / 'proof.json').write_text(report, encoding='utf-8')
    if options.report:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(report, encoding='utf-8')
    print(json.dumps({'passed': proof['passed'], 'work': str(work)}, ensure_ascii=False))
    return 0 if proof['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
