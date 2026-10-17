// Подставное окно для проверки стороны Inno протокола (installer\ui\PROTOCOL.md). Только для installer\tests: собирается run_tests.py / ui_protocol.py,
// в установщик не входит. Запускается как WXQSetupUI.exe --dir <папка> --token <токен> --parent <pid> (через /UIEXE=, только тестовая сборка).
// Сценарий — переменная FAKEUI_SCRIPT, шаги через «|»:
//   nomutex            не создавать мьютекс (мастер должен счесть, что окно не стартовало)
//   wait=<screen>      ждать, пока в state.json будет такой screen (до 5 минут)
//   raw=<текст>        дописать в commands.jsonl как есть (\n в тексте — перевод строки)
//   cmd=<seq>,<cmd>    дописать каноническую строку {"seq":N,"cmd":"X"}; cmd=<seq>,<cmd>,true|false добавляет options.launcher
//   sleep=<мс>         пауза
//   hammer=<мс>        читать state.json без передышки: каждая законченная запись должна быть целым JSON с растущим rev
//   die                выйти сразу (мьютекс исчезает), как упавшее окно
//   hold               ждать screen = quit
// Итог пишется в <папка>\fakeui-report.txt: screens (экраны по порядку), reads, partial, regress, rev_max, ошибки разбора.
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

static class FakeUI
{
    static string dir;
    static List<string> screens = new List<string>();
    static long reads, partial, regress, revMax; static List<string> errors = new List<string>();

    static string Read(string path)
    {
        try
        {
            using (var f = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            using (var r = new StreamReader(f, new UTF8Encoding(false), true)) return r.ReadToEnd();
        }
        catch (IOException) { return null; }
        catch (UnauthorizedAccessException) { return null; }
    }

    static string Screen(string text, bool count)
    {
        if (string.IsNullOrEmpty(text)) return null;
        text = text.TrimStart('\uFEFF').Trim();
        if (text.Length < 2 || text[0] != '{' || text[text.Length - 1] != '}') { if (count) partial++; return null; }
        Dictionary<string, object> d;
        try { d = new JavaScriptSerializer().DeserializeObject(text) as Dictionary<string, object>; }
        catch (Exception e) { if (count) { partial++; errors.Add(e.Message); } return null; }
        if (d == null || !d.ContainsKey("rev") || !d.ContainsKey("screen")) { if (count) partial++; return null; }
        long rev = Convert.ToInt64(d["rev"]);
        if (count) { reads++; if (rev < revMax) regress++; if (rev > revMax) revMax = rev; }
        string scr = Convert.ToString(d["screen"]);
        if (screens.Count == 0 || screens[screens.Count - 1] != scr) screens.Add(scr);
        return scr;
    }

    static void Append(string text)
    {
        byte[] data = new UTF8Encoding(false).GetBytes(text);
        for (int i = 0; i < 40; i++)
        {
            try { using (var f = new FileStream(Path.Combine(dir, "commands.jsonl"), FileMode.Append, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete)) { f.Write(data, 0, data.Length); } return; }
            catch (IOException) { Thread.Sleep(25); }
        }
        errors.Add("не удалось дописать команду");
    }

    static bool Wait(string screen, int ms)
    {
        var end = DateTime.UtcNow.AddMilliseconds(ms);
        while (DateTime.UtcNow < end)
        {
            string s = Screen(Read(Path.Combine(dir, "state.json")), true);
            if (s == screen) return true;
            if (s == "quit") return false;
            Thread.Sleep(5);
        }
        errors.Add("ожидание экрана " + screen + " истекло");
        return false;
    }

    static void WriteReport()
    {
        var report = new StringBuilder();
        report.Append("screens=").Append(string.Join(",", screens.ToArray())).Append('\n').Append("reads=").Append(reads).Append('\n').Append("partial=").Append(partial).Append('\n')
              .Append("regress=").Append(regress).Append('\n').Append("rev_max=").Append(revMax).Append('\n').Append("errors=").Append(string.Join(";", errors.ToArray())).Append('\n');
        // Защищённая папка установщика удаляется при выходе, поэтому тест задаёт отчёт вне её (FAKEUI_REPORT); без переменной - рядом с файлами обмена.
        string path = Environment.GetEnvironmentVariable("FAKEUI_REPORT") ?? Path.Combine(dir, "fakeui-report.txt");
        try { File.WriteAllText(path, report.ToString(), new UTF8Encoding(false)); } catch (IOException) { } catch (UnauthorizedAccessException) { }
    }

    static int Main(string[] args)
    {
        string token = null;
        for (int i = 0; i < args.Length - 1; i++) { if (args[i] == "--dir") dir = args[i + 1]; if (args[i] == "--token") token = args[i + 1]; }
        if (dir == null || token == null) return 2;
        string script = Environment.GetEnvironmentVariable("FAKEUI_SCRIPT") ?? "hold";
        Mutex m = null;
        var steps = script.Split('|');
        if (Array.IndexOf(steps, "nomutex") < 0) m = new Mutex(false, "Local\\WXQSetupUI-" + token);
        bool died = false;
        foreach (string step in steps)
        {
            int eq = step.IndexOf('=');
            string name = eq < 0 ? step : step.Substring(0, eq), arg = eq < 0 ? "" : step.Substring(eq + 1);
            if (name == "wait") { if (!Wait(arg, 300000)) break; }
            else if (name == "raw") Append(arg.Replace("\\n", "\n").Replace("\\r", "\r"));
            else if (name == "cmd") { var p = arg.Split(','); Append("{\"seq\":" + p[0] + ",\"cmd\":\"" + p[1] + "\"" + (p.Length > 2 ? ",\"options\":{\"launcher\":" + p[2] + "}" : "") + "}\n"); }
            else if (name == "sleep") Thread.Sleep(int.Parse(arg));
            else if (name == "hammer") { var end = DateTime.UtcNow.AddMilliseconds(int.Parse(arg)); while (DateTime.UtcNow < end) Screen(Read(Path.Combine(dir, "state.json")), true); }
            else if (name == "die") { died = true; break; }
            else if (name == "hold") { while (Screen(Read(Path.Combine(dir, "state.json")), true) != "quit") { Thread.Sleep(20); WriteReport(); } break; }
            WriteReport();
        }
        // Если мастер ещё жив и не звал выход, дожидаемся quit (отчёт полный), кроме сценария die.
        if (!died)
        {
            var end = DateTime.UtcNow.AddSeconds(20);
            while (DateTime.UtcNow < end && Screen(Read(Path.Combine(dir, "state.json")), false) != "quit") Thread.Sleep(50);
        }
        WriteReport();
        GC.KeepAlive(m);
        return 0;
    }
}
