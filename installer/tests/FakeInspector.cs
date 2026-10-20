// Стенд для test_launcher_ru.py: поддельный «лаунчер» с поддельным inspector (HTTP /json/list + WebSocket + Runtime.evaluate).
// Реального лаунчера и игры не касается. Настройка только переменными окружения (активатор запускает его без аргументов):
//   FAKE_ROLE   launcher (по умолчанию: окно, inspector открывается по событию Local\WXQFakeInspect-<pid>) | inspector | idle
//   FAKE_MODE   ok | wrongtitle | two | badws | evalerror | noclose | foreign (launcher: inspector в дочернем процессе)
//   FAKE_BIND   loopback (по умолчанию) | any
//   FAKE_PORT   порт inspector;  FAKE_LOG — файл журнала (одна JSON-строка на запрос);  FAKE_IGNORECLOSE=1 — окно не закрывается
// Собирается штатным csc из .NET Framework 4, C# 5.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

static class FakeInspector
{
    sealed class PassiveForm : Form
    {
        protected override bool ShowWithoutActivation { get { return true; } }
        protected override CreateParams CreateParams
        {
            get { var p = base.CreateParams; p.ExStyle |= 0x08000000; return p; }
        }
    }
    static readonly JavaScriptSerializer Js = new JavaScriptSerializer();
    static readonly object LogLock = new object();
    static TcpListener listener;
    static readonly List<TcpClient> clients = new List<TcpClient>();
    static string Env(string name, string fallback) { return Environment.GetEnvironmentVariable(name) ?? fallback; }

    static void Log(string method, string expression)
    {
        string file = Env("FAKE_LOG", null);
        if (file == null) return;
        var row = new Dictionary<string, object> { { "pid", Process.GetCurrentProcess().Id }, { "method", method }, { "expr", expression } };
        lock (LogLock) File.AppendAllText(file, Js.Serialize(row) + "\n", new UTF8Encoding(false));
    }

    [STAThread]
    static int Main()
    {
        string role = Env("FAKE_ROLE", "launcher");
        var killer = new Thread(() => { Thread.Sleep(120000); Environment.Exit(9); }) { IsBackground = true };
        killer.Start();
        Log("start", role);
        if (role == "idle") { Thread.Sleep(Timeout.Infinite); return 0; }
        if (role == "inspector") { StartInspector(); Thread.Sleep(Timeout.Infinite); return 0; }

        int pid = Process.GetCurrentProcess().Id;
        var signal = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\WXQFakeInspect-" + pid);
        var waiter = new Thread(() =>
        {
            signal.WaitOne();
            if (Env("FAKE_MODE", "ok") == "foreign")
            {
                var info = new ProcessStartInfo(Assembly.GetExecutingAssembly().Location) { UseShellExecute = false };
                info.EnvironmentVariables["FAKE_ROLE"] = "inspector";
                info.EnvironmentVariables["FAKE_MODE"] = "ok";
                Process.Start(info);
            }
            else StartInspector();
        }) { IsBackground = true };
        waiter.Start();

        var form = new PassiveForm { Text = "FakeLauncher", StartPosition = FormStartPosition.Manual, Location = new Point(-32000, -32000), Size = new Size(100, 60) };
        if (Env("FAKE_IGNORECLOSE", "") == "1") form.FormClosing += (s, e) => { e.Cancel = true; };
        Application.Run(form);
        return 0;
    }

    static void StartInspector()
    {
        int port = int.Parse(Env("FAKE_PORT", "9229"));
        listener = new TcpListener(Env("FAKE_BIND", "loopback") == "any" ? IPAddress.Any : IPAddress.Loopback, port);
        listener.ExclusiveAddressUse = true;
        listener.Start();
        var accept = new Thread(AcceptLoop) { IsBackground = true };
        accept.Start();
    }

    static void StopInspector()
    {
        try { listener.Stop(); } catch (Exception) { }
        lock (clients) { foreach (var c in clients) { try { c.Close(); } catch (Exception) { } } clients.Clear(); }
    }

    static void AcceptLoop()
    {
        while (true)
        {
            TcpClient client;
            try { client = listener.AcceptTcpClient(); } catch (Exception) { return; }
            lock (clients) clients.Add(client);
            var thread = new Thread(() => { try { Serve(client); } catch (Exception) { } }) { IsBackground = true };
            thread.Start();
        }
    }

    static string ReadHeaders(NetworkStream stream)
    {
        var bytes = new List<byte>();
        while (true)
        {
            int b = stream.ReadByte();
            if (b < 0) return null;
            bytes.Add((byte)b);
            int n = bytes.Count;
            if (n >= 4 && bytes[n - 4] == 13 && bytes[n - 3] == 10 && bytes[n - 2] == 13 && bytes[n - 1] == 10) return Encoding.ASCII.GetString(bytes.ToArray());
        }
    }

    static void Serve(TcpClient client)
    {
        var stream = client.GetStream();
        string head = ReadHeaders(stream);
        if (head == null) return;
        string[] lines = head.Split(new[] { "\r\n" }, StringSplitOptions.RemoveEmptyEntries);
        string path = lines[0].Split(' ')[1];
        string key = null;
        foreach (string line in lines) if (line.StartsWith("Sec-WebSocket-Key:", StringComparison.OrdinalIgnoreCase)) key = line.Substring(18).Trim();
        int port = ((IPEndPoint)listener.LocalEndpoint).Port;
        string mode = Env("FAKE_MODE", "ok");
        if (path == "/json/list")
        {
            Log("http", path);
            string title = mode == "wrongtitle" ? "some-other-target" : "electron/js2c/browser_init";
            string host = mode == "badws" ? "localhost" : "127.0.0.1";
            var items = new List<Dictionary<string, object>>();
            int count = mode == "two" ? 2 : 1;
            for (int i = 0; i < count; i++)
                items.Add(new Dictionary<string, object> { { "title", title }, { "webSocketDebuggerUrl", "ws://" + host + ":" + port + "/0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f" + i } });
            byte[] body = Encoding.UTF8.GetBytes(Js.Serialize(items));
            byte[] header = Encoding.ASCII.GetBytes("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + body.Length + "\r\nConnection: close\r\n\r\n");
            stream.Write(header, 0, header.Length); stream.Write(body, 0, body.Length);
            return;
        }
        if (key == null) return;
        string accept = Convert.ToBase64String(SHA1.Create().ComputeHash(Encoding.ASCII.GetBytes(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11")));
        byte[] reply = Encoding.ASCII.GetBytes("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n\r\n");
        stream.Write(reply, 0, reply.Length);
        while (true)
        {
            string text = ReadFrame(stream);
            if (text == null) return;
            var request = Js.DeserializeObject(text) as Dictionary<string, object>;
            string method = (string)request["method"];
            var parameters = request["params"] as Dictionary<string, object>;
            string expression = parameters != null && parameters.ContainsKey("expression") ? (string)parameters["expression"] : "";
            Log(method, expression);
            Dictionary<string, object> result;
            bool closeAfter = false;
            if (method != "Runtime.evaluate") result = new Dictionary<string, object> { { "exceptionDetails", new Dictionary<string, object> { { "text", "unexpected" } } } };
            else if (expression.Contains("process.uptime()"))
            {
                var me = Process.GetCurrentProcess();
                double started = (me.StartTime.ToUniversalTime() - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalMilliseconds;
                result = Value(new Dictionary<string, object> { { "pid", me.Id }, { "path", Assembly.GetExecutingAssembly().Location }, { "startedAtApprox", started } });
            }
            else if (expression.Contains(".install("))
            {
                if (mode == "evalerror") result = new Dictionary<string, object> { { "exceptionDetails", new Dictionary<string, object> { { "text", "boom" } } } };
                else { result = Value(true); closeAfter = mode != "noclose"; }
            }
            else { result = Value(null); closeAfter = mode != "noclose"; } // выражение закрытия inspector
            var message = new Dictionary<string, object> { { "id", request["id"] }, { "result", result } };
            WriteFrame(stream, Js.Serialize(message));
            if (closeAfter) { new Thread(() => { Thread.Sleep(200); StopInspector(); }) { IsBackground = true }.Start(); }
        }
    }

    static Dictionary<string, object> Value(object value)
    {
        return new Dictionary<string, object> { { "result", new Dictionary<string, object> { { "type", "object" }, { "value", value } } } };
    }

    static bool ReadExact(NetworkStream s, byte[] buf, int count)
    {
        int got = 0;
        while (got < count) { int n = s.Read(buf, got, count - got); if (n <= 0) return false; got += n; }
        return true;
    }

    static string ReadFrame(NetworkStream s)
    {
        var data = new MemoryStream();
        while (true)
        {
            var h = new byte[2];
            if (!ReadExact(s, h, 2)) return null;
            int opcode = h[0] & 0x0F; bool fin = (h[0] & 0x80) != 0;
            long len = h[1] & 0x7F;
            if (len == 126) { var e = new byte[2]; if (!ReadExact(s, e, 2)) return null; len = (e[0] << 8) | e[1]; }
            else if (len == 127) { var e = new byte[8]; if (!ReadExact(s, e, 8)) return null; len = 0; for (int i = 0; i < 8; i++) len = (len << 8) | e[i]; }
            var mask = new byte[4];
            if ((h[1] & 0x80) != 0 && !ReadExact(s, mask, 4)) return null;
            var payload = new byte[len];
            if (len > 0 && !ReadExact(s, payload, (int)len)) return null;
            for (int i = 0; i < payload.Length; i++) payload[i] ^= mask[i % 4];
            if (opcode == 8) return null;
            if (opcode == 9 || opcode == 10) continue;
            data.Write(payload, 0, payload.Length);
            if (fin) return Encoding.UTF8.GetString(data.ToArray());
        }
    }

    static void WriteFrame(NetworkStream s, string text)
    {
        byte[] payload = Encoding.UTF8.GetBytes(text);
        var frame = new MemoryStream();
        frame.WriteByte(0x81);
        if (payload.Length < 126) frame.WriteByte((byte)payload.Length);
        else { frame.WriteByte(126); frame.WriteByte((byte)(payload.Length >> 8)); frame.WriteByte((byte)payload.Length); }
        frame.Write(payload, 0, payload.Length);
        byte[] bytes = frame.ToArray();
        s.Write(bytes, 0, bytes.Length);
    }
}
