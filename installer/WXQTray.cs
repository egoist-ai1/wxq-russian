// WXQTray.exe - значок русификатора в области уведомлений. Права администратора не нужны и не запрашиваются.
//
// Что делает:
//   * показывает состояние автообновления из update-status.json (пишет только помощник WXQTool.exe update, от SYSTEM);
//   * пункт «Проверить обновление» просит планировщик запустить задачу «Egoist\WXQ-Russian Update» (schtasks /run; право на запуск у обычных
//     пользователей выдано дескриптором безопасности задачи, повышать ничего не надо);
//   * уведомления: «Перевод обновлён до X» и «Обновление ждёт закрытия игры»;
//   * следит за игрой и лаунчером: когда они закрылись, а обновление отложено (state=deferred), повторно просит задачу выполниться;
//   * когда установщик заменил WXQTray.exe новой версией, запускает её и выходит.
// Сам ничего не скачивает, в игру и в каталог данных не пишет. Автозапуск - запись HKLM\...\Run (ставит установщик).
//
// Сборка: csc /target:winexe /platform:x64 /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Web.Extensions.dll WXQTray.cs
// (build_installer.py --update-repo ...). .NET Framework 4, C# 5.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace Egoist.WXQ
{
    static class TrayProgram
    {
        [STAThread]
        static void Main(string[] argv)
        {
            bool restarted = argv.Length > 0 && argv[0] == "--restarted";
            bool created;
            // Один значок на сеанс. Новая версия, запущенная прежней при замене, ждёт, пока та выйдет.
            using (var mutex = new Mutex(false, "Local\\Egoist.WXQRussian.Tray", out created))
            {
                bool owned;
                try { owned = mutex.WaitOne(restarted ? 20000 : 0); }
                catch (AbandonedMutexException) { owned = true; }
                if (!owned) return;
                try
                {
                    Application.EnableVisualStyles();
                    Application.SetCompatibleTextRenderingDefault(false);
                    using (var tray = new TrayApp()) Application.Run();
                }
                finally { try { mutex.ReleaseMutex(); } catch (ApplicationException) { } }
            }
        }
    }

    class TrayApp : IDisposable
    {
        const string TaskName = "Egoist\\WXQ-Russian Update";
        static readonly Regex GameProcess = new Regex("^(WXQ|WXQLauncher.*|HonorofKingsACE|王者万象棋)$", RegexOptions.IgnoreCase);

        readonly NotifyIcon icon = new NotifyIcon();
        readonly ToolStripMenuItem statusItem = new ToolStripMenuItem("Обновление: проверка не выполнялась") { Enabled = false };
        readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
        readonly string self = Application.ExecutablePath;
        readonly DateTime selfStamp;
        readonly string statusFile;
        string seenKey = "";
        string notifiedDeferred = "";
        bool gameWasRunning;
        DateTime lastRequest = DateTime.MinValue;
        bool firstRead = true;

        public TrayApp()
        {
            selfStamp = File.GetLastWriteTimeUtc(self);
            statusFile = Path.Combine(DataDir(), "update-status.json");
            CleanOld();
            var menu = new ContextMenuStrip();
            menu.Items.Add(statusItem);
            menu.Items.Add(new ToolStripSeparator());
            var check = new ToolStripMenuItem("Проверить обновление");
            check.Click += delegate { RequestRun(true); };
            menu.Items.Add(check);
            var exit = new ToolStripMenuItem("Закрыть значок");
            exit.Click += delegate { Application.Exit(); };
            menu.Items.Add(exit);
            icon.ContextMenuStrip = menu;
            try { icon.Icon = Icon.ExtractAssociatedIcon(self); }
            catch (Exception) { icon.Icon = SystemIcons.Application; }
            icon.Text = "Русификатор Honor of Kings ACE";
            icon.Visible = true;
            icon.DoubleClick += delegate { RequestRun(true); };
            timer.Interval = 5000;
            timer.Tick += delegate { Tick(); };
            timer.Start();
            gameWasRunning = GameRunning();
            Tick();
        }

        public void Dispose()
        {
            timer.Stop(); timer.Dispose();
            icon.Visible = false; icon.Dispose();
        }

        // Каталог данных: путь запоминает установщик в datadir.txt рядом с этим exe; по умолчанию - ProgramData.
        static string DataDir()
        {
            try
            {
                string file = Path.Combine(Path.GetDirectoryName(Application.ExecutablePath), "datadir.txt");
                if (File.Exists(file))
                {
                    foreach (string line in File.ReadAllLines(file))
                        if (line.Trim().Length > 0) return line.Trim();
                }
            }
            catch (Exception) { }
            return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Egoist", "WXQ-Russian");
        }

        // Установщик обновления переименовывает запущенные WXQTool.exe и WXQTray.exe в *.old; когда рядом уже лежит новый файл, старый удаляем.
        void CleanOld()
        {
            try
            {
                string dir = Path.GetDirectoryName(self);
                foreach (string name in new[] { "WXQTool.exe", "WXQTray.exe" })
                {
                    string old = Path.Combine(dir, name + ".old");
                    if (File.Exists(old) && File.Exists(Path.Combine(dir, name))) { try { File.Delete(old); } catch (Exception) { } }
                }
            }
            catch (Exception) { }
        }

        static bool GameRunning()
        {
            foreach (var process in Process.GetProcesses())
            {
                try { if (GameProcess.IsMatch(process.ProcessName)) return true; }
                catch (Exception) { }
                finally { process.Dispose(); }
            }
            return false;
        }

        // Просит планировщик выполнить задачу. Сам процесс обновления идёт от SYSTEM; здесь только запуск.
        void RequestRun(bool manual)
        {
            if (!manual && (DateTime.UtcNow - lastRequest).TotalSeconds < 60) return;
            lastRequest = DateTime.UtcNow;
            try
            {
                var info = new ProcessStartInfo("schtasks.exe", "/run /tn \"" + TaskName + "\"") { CreateNoWindow = true, UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true };
                using (var process = Process.Start(info))
                {
                    process.WaitForExit(15000);
                    if (manual)
                    {
                        if (process.HasExited && process.ExitCode == 0) Balloon("Проверяю обновление…", "Результат появится здесь через несколько секунд.", ToolTipIcon.Info);
                        else Balloon("Не удалось запустить проверку", "Задача обновления не найдена или недоступна. Переустановите русификатор.", ToolTipIcon.Warning);
                    }
                }
            }
            catch (Exception)
            {
                if (manual) Balloon("Не удалось запустить проверку", "Планировщик заданий недоступен.", ToolTipIcon.Warning);
            }
        }

        void Balloon(string title, string text, ToolTipIcon kind)
        {
            try { icon.ShowBalloonTip(8000, title, text, kind); } catch (Exception) { }
        }

        static string Text(Dictionary<string, object> row, string key)
        {
            object value;
            return row != null && row.TryGetValue(key, out value) && value != null ? Convert.ToString(value, CultureInfo.InvariantCulture) : "";
        }

        Dictionary<string, object> ReadStatus()
        {
            try
            {
                if (!File.Exists(statusFile)) return null;
                string text;
                using (var stream = new FileStream(statusFile, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var reader = new StreamReader(stream, System.Text.Encoding.UTF8)) text = reader.ReadToEnd();
                return new JavaScriptSerializer().DeserializeObject(text.TrimStart('﻿')) as Dictionary<string, object>;
            }
            catch (Exception) { return null; }
        }

        void Tick()
        {
            // Заменили ли exe новой версией (установщик переименовал запущенный файл в .old и положил новый на то же место).
            try
            {
                if (File.Exists(self) && File.GetLastWriteTimeUtc(self) != selfStamp)
                {
                    Process.Start(new ProcessStartInfo(self, "--restarted") { UseShellExecute = false });
                    Application.Exit();
                    return;
                }
            }
            catch (Exception) { }

            bool running = GameRunning();
            var status = ReadStatus();
            string state = Text(status, "state");
            if (gameWasRunning && !running && state == "deferred") RequestRun(false);
            gameWasRunning = running;
            if (status == null) return;

            string key = state + "|" + Text(status, "installed") + "|" + Text(status, "available") + "|" + Text(status, "code") + "|" + Text(status, "updated_utc");
            string line = Describe(status, state);
            statusItem.Text = line.Length > 80 ? line.Substring(0, 77) + "…" : line;
            icon.Text = ("Русификатор: " + line).Length > 62 ? ("Русификатор: " + line).Substring(0, 62) : "Русификатор: " + line;
            if (key == seenKey) return;
            seenKey = key;
            if (firstRead) { firstRead = false; if (state != "deferred") return; }   // старый итог при запуске значка не показываем
            firstRead = false;
            string available = Text(status, "available"), installed = Text(status, "installed");
            if (state == "done") Balloon("Перевод обновлён", "Перевод обновлён до " + installed + ".", ToolTipIcon.Info);
            else if (state == "deferred" && notifiedDeferred != available)
            {
                notifiedDeferred = available;
                Balloon("Обновление ждёт закрытия игры", "Версия " + available + " установится, когда вы закроете игру и лаунчер.", ToolTipIcon.Info);
            }
            else if (state == "ready" && available.Length > 0) Balloon("Доступно обновление", "Версия " + available + ".", ToolTipIcon.Info);
            else if (state == "failed" && Text(status, "code") != "network" && Text(status, "code") != "retry_later" && Text(status, "code") != "not_configured")
                Balloon("Обновление не установилось", Text(status, "message"), ToolTipIcon.Warning);
        }

        static string Describe(Dictionary<string, object> status, string state)
        {
            switch (state)
            {
                case "checking": return "проверяю обновление";
                case "downloading": return "скачиваю версию " + Text(status, "available");
                case "applying": return "устанавливаю версию " + Text(status, "available");
                case "ready": return "доступна версия " + Text(status, "available");
                case "deferred": return "версия " + Text(status, "available") + " ждёт закрытия игры";
                case "done": return "перевод обновлён до " + Text(status, "installed");
                case "failed": return Text(status, "message").Length > 0 ? Text(status, "message") : "ошибка обновления";
                case "idle":
                    return Text(status, "code") == "up_to_date" ? "установлена последняя версия " + Text(status, "installed") : (Text(status, "message").Length > 0 ? Text(status, "message") : "обновлений нет");
                default: return "ожидание";
            }
        }
    }
}
