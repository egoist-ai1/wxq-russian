// WXQTray.exe - значок русификатора в области уведомлений. Права администратора не нужны и не запрашиваются.
//
// Что делает:
//   * показывает состояние обновления из update-status.json (пишет только помощник WXQTool.exe update --check-only, от SYSTEM);
//   * проверка идёт только когда человек зашёл в русификатор: открыл окно значка, нажал «Проверить обновление» или запустил лаунчер
//     (появление процесса WXQLauncher*, не чаще раза в 12 часов). Таймеров, проверок при входе в Windows и при закрытии игры нет.
//     Проверка = schtasks /run задачи «Egoist\WXQ-Russian Update» (право запуска у обычных пользователей выдано дескриптором безопасности задачи);
//     она только сверяет update.json и докачивает установщик в кэш, ничего не ставит и не закрывает;
//   * «Обновить сейчас» (меню, окно, уведомление): сверяет SHA-256 кэшированного установщика с записанным помощником и запускает его
//     с запросом UAC; установщик в окне сам закрывает лаунчер и игру. Это единственный способ применить обновление;
//   * двойной щелчок по значку открывает окно состояния; все тексты статусов - в таблице CodeText, технических кодов пользователь не видит;
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
                    using (var tray = new TrayApp(restarted)) Application.Run();
                }
                finally { try { mutex.ReleaseMutex(); } catch (ApplicationException) { } }
            }
        }
    }

    class TrayApp : IDisposable
    {
        const string TaskName = "Egoist\\WXQ-Russian Update";
        static readonly Regex LauncherProcess = new Regex("^WXQLauncher", RegexOptions.IgnoreCase);
        static readonly TimeSpan LauncherCheckEvery = TimeSpan.FromHours(12);

        readonly NotifyIcon icon = new NotifyIcon();
        ToolStripMenuItem updateItem;
        readonly ToolStripMenuItem statusItem =new ToolStripMenuItem("Обновление: проверка не выполнялась") { Enabled = false };
        readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
        readonly string self = Application.ExecutablePath;
        readonly DateTime selfStamp;
        readonly string statusFile;
        const string ReleasesUrl = "https://github.com/egoist-ai1/wxq-russian/releases";
        // Ограничение, о котором владелец просил сказать прямо (то же слово в слово в установщике, README и CHANGELOG).
        public const string VisitText = "Русификатор проверяет обновления, когда вы заходите в него: открываете значок в трее, нажимаете «Проверить» или запускаете игру через ярлык; постоянных проверок в фоне нет.";
        public const string UpdateLimit = "После выхода обновления игры первый запуск будет на китайском: перевод вернётся автоматически после того, как вы один раз запустите игру до главного меню и закроете её. Иначе нельзя — новые хеши файлов игры создаются только самой игрой.";

        string seenKey = "";
        string notifiedDeferred = "";
        string notifiedGameChanged = "";
        bool launcherWasRunning;
        DateTime lastRequest = DateTime.MinValue;
        bool firstRead = true;
        StatusForm form;

        public TrayApp(bool restarted)
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
            updateItem = new ToolStripMenuItem("Обновить сейчас") { Visible = false };
            updateItem.Click += delegate { UpdateNow(); };
            menu.Items.Add(updateItem);
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
            launcherWasRunning = LauncherRunning();
            Tick();
            // Значок перезапущен после замены установщиком (обновление только что встало): статус «готово к установке» устарел, сверяем без 12-часового лимита.
            if (restarted) RequestRun(false);
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

        static bool LauncherRunning()
        {
            foreach (var process in Process.GetProcesses())
            {
                try { if (LauncherProcess.IsMatch(process.ProcessName)) return true; }
                catch (Exception) { }
                finally { process.Dispose(); }
            }
            return false;
        }

        // Время последней проверки хранится у пользователя (не в каталоге данных): ограничение «раз в 12 часов» переживает перезапуск значка.
        static string VisitFile()
        {
            return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Egoist", "WXQ-Russian", "tray-last-check.txt");
        }

        static DateTime LastCheck()
        {
            try
            {
                long ticks;
                if (long.TryParse(File.ReadAllText(VisitFile()).Trim(), NumberStyles.None, CultureInfo.InvariantCulture, out ticks) && ticks > 0 && ticks <= DateTime.UtcNow.Ticks)
                    return new DateTime(ticks, DateTimeKind.Utc);
            }
            catch (Exception) { }
            return DateTime.MinValue;
        }

        static void SaveLastCheck()
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(VisitFile()));
                File.WriteAllText(VisitFile(), DateTime.UtcNow.Ticks.ToString(CultureInfo.InvariantCulture));
            }
            catch (Exception) { }
        }

        // Просит планировщик выполнить задачу (только проверка: update --check-only). Сам процесс идёт от SYSTEM; здесь только запуск.
        void RequestRun(bool manual)
        {
            if (!manual && (DateTime.UtcNow - lastRequest).TotalSeconds < 60) return;
            lastRequest = DateTime.UtcNow;
            SaveLastCheck();
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

            // Запуск лаунчера = заход в русификатор: проверка один раз за запуск и не чаще раза в 12 часов. Работа лаунчера и закрытие игры ничего не запускают.
            bool launcher = LauncherRunning();
            if (launcher && !launcherWasRunning && DateTime.UtcNow - LastCheck() >= LauncherCheckEvery) RequestRun(false);
            launcherWasRunning = launcher;
            var status = ReadStatus();
            string state = Text(status, "state");
            bool canUpdate = HasInstaller(status);
            updateItem.Visible = canUpdate;
            if (form != null) form.ShowUpdate(canUpdate);
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
                Balloon("Обновление не применено", "Версия " + available + " ждёт: нажмите «Обновить сейчас» в меню значка.", ToolTipIcon.Info);
            }
            else if (state == "ready" && code == "reapply") Balloon("Нужно вернуть перевод", "Нажмите «Обновить сейчас» в меню значка: установщик сам закроет лаунчер и игру.", ToolTipIcon.Info);
            else if (state == "ready" && available.Length > 0) Balloon("Доступно обновление " + available, "Нажмите «Обновить сейчас» в меню значка: установщик сам закроет лаунчер и игру.", ToolTipIcon.Info);
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
            { "game_running", "Игра или лаунчер запущены. Обновление само ничего не закрывает: нажмите «Обновить сейчас», когда будете готовы, — установщик закроет лаунчер и игру." },
            { "reapply", "Игра обновилась, перевод нужно вернуть: нажмите «Обновить сейчас», установщик сам закроет лаунчер и игру." },
            { "setup_running", "Обновление ждёт: сейчас идёт установка перевода." },
            { "busy_running", "Обновление ждёт: русификатор занят другой операцией." },
            { "reapplied", "Игра обновилась, перевод возвращён." },
            { "rolled_back", "Новая версия не встала. Возвращена прежняя версия {i}, перевод работает." },
            { "av_blocked", "Антивирус, похоже, убрал файл обновления в карантин. Восстановите его или добавьте папку русификатора в исключения и нажмите «Проверить»." },
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
                case "ready": return "Доступно обновление " + Text(status, "available") + " — нажмите «Обновить сейчас»; установщик сам закроет лаунчер и игру.";
                case "deferred": return "Версия " + Text(status, "available") + " не применена: нажмите «Обновить сейчас».";
                case "done": return "Перевод обновлён до " + Text(status, "installed") + ".";
                case "failed": return "Обновление не удалось. Подробности в журнале.";
                case "idle": return "Обновлений нет.";
                default: return "Ожидание.";
            }
        }

        // ---- окно состояния ----

        string DataFile(string name) { return Path.Combine(DataDir(), name); }

        // Кнопка «Обновить сейчас» - явное согласие закрыть лаунчер и игру. Установщик берётся из кэша помощника; перед запуском сверяем путь,
        // размер и SHA-256 с теми, что записал помощник (запись в каталог данных у обычного пользователя закрыта правами).
        // Готовый установщик есть (помощник очищает путь при загрузке новой версии, установке и «всё актуально»; при сбое сети путь остаётся).
        static bool HasInstaller(Dictionary<string, object> status)
        {
            string path = Text(status, "setup_path");
            try { return path.Length > 0 && Text(status, "setup_sha").Length == 64 && File.Exists(path); }
            catch (Exception) { return false; }
        }

        void UpdateNow()
        {
            var status = ReadStatus();
            string path = Text(status, "setup_path"), sha = Text(status, "setup_sha").ToLowerInvariant();
            long bytes;
            long.TryParse(Text(status, "setup_bytes"), NumberStyles.None, CultureInfo.InvariantCulture, out bytes);
            try
            {
                string updates = Path.GetFullPath(Path.Combine(DataDir(), "updates")).TrimEnd('\\') + "\\";
                if (path.Length == 0 || sha.Length != 64 || !Path.GetFullPath(path).StartsWith(updates, StringComparison.OrdinalIgnoreCase) || !File.Exists(path) || new FileInfo(path).Length != bytes)
                { Balloon("Установщик не готов", "Нажмите «Проверить обновление»: он будет скачан заново.", ToolTipIcon.Warning); return; }
                string actual;
                using (var stream = File.OpenRead(path))
                using (var hash = System.Security.Cryptography.SHA256.Create())
                    actual = BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
                if (actual != sha)
                { Balloon("Установщик не прошёл проверку", "Нажмите «Проверить обновление»: файл будет скачан заново.", ToolTipIcon.Warning); return; }
                // runas: запрос UAC в сеансе пользователя; установщик откроет окно с этапами и сам закроет лаунчер и игру.
                Process.Start(new ProcessStartInfo(path) { UseShellExecute = true, Verb = "runas", WorkingDirectory = Path.GetDirectoryName(path) });
            }
            catch (System.ComponentModel.Win32Exception error)
            {
                if (error.NativeErrorCode != 1223) Balloon("Не удалось запустить установщик", "Windows ответила ошибкой " + error.NativeErrorCode + ". Попробуйте ещё раз или запустите установщик из папки updates вручную.", ToolTipIcon.Warning);
                // 1223 (ERROR_CANCELLED) - человек отказал в запросе UAC: ничего не происходит
            }
            catch (Exception) { Balloon("Не удалось запустить установщик", "Подробности в журнале русификатора.", ToolTipIcon.Warning); }
        }

        void ShowStatus()
        {
            if (form == null)
            {
                form = new StatusForm();
                form.CheckNow = delegate { RequestRun(true); };
                form.UpdateNow = delegate { UpdateNow(); };
                form.OpenLog = delegate { OpenLog(); };
                form.OpenReleases = delegate { OpenUrl(ReleasesUrl); };
            }
            var status = ReadStatus();
            if (status == null) form.Fill("", "", "", "Проверка ещё не выполнялась.");
            else FillForm(status, Text(status, "state"));
            form.ShowUpdate(HasInstaller(status));
            bool wasVisible = form.Visible;
            if (!wasVisible) form.Show();
            form.WindowState = FormWindowState.Normal;
            form.Activate();
            if (!wasVisible) RequestRun(false);   // открыли окно = зашли в русификатор: тихая проверка (не чаще раза в минуту)
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
        public Action CheckNow, UpdateNow, OpenLog, OpenReleases;
        readonly Button checkButton, updateButton, logButton, releasesButton;
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
            ClientSize = new Size(520, 520);   // 456 (кнопки) + 40 + 24 отступ
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

            var note = new Panel { Left = 24, Top = 292, Width = 472, Height = 148, BackColor = Bg };
            var dot = MakeLabel("Как работает обновление", 0, 0, 472, 18, 9.5f, Text_); dot.Font = new Font("Segoe UI Semibold", 9.5f); note.Controls.Add(dot);
            var limit = MakeLabel(TrayApp.VisitText + "\n\n" + TrayApp.UpdateLimit, 0, 22, 472, 124, 9f, Muted); note.Controls.Add(limit);
            Controls.Add(note);

            checkButton = MakeButton("Проверить", 24, 456, 168, true); checkButton.Click += delegate { if (CheckNow != null) CheckNow(); };
            updateButton = MakeButton("Обновить сейчас", 24, 456, 148, true); updateButton.Click += delegate { if (UpdateNow != null) UpdateNow(); };
            logButton = MakeButton("Журнал", 200, 456, 96, false); logButton.Click += delegate { if (OpenLog != null) OpenLog(); };
            releasesButton = MakeButton("Страница релизов", 304, 456, 148, false); releasesButton.Click += delegate { if (OpenReleases != null) OpenReleases(); };
            Controls.Add(updateButton); Controls.Add(checkButton); Controls.Add(logButton); Controls.Add(releasesButton);
            ShowUpdate(false);
        }

        // Есть готовое обновление: «Обновить сейчас» - главная кнопка, «Проверить» уходит рядом.
        public void ShowUpdate(bool available)
        {
            updateButton.Visible = available;
            if (available)
            {
                checkButton.SetBounds(180, 456, 100, 40); checkButton.BackColor = Surface2; checkButton.ForeColor = Text_; checkButton.FlatAppearance.BorderColor = Line;
                logButton.SetBounds(288, 456, 80, 40); releasesButton.SetBounds(376, 456, 120, 40);
                releasesButton.Text = "Релизы";
                AcceptButton = updateButton;
            }
            else
            {
                checkButton.SetBounds(24, 456, 168, 40); checkButton.BackColor = Text_; checkButton.ForeColor = Bg; checkButton.FlatAppearance.BorderColor = Text_;
                logButton.SetBounds(200, 456, 96, 40); releasesButton.SetBounds(304, 456, 148, 40);
                releasesButton.Text = "Страница релизов";
                AcceptButton = checkButton;
            }
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
