// WXQTray.exe - значок русификатора в области уведомлений. Права администратора не нужны и не запрашиваются.
//
// Что делает:
//   * показывает состояние автообновления из update-status.json (пишет только помощник WXQTool.exe update, от SYSTEM);
//   * пункт «Проверить обновление» просит планировщик запустить задачу «Egoist\WXQ-Russian Update» (schtasks /run; право на запуск у обычных
//     пользователей выдано дескриптором безопасности задачи, повышать ничего не надо);
//   * уведомления: «Перевод обновлён до X» и «Обновление ждёт закрытия игры»;
//   * двойной щелчок по значку открывает окно состояния (версии перевода и игры, последняя проверка, итог, кнопки «Проверить сейчас», «Журнал»,
//     «Страница релизов»); все тексты статусов - в таблице CodeText, технических кодов пользователь не видит;
//   * следит за игрой и лаунчером: когда они закрылись, просит задачу выполниться (не чаще раза в 10 минут, при любом состоянии, не только
//     при отложенном обновлении): после обновления игры именно закрытие игры возвращает перевод;
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
        const string ReleasesUrl = "https://github.com/egoist-ai1/wxq-russian/releases";
        // Ограничение, о котором владелец просил сказать прямо (то же слово в слово в установщике, README и CHANGELOG).
        public const string UpdateLimit = "После выхода обновления игры первый запуск будет на китайском: перевод вернётся автоматически после того, как вы один раз запустите игру до главного меню и закроете её. Иначе нельзя — новые хеши файлов игры создаются только самой игрой.";

        string seenKey = "";
        string notifiedDeferred = "";
        string notifiedGameChanged = "";
        bool gameWasRunning;
        DateTime lastRequest = DateTime.MinValue;
        DateTime lastCloseRun = DateTime.MinValue;
        bool firstRead = true;
        StatusForm form;

        public TrayApp()
        {
            selfStamp = File.GetLastWriteTimeUtc(self);
            statusFile = Path.Combine(DataDir(), "update-status.json");
            CleanOld();
            var menu = new ContextMenuStrip();
            menu.Items.Add(statusItem);
            menu.Items.Add(new ToolStripSeparator());
            var show = new ToolStripMenuItem("Состояние…");
            show.Click += delegate { ShowStatus(); };
            menu.Items.Add(show);
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
            icon.DoubleClick += delegate { ShowStatus(); };
            timer.Interval = 5000;
            timer.Tick += delegate { Tick(); };
            timer.Start();
            gameWasRunning = GameRunning();
            Tick();
        }

        public void Dispose()
        {
            timer.Stop(); timer.Dispose();
            if (form != null) { form.Allow = true; form.Close(); form.Dispose(); }
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
            // Игра закрылась: просим задачу выполниться. Не чаще раза в 10 минут и при любом состоянии - после обновления игры
            // перевод возвращается именно после её первого запуска и закрытия, а не по расписанию.
            if (gameWasRunning && !running && (DateTime.UtcNow - lastCloseRun).TotalMinutes >= 10)
            {
                lastCloseRun = DateTime.UtcNow;
                RequestRun(false);
            }
            gameWasRunning = running;
            if (status == null) { if (form != null && form.Visible) form.Fill("", "", "", "Проверка ещё не выполнялась."); return; }

            string key = state + "|" + Text(status, "installed") + "|" + Text(status, "available") + "|" + Text(status, "code") + "|" + Text(status, "updated_utc");
            string line = Describe(status, state);
            statusItem.Text = line.Length > 80 ? line.Substring(0, 77) + "…" : line;
            icon.Text = ("Русификатор: " + line).Length > 62 ? ("Русификатор: " + line).Substring(0, 62) : "Русификатор: " + line;
            if (form != null && form.Visible) FillForm(status, state);
            if (key == seenKey) return;
            seenKey = key;
            string code = Text(status, "code");
            bool urgent = state == "deferred";
            if (firstRead) { firstRead = false; if (!urgent) return; }   // старый итог при запуске значка не показываем
            firstRead = false;
            string available = Text(status, "available"), installed = Text(status, "installed");
            if (state == "done" && code == "reapplied") Balloon("Перевод возвращён", "Игра обновилась, перевод снова на месте.", ToolTipIcon.Info);
            else if (state == "done") Balloon("Перевод обновлён", "Перевод обновлён до " + installed + ".", ToolTipIcon.Info);
            else if (state == "deferred" && code == "game_changed")
            {
                string gv = Text(status, "game_version");
                if (notifiedGameChanged != gv)
                {
                    notifiedGameChanged = gv;
                    Balloon("Игра обновилась", "Запустите её один раз до меню и закройте — перевод вернётся сам.", ToolTipIcon.Info);
                }
            }
            else if (state == "deferred" && code == "game_old") { /* тихо: это не действие пользователя, текст виден в окне состояния */ }
            else if (state == "deferred" && notifiedDeferred != available)
            {
                notifiedDeferred = available;
                Balloon("Обновление ждёт закрытия игры", "Версия " + available + " установится, когда вы закроете игру и лаунчер.", ToolTipIcon.Info);
            }
            else if (state == "ready" && available.Length > 0) Balloon("Доступно обновление", "Версия " + available + ".", ToolTipIcon.Info);
            else if (state == "failed" && code != "network" && code != "retry_later" && code != "not_configured")
                Balloon(code == "rolled_back" ? "Обновление отменено" : "Обновление не установилось", Describe(status, state), ToolTipIcon.Warning);
        }

        // ---- тексты статусов ----

        // Единая таблица: код статуса -> то, что видит человек. Подстановки: {a} - доступная версия, {i} - установленная, {g} - версия игры.
        // Кодов и технических слов (sha_mismatch и т. п.) в видимом тексте нет.
        static readonly Dictionary<string, string> CodeText = new Dictionary<string, string>
        {
            { "up_to_date", "Установлена последняя версия перевода: {i}." },
            { "no_release", "Опубликованных обновлений пока нет." },
            { "not_installed", "Перевод не установлен." },
            { "not_configured", "В этой сборке не настроены обновления." },
            { "game_changed", "Игра обновилась. Запустите её один раз до меню и закройте — перевод вернётся сам." },
            { "game_old", "Версия {a} ждёт обновления игры. Пока работает версия {i}." },
            { "game_running", "Обновление ждёт, когда вы закроете игру и лаунчер." },
            { "setup_running", "Обновление ждёт: сейчас идёт установка перевода." },
            { "busy_running", "Обновление ждёт: русификатор занят другой операцией." },
            { "reapplied", "Игра обновилась, перевод возвращён." },
            { "rolled_back", "Новая версия не встала. Возвращена прежняя версия {i}, перевод работает." },
            { "av_blocked", "Антивирус, похоже, убрал файл обновления в карантин. Восстановите его или добавьте папку русификатора в исключения и нажмите «Проверить сейчас»." },
            { "setup_failed", "Установка обновления не удалась. Подробности в журнале." },
            { "setup_timeout", "Установщик обновления работает слишком долго. Подробности в журнале." },
            { "sha_mismatch", "Скачанный файл не прошёл проверку подлинности и удалён. Попробую позже." },
            { "bad_signature", "Обновление не прошло проверку подписи и не применено." },
            { "replay", "Обновление не прошло проверку: оно старше уже принятого." },
            { "downgrade", "Опубликована версия ниже установленной. Ничего не меняю." },
            { "updater_too_old", "Для обновления нужна новая версия русификатора. Скачайте установщик со страницы релизов." },
            { "network", "Нет связи с сервером обновлений. Попробую позже." },
            { "retry_later", "Прошлая попытка не удалась. Повторю позже." },
            { "no_installer", "Нет сохранённого установщика. Скачайте его со страницы релизов." },
            { "disk_space", "Не хватает места на диске для обновления." },
            { "acl", "Папка обновления небезопасна, обновление остановлено. Подробности в журнале." },
            { "version", "Эта версия перевода не подходит к установленной игре. Ждите обновления." },
            { "reinstall", "Остались файлы прежней установки. Запустите установщик и нажмите «Удалить перевод»." },
            { "cache_missing", "Игра ещё не создала свои файлы. Запустите её один раз до меню и закройте." },
            { "busy", "Обновление уже выполняется." }
        };

        static string Fill(string template, Dictionary<string, object> status)
        {
            return template.Replace("{a}", Text(status, "available")).Replace("{i}", Text(status, "installed")).Replace("{g}", Text(status, "game_version"));
        }

        static string Describe(Dictionary<string, object> status, string state)
        {
            string code = Text(status, "code"), text;
            if (code.Length > 0 && CodeText.TryGetValue(code, out text)) return Fill(text, status);
            switch (state)
            {
                case "checking": return "Проверяю обновление.";
                case "downloading": return "Скачиваю версию " + Text(status, "available") + ".";
                case "applying": return "Устанавливаю версию " + Text(status, "available") + ".";
                case "ready": return "Доступна версия " + Text(status, "available") + ".";
                case "deferred": return "Версия " + Text(status, "available") + " ждёт закрытия игры.";
                case "done": return "Перевод обновлён до " + Text(status, "installed") + ".";
                case "failed": return "Обновление не удалось. Подробности в журнале.";
                case "idle": return "Обновлений нет.";
                default: return "Ожидание.";
            }
        }

        // ---- окно состояния ----

        string DataFile(string name) { return Path.Combine(DataDir(), name); }

        void ShowStatus()
        {
            if (form == null)
            {
                form = new StatusForm();
                form.CheckNow = delegate { RequestRun(true); };
                form.OpenLog = delegate { OpenLog(); };
                form.OpenReleases = delegate { OpenUrl(ReleasesUrl); };
            }
            var status = ReadStatus();
            if (status == null) form.Fill("", "", "", "Проверка ещё не выполнялась.");
            else FillForm(status, Text(status, "state"));
            if (!form.Visible) form.Show();
            form.WindowState = FormWindowState.Normal;
            form.Activate();
        }

        void FillForm(Dictionary<string, object> status, string state)
        {
            string last = "";
            DateTime when;
            if (DateTime.TryParse(Text(status, "updated_utc"), CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out when))
                last = when.ToLocalTime().ToString("dd.MM.yyyy HH:mm", CultureInfo.CurrentCulture);
            form.Fill(Text(status, "installed"), Text(status, "game_version"), last, Describe(status, state));
        }

        void OpenLog()
        {
            try
            {
                string log = DataFile("install.log");
                if (File.Exists(log)) Process.Start(new ProcessStartInfo("notepad.exe", "\"" + log + "\"") { UseShellExecute = false });
                else Balloon("Журнала пока нет", "Он появится после первой проверки или установки.", ToolTipIcon.Info);
            }
            catch (Exception) { Balloon("Не удалось открыть журнал", "Файл: " + DataFile("install.log"), ToolTipIcon.Warning); }
        }

        // Страница релизов открывается через explorer.exe: браузер запускает обычная оболочка пользователя.
        static void OpenUrl(string url)
        {
            try { Process.Start(new ProcessStartInfo("explorer.exe", url) { UseShellExecute = false }); }
            catch (Exception) { }
        }
    }

    // Окно состояния в стиле установщика (Egoist DS: монохром, золото только для акцента, радиусы и отступы кратны 4).
    class StatusForm : Form
    {
        public Action CheckNow, OpenLog, OpenReleases;
        public bool Allow;   // true - закрытие настоящее (значок закрыт); иначе окно только прячется
        static readonly Color Bg = ColorTranslator.FromHtml("#0E0E0F"), Surface = ColorTranslator.FromHtml("#141416"), Surface2 = ColorTranslator.FromHtml("#1C1C1E"),
            Line = ColorTranslator.FromHtml("#26262A"), Text_ = ColorTranslator.FromHtml("#EDEBE6"), Soft = ColorTranslator.FromHtml("#B5B3AD"),
            Muted = ColorTranslator.FromHtml("#8C8A85"), Gold = ColorTranslator.FromHtml("#C8A55A");
        readonly Label versionValue, gameValue, lastValue, resultValue;

        public StatusForm()
        {
            Text = "Русификатор Honor of Kings ACE";
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false; MinimizeBox = true; ShowInTaskbar = true;
            StartPosition = FormStartPosition.CenterScreen;
            AutoScaleMode = AutoScaleMode.Dpi;
            ClientSize = new Size(520, 468);   // 404 (кнопки) + 40 + 24 отступ
            BackColor = Bg; ForeColor = Text_;
            Font = new Font("Segoe UI", 9.75f);
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch (Exception) { }

            var eyebrow = MakeLabel("EGOIST · РУСИФИКАТОР", 24, 20, 472, 16, 8.25f, Gold);
            var title = MakeLabel("Honor of Kings ACE", 24, 38, 472, 32, 17f, Text_);
            title.Font = new Font("Segoe UI Semibold", 17f);
            Controls.Add(eyebrow); Controls.Add(title);

            var card = new Panel { Left = 24, Top = 84, Width = 472, Height = 188, BackColor = Surface };
            card.Paint += delegate (object s, PaintEventArgs e) { using (var pen = new Pen(Line)) e.Graphics.DrawRectangle(pen, 0, 0, card.Width - 1, card.Height - 1); };
            Controls.Add(card);
            versionValue = Row(card, "Версия перевода", 16);
            gameValue = Row(card, "Версия игры", 48);
            lastValue = Row(card, "Последняя проверка", 80);
            var resultCaption = MakeLabel("Результат", 16, 112, 160, 18, 9f, Muted); card.Controls.Add(resultCaption);
            resultValue = MakeLabel("", 16, 132, 440, 48, 10f, Text_); card.Controls.Add(resultValue);

            var note = new Panel { Left = 24, Top = 292, Width = 472, Height = 96, BackColor = Bg };
            var dot = MakeLabel("Как работает обновление", 0, 0, 472, 18, 9.5f, Text_); dot.Font = new Font("Segoe UI Semibold", 9.5f); note.Controls.Add(dot);
            var limit = MakeLabel(TrayApp.UpdateLimit, 0, 22, 472, 76, 9f, Muted); note.Controls.Add(limit);
            Controls.Add(note);

            int y = 404;
            var check = MakeButton("Проверить сейчас", 24, y, 168, true); check.Click += delegate { if (CheckNow != null) CheckNow(); };
            var log = MakeButton("Журнал", 200, y, 96, false); log.Click += delegate { if (OpenLog != null) OpenLog(); };
            var rel = MakeButton("Страница релизов", 304, y, 148, false); rel.Click += delegate { if (OpenReleases != null) OpenReleases(); };
            Controls.Add(check); Controls.Add(log); Controls.Add(rel);
            AcceptButton = check;
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (!Allow && e.CloseReason == CloseReason.UserClosing) { e.Cancel = true; Hide(); return; }
            base.OnFormClosing(e);
        }

        Label MakeLabel(string text, int x, int y, int w, int h, float size, Color color)
        {
            return new Label { Text = text, Left = x, Top = y, Width = w, Height = h, AutoSize = false, ForeColor = color, BackColor = Color.Transparent, Font = new Font("Segoe UI", size) };
        }

        Label Row(Panel card, string caption, int y)
        {
            card.Controls.Add(MakeLabel(caption, 16, y, 180, 24, 9.75f, Muted));
            var value = MakeLabel("—", 200, y, 256, 24, 10f, Text_);
            value.TextAlign = ContentAlignment.TopRight;
            card.Controls.Add(value);
            return value;
        }

        Button MakeButton(string text, int x, int y, int w, bool primary)
        {
            var button = new Button { Text = text, Left = x, Top = y, Width = w, Height = 40, FlatStyle = FlatStyle.Flat, UseVisualStyleBackColor = false, Cursor = Cursors.Hand };
            button.FlatAppearance.BorderColor = primary ? Text_ : Line;
            button.FlatAppearance.BorderSize = 1;
            button.BackColor = primary ? Text_ : Surface2;
            button.ForeColor = primary ? Bg : Text_;
            button.Font = new Font("Segoe UI Semibold", 9.75f);
            return button;
        }

        public void Fill(string translation, string game, string last, string result)
        {
            versionValue.Text = translation.Length > 0 ? translation : "—";
            gameValue.Text = game.Length > 0 ? game : "—";
            lastValue.Text = last.Length > 0 ? last : "—";
            resultValue.Text = result;
        }
    }
}
