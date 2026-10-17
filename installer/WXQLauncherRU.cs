// Активатор «русский лаунчер» для Honor of Kings: ACE.
//
// Запускает или находит НЕТРОНУТЫЙ оригинальный лаунчер (王者万象棋.exe) и коротко подключает перевод:
// включает встроенный inspector процесса (так делает Node: process._debugProcess), одним Runtime.evaluate
// подгружает launcher-ru-runtime.cjs и сразу закрывает inspector. Любой отказ означает запуск оригинала
// без подключения. Файлы лаунчера не меняются.
//
// Рядом с exe (папка launcher\ в Program Files): target.txt, profiles.json, launcher-ru-runtime.cjs, launcher-ru.json,
// launcher-entry-public-ru.draft.json, launcher-layout.css, main-logo-title.a73b14e6.png (их читает runtime из своей папки).
// Запуск и подключение разрешены, только если папка launcher\ и её родитель закрыты от записи обычным пользователям
// И оригинальный exe лежит в Program Files, без ссылок (junction, symlink) на пути.
// SHA-256 модуля и словаря зашиты в exe ресурсом launcher-expected.txt (csc /resource:...,launcher-expected.txt).
// Флаг включения: HKLM\SOFTWARE\Egoist\WXQ-Russian, значение LauncherRU (DWORD 1).
//
// Собирается штатным csc.exe из .NET Framework 4, C# 5. Тестовая сборка: /define:TESTBUILD (параметры --test-*).
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Management;
using System.Net;
using System.Net.WebSockets;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using Microsoft.Win32;

namespace Egoist.WXQLauncherRU
{
    // Отказ подключать перевод. Code — фиксированный код для launcher-status.json, без путей и данных пользователя.
    sealed class Refusal : Exception
    {
        public readonly string Code;
        public readonly string Result;
        public Refusal(string code) : this(code, "fallback") { }
        public Refusal(string code, string result) : base(code) { Code = code; Result = result; }
    }

    static class Opt
    {
        public const string LauncherTitle = "electron/js2c/browser_init";
        public const int WindowWaitMs = 30000;
        public const int SettleMs = 1500;
        public const int InspectorWaitMs = 6000;
        public const int CloseWaitMs = 4000;
        public const long MaxIdentityDriftMs = 10000;
#if TESTBUILD
        public static int Port = 9229;
        public static string StatusDirOverride;
        public static int Flag = -1;                 // -1: читать реестр
        public static bool NoSignature, SignalActivation, IgnoreOwner;
        public static string TitlePrefix = LauncherTitle, GameName = "WXQ", TrustRoot, ExpectedFile;
#else
        public const int Port = 9229;
#endif
        public static string StatusDir
        {
            get
            {
#if TESTBUILD
                if (StatusDirOverride != null) return StatusDirOverride;
#endif
                return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Egoist", "WXQ-Russian");
            }
        }
    }

    static class Native
    {
        public const uint ProcessAccess = 0x0002 | 0x0400 | 0x0008 | 0x0020 | 0x0010; // CREATE_THREAD | QUERY_INFORMATION | VM_OPERATE | VM_WRITE | VM_READ
        [DllImport("kernel32.dll", SetLastError = true)] public static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)] public static extern IntPtr OpenFileMappingW(uint access, bool inherit, string name);
        [DllImport("kernel32.dll", SetLastError = true)] public static extern IntPtr MapViewOfFile(IntPtr mapping, uint access, uint high, uint low, UIntPtr bytes);
        [DllImport("kernel32.dll", SetLastError = true)] public static extern bool UnmapViewOfFile(IntPtr view);
        [DllImport("kernel32.dll", SetLastError = true)] public static extern IntPtr CreateRemoteThread(IntPtr process, IntPtr attrs, UIntPtr stack, IntPtr start, IntPtr param, uint flags, out uint id);
        [DllImport("kernel32.dll")] public static extern uint WaitForSingleObject(IntPtr handle, uint ms);
        [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr handle);
        [DllImport("iphlpapi.dll", SetLastError = true)] public static extern int GetExtendedTcpTable(IntPtr table, ref int size, bool sort, int family, int tableClass, int reserved);
        [DllImport("wintrust.dll", CharSet = CharSet.Unicode)] public static extern int WinVerifyTrust(IntPtr hwnd, ref Guid action, ref WinTrustData data);

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        public struct WinTrustFileInfo
        {
            public uint cbStruct;
            [MarshalAs(UnmanagedType.LPWStr)] public string path;
            public IntPtr file;
            public IntPtr knownSubject;
        }
        [StructLayout(LayoutKind.Sequential)]
        public struct WinTrustData
        {
            public uint cbStruct;
            public IntPtr policyCallbackData, sipClientData;
            public uint uiChoice, revocationChecks, unionChoice;
            public IntPtr file;
            public uint stateAction;
            public IntPtr stateData, urlReference;
            public uint provFlags, uiContext;
            public IntPtr signatureSettings;
        }
    }

    sealed class Listener { public int Pid; public string Addr; public bool V6; }

    static class Tcp
    {
        // Слушающие сокеты порта (IPv4 и IPv6) с PID владельца.
        public static List<Listener> Listeners(int port)
        {
            var result = new List<Listener>();
            Collect(2, 24, port, result);
            Collect(23, 56, port, result);
            return result;
        }

        static void Collect(int family, int rowSize, int port, List<Listener> result)
        {
            int size = 0;
            Native.GetExtendedTcpTable(IntPtr.Zero, ref size, false, family, 5, 0);
            for (int attempt = 0; attempt < 5; attempt++)
            {
                size += 4096;
                IntPtr buffer = Marshal.AllocHGlobal(size);
                try
                {
                    int rc = Native.GetExtendedTcpTable(buffer, ref size, false, family, 5, 0); // TCP_TABLE_OWNER_PID_ALL
                    if (rc == 122) continue;                                                      // буфер мал, size обновлён
                    if (rc != 0) throw new InvalidOperationException("tcp-table");
                    int count = Marshal.ReadInt32(buffer, 0);
                    for (int i = 0; i < count; i++)
                    {
                        IntPtr row = IntPtr.Add(buffer, 4 + i * rowSize);
                        int state, portRaw, pid; string addr;
                        if (family == 2)
                        {
                            state = Marshal.ReadInt32(row, 0);
                            addr = new IPAddress(BitConverter.GetBytes((uint)Marshal.ReadInt32(row, 4))).ToString();
                            portRaw = Marshal.ReadInt32(row, 8);
                            pid = Marshal.ReadInt32(row, 20);
                        }
                        else
                        {
                            var raw = new byte[16];
                            Marshal.Copy(row, raw, 0, 16);
                            addr = new IPAddress(raw).ToString();
                            portRaw = Marshal.ReadInt32(row, 20);
                            state = Marshal.ReadInt32(row, 48);
                            pid = Marshal.ReadInt32(row, 52);
                        }
                        int localPort = ((portRaw & 0xFF) << 8) | ((portRaw >> 8) & 0xFF);
                        if (state == 2 && localPort == port) result.Add(new Listener { Pid = pid, Addr = addr, V6 = family == 23 });
                    }
                    return;
                }
                finally { Marshal.FreeHGlobal(buffer); }
            }
            throw new InvalidOperationException("tcp-table-size");
        }

        // Порт принадлежит ровно процессу pid и слушает только 127.0.0.1.
        public static bool OwnedExactly(int pid, int port)
        {
            var list = Listeners(port);
            return list.Count == 1 && list[0].Pid == pid && !list[0].V6 && list[0].Addr == "127.0.0.1";
        }
    }

    static class Files
    {
        public static string Sha256(string path)
        {
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 1 << 20))
            using (var hash = SHA256.Create())
            {
                var bytes = hash.ComputeHash(stream);
                var text = new StringBuilder(bytes.Length * 2);
                foreach (byte b in bytes) text.Append(b.ToString("x2", CultureInfo.InvariantCulture));
                return text.ToString();
            }
        }

        static readonly string[] TrustedSids = { "S-1-5-18", "S-1-5-32-544", "S-1-3-0" };
        const string TrustedInstallerPrefix = "S-1-5-80-956008885-";
        const FileSystemRights WriteMask = FileSystemRights.WriteData | FileSystemRights.AppendData | FileSystemRights.WriteExtendedAttributes |
            FileSystemRights.WriteAttributes | FileSystemRights.Delete | FileSystemRights.DeleteSubdirectoriesAndFiles |
            FileSystemRights.ChangePermissions | FileSystemRights.TakeOwnership;

        static bool Trusted(SecurityIdentifier sid, bool allowCreatorOwner)
        {
            if (sid == null) return false;
            string text = sid.Value;
            if (text == "S-1-3-0") return allowCreatorOwner;
            return Array.IndexOf(TrustedSids, text) >= 0 || text.StartsWith(TrustedInstallerPrefix, StringComparison.Ordinal);
        }

        // Запись разрешена только SYSTEM, администраторам и TrustedInstaller; владелец — тоже из них. null — права в порядке.
        public static string AclProblem(FileSystemInfo item)
        {
            FileSystemSecurity security = item is DirectoryInfo ? (FileSystemSecurity)((DirectoryInfo)item).GetAccessControl(AccessControlSections.Access | AccessControlSections.Owner)
                                                                : ((FileInfo)item).GetAccessControl(AccessControlSections.Access | AccessControlSections.Owner);
            bool skipOwner = false;
#if TESTBUILD
            skipOwner = Opt.IgnoreOwner;
#endif
            if (!skipOwner && !Trusted(security.GetOwner(typeof(SecurityIdentifier)) as SecurityIdentifier, false)) return "owner";
            foreach (FileSystemAccessRule rule in security.GetAccessRules(true, true, typeof(SecurityIdentifier)))
            {
                if (rule.AccessControlType != AccessControlType.Allow || (rule.FileSystemRights & WriteMask) == 0) continue;
                if (!Trusted(rule.IdentityReference as SecurityIdentifier, true)) return "write";
            }
            return null;
        }

        // Authenticode: цепочка доверия без сети и без интерфейса; возвращает отпечаток подписанта.
        public static string SignerThumbprint(string path)
        {
            Guid action = new Guid("00AAC56B-CD44-11d0-8CC2-00C04FC295EE");
            var info = new Native.WinTrustFileInfo { path = path };
            info.cbStruct = (uint)Marshal.SizeOf(typeof(Native.WinTrustFileInfo));
            IntPtr infoPtr = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(Native.WinTrustFileInfo)));
            try
            {
                Marshal.StructureToPtr(info, infoPtr, false);
                var data = new Native.WinTrustData { file = infoPtr, uiChoice = 2, revocationChecks = 0, unionChoice = 1, stateAction = 0, provFlags = 0x10 | 0x1000 };
                data.cbStruct = (uint)Marshal.SizeOf(typeof(Native.WinTrustData));
                if (Native.WinVerifyTrust(new IntPtr(-1), ref action, ref data) != 0) throw new Refusal("signature");
            }
            finally { Marshal.DestroyStructure(infoPtr, typeof(Native.WinTrustFileInfo)); Marshal.FreeHGlobal(infoPtr); }
            try { return new X509Certificate2(X509Certificate.CreateFromSignedFile(path)).Thumbprint; }
            catch (Exception) { throw new Refusal("signature"); }
        }
    }

    sealed class Profile { public string Build, Exe, Asar, Skin, LauncherJson, Thumbprint; }

    sealed class Session
    {
        public string Dir, Target, ForwardArgs = "";
        public string Profile;
        public Process Proc;
        public bool Owned, AlreadyRunning, Activated, CloseTried;
        public long StartTicks;
        public DateTime StartUtc;
        public string AclState;                       // null — права в порядке, иначе код
        public bool AclChecked;
    }

    static class Program
    {
        static readonly JavaScriptSerializer Js = new JavaScriptSerializer { MaxJsonLength = 4 * 1024 * 1024 };

        static int Main(string[] args)
        {
            var s = new Session();
            s.Dir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            s.ForwardArgs = ParseArgs(args);
            try { s.Target = ReadTarget(s.Dir); }
            catch (Exception) { WriteStatus(null, "fallback", "no-target"); return 3; }

            bool flag = false;
            try { flag = ReadFlag(); } catch (Exception) { }
            if (!flag)
            {
                string problem = TrustProblem(s);
                if (problem != null || !StartOriginal(s)) WriteStatus(null, "fallback", problem ?? "untrusted-target");
                return 0;
            }

            string result = "ok", code = "attached";
            try { Attach(s); }
            catch (Refusal r) { result = r.Result; code = r.Code; }
            catch (Exception e)
            {
                result = "fallback"; code = InternalCode(e);
            }
            if (s.Activated)
            {
                string left = EnsurePortClosed(s);
                if (left != null) { code = result == "ok" ? left : code + ";" + left; result = "fallback"; }
            }
            if (result == "ok" && s.Proc != null && s.Proc.HasExited) { result = "fallback"; code = "launcher-exited"; }
            if (result != "ok")
            {
                bool start = false;
                if (!s.Owned && !s.AlreadyRunning) start = true;                  // оригинал ещё не запускали
                else if (s.Owned && s.Activated)
                {
                    // Порт inspector был открыт в нашем процессе администратора: такой процесс не оставляем, запускаем оригинал заново.
                    if (EndOwned(s)) { s.Owned = false; start = true; }
                    else code += ";restart-skipped";
                }
                if (start && !StartOriginal(s)) code = TrustProblem(s) ?? "untrusted-target";
            }
            WriteStatus(s.Profile, result, code);
            return 0;
        }

        // В релизе код фиксированный; в тестовой сборке добавляем тип и текст исключения для диагностики.
        static string InternalCode(Exception error)
        {
#if TESTBUILD
            return "internal-error:" + error.GetType().Name + ":" + error.Message;
#else
            return "internal-error";
#endif
        }

        static string ParseArgs(string[] args)
        {
            var forwarded = new List<string>();
            foreach (string arg in args)
            {
#if TESTBUILD
                if (arg.StartsWith("--test-", StringComparison.Ordinal)) { TestOption(arg); continue; }
#endif
                forwarded.Add(arg.IndexOfAny(new[] { ' ', '\t', '"' }) >= 0 ? "\"" + arg.Replace("\"", "\\\"") + "\"" : arg);
            }
            return string.Join(" ", forwarded.ToArray());
        }

#if TESTBUILD
        static void TestOption(string arg)
        {
            int eq = arg.IndexOf('=');
            string name = eq < 0 ? arg : arg.Substring(0, eq), value = eq < 0 ? "" : arg.Substring(eq + 1);
            switch (name)
            {
                case "--test-port": Opt.Port = int.Parse(value, CultureInfo.InvariantCulture); break;
                case "--test-status-dir": Opt.StatusDirOverride = value; break;
                case "--test-flag": Opt.Flag = int.Parse(value, CultureInfo.InvariantCulture); break;
                case "--test-no-signature": Opt.NoSignature = true; break;
                case "--test-signal-activation": Opt.SignalActivation = true; break;
                case "--test-ignore-owner": Opt.IgnoreOwner = true; break;
                case "--test-title": Opt.TitlePrefix = value; break;
                case "--test-game": Opt.GameName = value; break;
                case "--test-trust-root": Opt.TrustRoot = value; break;
                case "--test-expected": Opt.ExpectedFile = value; break;
            }
        }
#endif

        static string ReadTarget(string dir)
        {
            string[] lines = File.ReadAllLines(Path.Combine(dir, "target.txt"), Encoding.UTF8);
            string path = lines.Length > 0 ? lines[0].Trim() : "";
            if (path.Length == 0 || !Path.IsPathRooted(path) || path.StartsWith("\\\\", StringComparison.Ordinal) ||
                !path.EndsWith(".exe", StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("target");
            path = Path.GetFullPath(path);
            if (!File.Exists(path)) throw new InvalidOperationException("target");
            return path;
        }

        static bool ReadFlag()
        {
#if TESTBUILD
            if (Opt.Flag >= 0) return Opt.Flag == 1;
#endif
            using (var hive = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
            using (var key = hive.OpenSubKey(@"SOFTWARE\Egoist\WXQ-Russian"))
            {
                if (key == null) return false;
                object value = key.GetValue("LauncherRU");
                return value is int && (int)value == 1;
            }
        }

        // ---------- запуск оригинала ----------

        static bool IsReparse(string path)
        {
            return (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0;
        }

        // Папка launcher\ и её родитель ({app}) не ссылки; обе папки и все файлы в launcher\ закрыты от записи обычным пользователям.
        static bool AclOk(Session s)
        {
            if (!s.AclChecked)
            {
                s.AclChecked = true;
                try
                {
                    string problem = null;
                    string parent = Path.GetDirectoryName(s.Dir.TrimEnd('\\'));
                    foreach (string folder in new[] { s.Dir, parent })
                    {
                        if (problem != null) break;
                        if (string.IsNullOrEmpty(folder)) { problem = "error"; break; }
                        problem = IsReparse(folder) ? "reparse" : Files.AclProblem(new DirectoryInfo(folder));
                    }
                    if (problem == null)
                    {
                        foreach (FileSystemInfo item in new DirectoryInfo(s.Dir).GetFileSystemInfos())
                        {
                            if ((item.Attributes & FileAttributes.ReparsePoint) != 0) { problem = "reparse"; break; }
                            problem = Files.AclProblem(item);
                            if (problem != null) break;
                        }
                    }
                    s.AclState = problem;
                }
                catch (Exception) { s.AclState = "error"; }
            }
            return s.AclState == null;
        }

        // Цель внутри Program Files (или Program Files (x86)); ни один каталог на пути и сам exe не ссылки (junction, symlink).
        static bool UnderTrustedRoot(string target)
        {
            var roots = new List<string>();
#if TESTBUILD
            if (Opt.TrustRoot != null) roots.Add(Opt.TrustRoot);
#else
            roots.Add(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles));
            roots.Add(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86));
#endif
            foreach (string root in roots)
            {
                if (string.IsNullOrEmpty(root)) continue;
                string prefix = Path.GetFullPath(root).TrimEnd('\\') + "\\";
                if (!target.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) continue;
                try
                {
                    string current = prefix.TrimEnd('\\');
                    foreach (string part in target.Substring(prefix.Length).Split('\\'))
                    {
                        current = current + "\\" + part;
                        if (IsReparse(current)) return false;
                    }
                    return true;
                }
                catch (Exception) { return false; }
            }
            return false;
        }

        // null — запускать можно. Иначе код отказа: повышенный процесс не запускает цель, если папку активатора или файл цели мог подменить обычный пользователь.
        static string TrustProblem(Session s)
        {
            if (!AclOk(s)) return "acl";
            if (!UnderTrustedRoot(s.Target)) return "untrusted-target";
            return null;
        }

        static bool StartOriginal(Session s)
        {
            if (TrustProblem(s) != null) return false;
            try { s.Proc = Launch(s); s.Owned = true; return true; }
            catch (Exception) { return false; }
        }

        static Process Launch(Session s)
        {
            var info = new ProcessStartInfo(s.Target, s.ForwardArgs) { UseShellExecute = false, WorkingDirectory = Path.GetDirectoryName(s.Target) };
            return Process.Start(info);
        }

        // ---------- подключение перевода ----------

        static void Attach(Session s)
        {
            string trust = TrustProblem(s);
            if (trust != null) throw new Refusal(trust);
            string runtimePath = Path.Combine(s.Dir, "launcher-ru-runtime.cjs");
            string dictionaryPath = Path.Combine(s.Dir, "launcher-ru.json");
            string profilesPath = Path.Combine(s.Dir, "profiles.json");
            string runtimeSha, dictionarySha;
            LoadExpected(s.Dir, out runtimeSha, out dictionarySha);
            if (!File.Exists(runtimePath) || !File.Exists(dictionaryPath) || !File.Exists(profilesPath)) throw new Refusal("files-missing");
            if (Files.Sha256(runtimePath) != runtimeSha) throw new Refusal("module-pin");
            if (Files.Sha256(dictionaryPath) != dictionarySha) throw new Refusal("dictionary-pin");

            Profile profile = FindProfile(File.ReadAllText(profilesPath, Encoding.UTF8), s.Target);
            s.Profile = profile.Build;
            string exeDir = Path.GetDirectoryName(s.Target);
            string lib = Path.Combine(exeDir, @"resources\app.asar.unpacked\node_modules\@tencent\native-resource\dist\lib");
            RequireHash(s.Target, profile.Exe, "hash-exe");
            RequireHash(Path.Combine(exeDir, @"resources\app.asar"), profile.Asar, "hash-asar");
            RequireHash(Path.Combine(lib, "skin.zip"), profile.Skin, "hash-skin");
            RequireHash(Path.Combine(lib, @"locales-i18n\launcher.json"), profile.LauncherJson, "hash-launcher-json");
            CheckSignature(s.Target, profile);

            if (GameRunning()) throw new Refusal("game-running", "skipped");

            List<int> running = FindMainProcesses(s.Target);
            if (running.Count > 1) { s.AlreadyRunning = true; throw new Refusal("launcher-ambiguous"); }
            if (running.Count == 1)
            {
                s.AlreadyRunning = true;
                s.Proc = Process.GetProcessById(running[0]);
                if (File.GetLastWriteTimeUtc(s.Target) > s.Proc.StartTime.ToUniversalTime()) throw new Refusal("stale-process");
            }
            else
            {
                s.Proc = Launch(s);
                s.Owned = true;
                if (!WaitWindow(s.Proc, Opt.WindowWaitMs)) throw new Refusal("no-window");
                Thread.Sleep(Opt.SettleMs);
                if (s.Proc.HasExited) throw new Refusal("launcher-exited");
            }
            s.StartUtc = s.Proc.StartTime.ToUniversalTime();
            s.StartTicks = s.StartUtc.Ticks;

            if (Tcp.Listeners(Opt.Port).Count > 0) throw new Refusal("port-busy");
            s.Activated = true;
            if (!Activate(s.Proc.Id)) throw new Refusal("activate-failed");

            // Окно порта: от открытия до закрытия inspector.
            var deadline = DateTime.UtcNow.AddMilliseconds(Opt.InspectorWaitMs);
            while (Tcp.Listeners(Opt.Port).Count == 0)
            {
                if (DateTime.UtcNow > deadline) throw new Refusal("inspector-timeout");
                Thread.Sleep(50);
            }
            if (!Tcp.OwnedExactly(s.Proc.Id, Opt.Port)) throw new Refusal("port-owner");

            Uri endpoint = ReadInspectorTarget(Opt.Port);
            if (!Tcp.OwnedExactly(s.Proc.Id, Opt.Port)) throw new Refusal("port-owner");
            using (var cdp = Cdp.Connect(endpoint))
            {
                CheckIdentity(cdp, s);
                if (!Tcp.OwnedExactly(s.Proc.Id, Opt.Port)) throw new Refusal("port-owner");
                Dictionary<string, object> reply = null;
                try { reply = cdp.Evaluate(InstallExpression(runtimePath, runtimeSha, dictionaryPath, dictionarySha), 5000); }
                catch (Exception) { }
                if (reply == null || reply.ContainsKey("exceptionDetails"))
                {
                    try { cdp.Evaluate(CloseExpression, 2000); } catch (Exception) { }
                    throw new Refusal("install-failed");
                }
            }
        }

        const string IdentityExpression = "({pid:process.pid,path:process.execPath,startedAtApprox:Date.now()-process.uptime()*1000})";
        const string CloseExpression = "setTimeout(()=>process.getBuiltinModule(\"inspector\").close(),200);void 0";

        static string InstallExpression(string module, string moduleSha, string dictionary, string dictionarySha)
        {
            // Одно синхронное выражение: сверка SHA, require, установка обработчиков; inspector закрывается в finally.
            return "(()=>{const r=process.getBuiltinModule(\"module\").createRequire(process.execPath);try{" +
                "const f=r(\"node:fs\"),c=r(\"node:crypto\"),p=" + Js.Serialize(module) + ",d=" + Js.Serialize(dictionary) + ";" +
                "const h=x=>c.createHash(\"sha256\").update(f.readFileSync(x)).digest(\"hex\");" +
                "if(h(p)!==" + Js.Serialize(moduleSha) + ")throw Error(\"module pin\");" +
                "if(h(d)!==" + Js.Serialize(dictionarySha) + ")throw Error(\"dictionary pin\");" +
                "let e=null;try{e=r(\"electron\")}catch(x){}" +
                "r(p).install({electron:e,resourcesPath:process.resourcesPath,dictionaryPath:d,dictionarySha256:" + Js.Serialize(dictionarySha) + "});" +
                "}finally{setTimeout(()=>process.getBuiltinModule(\"inspector\").close(),200)}return true})()";
        }

        static void LoadExpected(string dir, out string runtimeSha, out string dictionarySha)
        {
            string text = null;
            using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("launcher-expected.txt"))
            {
                if (stream != null) using (var reader = new StreamReader(stream, Encoding.UTF8)) text = reader.ReadToEnd();
            }
#if TESTBUILD
            if (text == null)
            {
                string file = Opt.ExpectedFile ?? Path.Combine(dir, "launcher-expected.txt");
                if (File.Exists(file)) text = File.ReadAllText(file, Encoding.UTF8);
            }
#endif
            runtimeSha = dictionarySha = null;
            if (text != null)
            {
                foreach (string line in text.Split('\n'))
                {
                    int eq = line.IndexOf('=');
                    if (eq <= 0) continue;
                    string key = line.Substring(0, eq).Trim(), value = line.Substring(eq + 1).Trim().ToLowerInvariant();
                    if (!Regex.IsMatch(value, "^[0-9a-f]{64}$")) continue;
                    if (key == "runtime_sha256") runtimeSha = value;
                    else if (key == "dictionary_sha256") dictionarySha = value;
                }
            }
            if (runtimeSha == null || dictionarySha == null) throw new Refusal("no-expected");
        }

        static Profile FindProfile(string json, string target)
        {
            Dictionary<string, object> root;
            IEnumerable builds;
            try
            {
                root = Js.DeserializeObject(json) as Dictionary<string, object>;
                if (root == null || !(root["schema"] is int) || (int)root["schema"] != 1) throw new Refusal("profiles-invalid");
                builds = root["builds"] as IEnumerable;
                if (builds == null || builds is string) throw new Refusal("profiles-invalid");
            }
            catch (Refusal) { throw; }
            catch (Exception) { throw new Refusal("profiles-invalid"); }
            foreach (object item in builds)
            {
                var row = item as Dictionary<string, object>;
                if (row == null) continue;
                try
                {
                    string rel = ((string)row["exe_rel"]).Replace('/', '\\').Trim('\\');
                    if (rel.Length == 0 || rel.Contains("..") || !target.EndsWith("\\" + rel, StringComparison.OrdinalIgnoreCase)) continue;
                    var sha = (Dictionary<string, object>)row["sha256"];
                    var profile = new Profile
                    {
                        Build = Convert.ToString(row["build"], CultureInfo.InvariantCulture), Thumbprint = ((string)row["signer_thumbprint"]).Replace(" ", "").ToUpperInvariant(),
                        Exe = (string)sha["exe"], Asar = (string)sha["app_asar"], Skin = (string)sha["skin_zip"], LauncherJson = (string)sha["launcher_json"]
                    };
                    if (!Regex.IsMatch(profile.Build, @"^[0-9A-Za-z._-]{1,40}$")) throw new Refusal("profiles-invalid");
                    return profile;
                }
                catch (Refusal) { throw; }
                catch (Exception) { throw new Refusal("profiles-invalid"); }
            }
            throw new Refusal("profile-unknown");
        }

        static void RequireHash(string path, string expected, string code)
        {
            if (!File.Exists(path) || !string.Equals(Files.Sha256(path), expected, StringComparison.OrdinalIgnoreCase)) throw new Refusal(code);
        }

        static void CheckSignature(string path, Profile profile)
        {
#if TESTBUILD
            if (Opt.NoSignature) return;
#endif
            string thumb = Files.SignerThumbprint(path);
            if (!string.Equals(thumb, profile.Thumbprint, StringComparison.OrdinalIgnoreCase)) throw new Refusal("thumbprint");
        }

        static bool GameRunning()
        {
            string name = "WXQ";
#if TESTBUILD
            name = Opt.GameName;
#endif
            return Process.GetProcessesByName(name).Length > 0;
        }

        // Главные процессы лаунчера с этим exe (дочерние Electron-процессы несут --type=).
        static List<int> FindMainProcesses(string target)
        {
            var result = new List<int>();
            using (var search = new ManagementObjectSearcher("SELECT ProcessId, ExecutablePath, CommandLine FROM Win32_Process"))
            using (var rows = search.Get())
            {
                foreach (ManagementBaseObject row in rows)
                {
                    string path = row["ExecutablePath"] as string, command = row["CommandLine"] as string ?? "";
                    if (path == null) continue;
                    try { if (!string.Equals(Path.GetFullPath(path), target, StringComparison.OrdinalIgnoreCase)) continue; }
                    catch (ArgumentException) { continue; }
                    if (command.IndexOf(" --type=", StringComparison.Ordinal) >= 0) continue;
                    result.Add(Convert.ToInt32(row["ProcessId"], CultureInfo.InvariantCulture));
                }
            }
            return result;
        }

        static bool WaitWindow(Process process, int ms)
        {
            var deadline = DateTime.UtcNow.AddMilliseconds(ms);
            while (DateTime.UtcNow < deadline)
            {
                process.Refresh();
                if (process.HasExited) return false;
                if (process.MainWindowHandle != IntPtr.Zero) return true;
                Thread.Sleep(200);
            }
            return false;
        }

        // Как process._debugProcess в Node (Windows): именованное отображение node-debug-handler-<pid> хранит адрес
        // обработчика сигнала в целевом процессе; запускаем в нём поток. ПРЕДПОЛОЖЕНИЕ по памяти об исходниках Node 20,
        // на живом Electron-лаунчере не проверялось.
        static bool Activate(int pid)
        {
#if TESTBUILD
            if (Opt.SignalActivation)
            {
                try { using (var signal = EventWaitHandle.OpenExisting("Local\\WXQFakeInspect-" + pid.ToString(CultureInfo.InvariantCulture))) return signal.Set(); }
                catch (Exception) { return false; }
            }
#endif
            IntPtr process = Native.OpenProcess(Native.ProcessAccess, false, pid);
            if (process == IntPtr.Zero) return false;
            IntPtr mapping = IntPtr.Zero, view = IntPtr.Zero, thread = IntPtr.Zero;
            try
            {
                mapping = Native.OpenFileMappingW(0x0004, false, "node-debug-handler-" + pid.ToString(CultureInfo.InvariantCulture)); // FILE_MAP_READ
                if (mapping == IntPtr.Zero) return false;
                view = Native.MapViewOfFile(mapping, 0x0004, 0, 0, new UIntPtr((uint)IntPtr.Size));
                if (view == IntPtr.Zero) return false;
                IntPtr handler = Marshal.ReadIntPtr(view);
                if (handler == IntPtr.Zero) return false;
                uint id;
                thread = Native.CreateRemoteThread(process, IntPtr.Zero, UIntPtr.Zero, handler, IntPtr.Zero, 0, out id);
                if (thread == IntPtr.Zero) return false;
                Native.WaitForSingleObject(thread, 2000);
                return true;
            }
            finally
            {
                if (thread != IntPtr.Zero) Native.CloseHandle(thread);
                if (view != IntPtr.Zero) Native.UnmapViewOfFile(view);
                if (mapping != IntPtr.Zero) Native.CloseHandle(mapping);
                Native.CloseHandle(process);
            }
        }

        static bool TitleOk(string title)
        {
#if TESTBUILD
            return title != null && title.StartsWith(Opt.TitlePrefix, StringComparison.Ordinal);
#else
            return title == Opt.LauncherTitle;
#endif
        }

        // /json/list: ровно одна цель, адрес только 127.0.0.1:порт (как validateTarget в launcher-startup-runtime.mjs).
        static Uri ReadInspectorTarget(int port)
        {
            string body;
            try
            {
                var request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + port.ToString(CultureInfo.InvariantCulture) + "/json/list");
                request.Proxy = null; request.AllowAutoRedirect = false; request.Timeout = 2000; request.ReadWriteTimeout = 2000;
                using (var response = (HttpWebResponse)request.GetResponse())
                using (var stream = response.GetResponseStream())
                {
                    var buffer = new byte[1 << 20];
                    int total = 0, read;
                    while (total < buffer.Length && (read = stream.Read(buffer, total, buffer.Length - total)) > 0) total += read;
                    body = Encoding.UTF8.GetString(buffer, 0, total);
                }
            }
            catch (Exception) { throw new Refusal("target-list"); }
            try
            {
                object[] list = Js.DeserializeObject(body) as object[];
                if (list == null || list.Length != 1) throw new Refusal("target-list");
                var entry = list[0] as Dictionary<string, object>;
                if (entry == null || !TitleOk(entry.ContainsKey("title") ? entry["title"] as string : null)) throw new Refusal("target-list");
                var uri = new Uri((string)entry["webSocketDebuggerUrl"]);
                if (uri.Scheme != "ws" || uri.Host != "127.0.0.1" || uri.Port != port || uri.UserInfo.Length != 0 || uri.Query.Length != 0 ||
                    uri.Fragment.Length != 0 || !Regex.IsMatch(uri.AbsolutePath, "^/[0-9a-fA-F-]+$")) throw new Refusal("target-list");
                return uri;
            }
            catch (Refusal) { throw; }
            catch (Exception) { throw new Refusal("target-list"); }
        }

        static void CheckIdentity(Cdp cdp, Session s)
        {
            Dictionary<string, object> reply;
            try { reply = cdp.Evaluate(IdentityExpression, 3000); }
            catch (Exception) { throw new Refusal("identity"); }
            try
            {
                if (reply.ContainsKey("exceptionDetails")) throw new Refusal("identity");
                var value = (Dictionary<string, object>)((Dictionary<string, object>)reply["result"])["value"];
                double started = Convert.ToDouble(value["startedAtApprox"], CultureInfo.InvariantCulture);
                double expected = (s.StartUtc - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalMilliseconds;
                bool same = Convert.ToInt32(value["pid"], CultureInfo.InvariantCulture) == s.Proc.Id &&
                    string.Equals(Path.GetFullPath((string)value["path"]), s.Target, StringComparison.OrdinalIgnoreCase) &&
                    Math.Abs(started - expected) <= Opt.MaxIdentityDriftMs;
                if (!same) throw new Refusal("identity");
            }
            catch (Refusal) { throw; }
            catch (Exception) { throw new Refusal("identity"); }
        }

        // Порт должен закрыться за 4 с. Иначе свой процесс просим закрыться (CloseMainWindow, без Kill: он мог начать автообновление),
        // чужой не трогаем. null — порт закрыт.
        static string EnsurePortClosed(Session s)
        {
            if (s.Proc == null) return null;
            int pid = s.Proc.Id;
            var deadline = DateTime.UtcNow.AddMilliseconds(Opt.CloseWaitMs);
            while (true)
            {
                bool open = false;
                foreach (Listener l in Tcp.Listeners(Opt.Port)) if (l.Pid == pid) open = true;
                if (!open) return null;
                if (DateTime.UtcNow > deadline) break;
                Thread.Sleep(150);
            }
            if (!s.Owned) return "port-open-left";
            try
            {
                s.Proc.Refresh();
                if (s.Proc.HasExited) return null;
                if (s.Proc.StartTime.ToUniversalTime().Ticks != s.StartTicks) return "port-open-left";
                s.CloseTried = true;
                s.Proc.CloseMainWindow();
                if (s.Proc.WaitForExit(Opt.CloseWaitMs)) return "port-open-closed";
            }
            catch (Exception) { }
            return "port-open-left";
        }

        // Закрыть процесс лаунчера, который запустили мы (мягко, без Kill). true — процесса больше нет.
        static bool EndOwned(Session s)
        {
            if (s.Proc == null) return true;
            try
            {
                s.Proc.Refresh();
                if (s.Proc.HasExited) return true;
                if (s.Proc.StartTime.ToUniversalTime().Ticks != s.StartTicks || s.CloseTried) return false;   // мягкую просьбу уже передали и подождали
                s.CloseTried = true;
                s.Proc.CloseMainWindow();
                return s.Proc.WaitForExit(Opt.CloseWaitMs);
            }
            catch (Exception) { return false; }
        }

        static void WriteStatus(string profile, string result, string code)
        {
            try
            {
                Directory.CreateDirectory(Opt.StatusDir);
                if ((File.GetAttributes(Opt.StatusDir) & FileAttributes.ReparsePoint) != 0) return;
                string file = Path.Combine(Opt.StatusDir, "launcher-status.json");
                if (File.Exists(file) && (File.GetAttributes(file) & FileAttributes.ReparsePoint) != 0) return;
                string text = "{\"time\":" + Js.Serialize(DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture)) +
                    ",\"profile\":" + (profile == null ? "null" : Js.Serialize(profile)) + ",\"result\":" + Js.Serialize(result) + ",\"code\":" + Js.Serialize(code) + "}\n";
                File.WriteAllText(file, text, new UTF8Encoding(false));
            }
            catch (Exception) { }
        }
    }

    // Минимальный синхронный клиент CDP: только Runtime.evaluate, без Debugger.enable и других доменов.
    sealed class Cdp : IDisposable
    {
        static readonly JavaScriptSerializer Js = new JavaScriptSerializer { MaxJsonLength = 2 * 1024 * 1024 };
        ClientWebSocket socket;
        int nextId;

        public static Cdp Connect(Uri endpoint)
        {
            var cdp = new Cdp { socket = new ClientWebSocket() };
            try
            {
                cdp.socket.Options.Proxy = null;
                using (var cts = new CancellationTokenSource(2000)) cdp.socket.ConnectAsync(endpoint, cts.Token).Wait();
                return cdp;
            }
            catch (Exception) { cdp.Dispose(); throw new Refusal("ws-failed"); }
        }

        // Возвращает поле result ответа (с result/exceptionDetails). Ошибка протокола — исключение.
        public Dictionary<string, object> Evaluate(string expression, int timeoutMs)
        {
            int id = ++nextId;
            var request = new Dictionary<string, object>
            {
                { "id", id }, { "method", "Runtime.evaluate" },
                { "params", new Dictionary<string, object> { { "expression", expression }, { "returnByValue", true }, { "awaitPromise", false } } }
            };
            using (var cts = new CancellationTokenSource(timeoutMs))
            {
                var bytes = Encoding.UTF8.GetBytes(Js.Serialize(request));
                socket.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, cts.Token).Wait();
                while (true)
                {
                    var message = new MemoryStream();
                    var buffer = new byte[16384];
                    WebSocketReceiveResult part;
                    do
                    {
                        part = socket.ReceiveAsync(new ArraySegment<byte>(buffer), cts.Token).Result;
                        if (part.MessageType == WebSocketMessageType.Close) throw new InvalidOperationException("closed");
                        message.Write(buffer, 0, part.Count);
                        if (message.Length > (1 << 20)) throw new InvalidOperationException("too-large");
                    } while (!part.EndOfMessage);
                    var reply = Js.DeserializeObject(Encoding.UTF8.GetString(message.ToArray())) as Dictionary<string, object>;
                    if (reply == null || !reply.ContainsKey("id") || Convert.ToInt32(reply["id"], CultureInfo.InvariantCulture) != id) continue;
                    if (reply.ContainsKey("error")) throw new InvalidOperationException("protocol-error");
                    return reply["result"] as Dictionary<string, object>;
                }
            }
        }

        public void Dispose()
        {
            if (socket == null) return;
            try { using (var cts = new CancellationTokenSource(500)) socket.CloseOutputAsync(WebSocketCloseStatus.NormalClosure, "", cts.Token).Wait(); } catch (Exception) { }
            socket.Dispose();
            socket = null;
        }
    }
}
