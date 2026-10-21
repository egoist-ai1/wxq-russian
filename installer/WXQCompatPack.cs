using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;

namespace Egoist.WXQRussian
{
    // Изменённый текст упаковывает штатный SDK игры. Метаданные и LZ4 не угадываются.
    static class CompatPacker
    {
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate void LogFn(int level, IntPtr text);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate void PrintFn(IntPtr text);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] [return: MarshalAs(UnmanagedType.U1)]
        delegate bool InitFn(LogFn log, PrintFn print, IntPtr extra);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int CreateFn(byte[] config, byte[] destination);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int CacheFn(byte[] path);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] [return: MarshalAs(UnmanagedType.U1)]
        delegate bool BindFn(int packer, int cache);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] [return: MarshalAs(UnmanagedType.U1)]
        delegate bool CompressFn(int packer, uint method);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] [return: MarshalAs(UnmanagedType.U1)]
        delegate bool AddFn(int packer, byte[] package, [MarshalAs(UnmanagedType.U1)] bool flag, byte[] raw, byte[] files);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] [return: MarshalAs(UnmanagedType.U1)] delegate bool SaveFn(int packer);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate void DestroyFn(int handle);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern IntPtr LoadLibraryEx(string path, IntPtr reserved, uint flags);
        [DllImport("kernel32.dll", CharSet = CharSet.Ansi, ExactSpelling = true)]
        static extern IntPtr GetProcAddress(IntPtr module, string name);
        [DllImport("kernel32.dll")] static extern bool FreeLibrary(IntPtr module);
        [DllImport("kernel32.dll")] static extern uint SetErrorMode(uint mode);

        static readonly LogFn SilentLog = delegate(int level, IntPtr text) { };
        static readonly PrintFn SilentPrint = delegate(IntPtr text) { };
        static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);

        static T Bind<T>(IntPtr module, string name) where T : class
        {
            IntPtr pointer = GetProcAddress(module, name);
            if (pointer == IntPtr.Zero) throw new IOException("В SDK игры нет функции упаковки " + name + ".");
            return Marshal.GetDelegateForFunctionPointer(pointer, typeof(T)) as T;
        }

        static byte[] Z(string value)
        {
            if (value.IndexOf('\0') >= 0) throw new IOException("Недопустимый путь упаковки.");
            return Utf8.GetBytes(value.Replace('\\', '/') + "\0");
        }

        static uint VersionNumber(string version)
        {
            string[] parts = (version ?? "").Split('.');
            if (parts.Length != 4) throw new IOException("Версия игры не подходит для штатной упаковки ресурсов.");
            uint result = 0;
            foreach (string text in parts)
            {
                uint part;
                if (!uint.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out part) || part > 255)
                    throw new IOException("Версия игры не подходит для штатной упаковки ресурсов.");
                result = (result << 8) | part;
            }
            return result;
        }

        // directory находится в защищённой папке новой резервной копии, а не в игре.
        public static Dictionary<ulong, OverrideResource> Pack(string dll, string directory, string package,
            string version, int revision, IDictionary<ulong, byte[]> payloads)
        {
            return PackCore(dll, directory, package, version, revision, null, payloads);
        }

        public static Dictionary<ulong, OverrideResource> PackFiles(string dll, string directory, string package,
            string version, int revision, IDictionary<ulong, string> payloads)
        {
            return PackCore(dll, directory, package, version, revision, payloads, null);
        }

        static Dictionary<ulong, OverrideResource> PackCore(string dll, string directory, string package,
            string version, int revision, IDictionary<ulong, string> paths, IDictionary<ulong, byte[]> buffers)
        {
            var ids = (paths != null ? paths.Keys : buffers.Keys).OrderBy(id => id).ToList();
            if (ids.Count == 0) return new Dictionary<ulong, OverrideResource>();
            if (Directory.Exists(directory)) throw new IOException("Папка новой упаковки уже существует.");
            Directory.CreateDirectory(directory);
            string raw = Path.Combine(directory, "raw"), packed = Path.Combine(directory, "packed");
            Directory.CreateDirectory(raw);
            var lines = new List<string>();
            var expectedSha = new Dictionary<ulong, string>();
            var expectedBytes = new Dictionary<ulong, long>();
            foreach (ulong id in ids)
            {
                string hex = id.ToString("x16", CultureInfo.InvariantCulture);
                string path = Path.Combine(raw, hex + ".bin");
                if (paths != null) File.Copy(paths[id], path, false);
                else File.WriteAllBytes(path, buffers[id]);
                Util.FlushToDisk(path);
                expectedSha[id] = Util.Sha(path);
                expectedBytes[id] = new FileInfo(path).Length;
                // ID задаётся явно; alias не используется как ключ сопоставления старой и новой игры.
                lines.Add(id.ToString(CultureInfo.InvariantCulture) + "|0|0|/" + hex + ".bin|" +
                    path.Replace('\\', '/') + "|0,327681,307200;");
            }
            string files = Path.Combine(directory, "files.txt"), config = Path.Combine(directory, "pack-config.json");
            File.WriteAllText(files, string.Join("\n", lines) + "\n", Utf8);
            var settings = new Dictionary<string, object> {
                { "PackLog", false }, { "Version", VersionNumber(version) }, { "BuildID", revision },
                { "MaxStorageSize", 1073741824 }, { "AllInOne", false }, { "InternalPackage", "0" },
                { "ExtraExportDataPackage", "" }, { "WriteVersion", true }, { "WriteBuildID", true }
            };
            File.WriteAllText(config, new JavaScriptSerializer().Serialize(settings), Utf8);
            string emptyFiles = Path.Combine(directory, "internal-files.txt");
            File.WriteAllText(emptyFiles, "", Utf8);
            uint previousMode = SetErrorMode(0x0001 | 0x0002 | 0x8000);
            IntPtr module = LoadLibraryEx(dll, IntPtr.Zero, 0x00000100 | 0x00001000);
            SetErrorMode(previousMode);
            if (module == IntPtr.Zero) throw new IOException("Не удалось загрузить SDK упаковки игры.");
            try
            {
                var initialize = Bind<InitFn>(module, "VFS_Initialize");
                var create = Bind<CreateFn>(module, "VFS_CreatePacker");
                var createCache = Bind<CacheFn>(module, "VFS_CreatePackerCacheQuery");
                var bindCache = Bind<BindFn>(module, "VFS_PackerBindCacheQuery");
                var compress = Bind<CompressFn>(module, "VFS_PackerSetGlobalIndexCompressMethod");
                var add = Bind<AddFn>(module, "VFS_PackerAddPackage");
                var save = Bind<SaveFn>(module, "VFS_PackerSave");
                var destroy = Bind<DestroyFn>(module, "VFS_DestroyPacker");
                var destroyCache = Bind<DestroyFn>(module, "VFS_DestroyPackerCacheQuery");
                if (!initialize(SilentLog, SilentPrint, IntPtr.Zero)) throw new IOException("SDK упаковки не готов.");
                int packer = create(Z(config), Z(packed));
                if (packer < 0) throw new IOException("SDK не создал упаковщик ресурсов.");
                int cache = -1;
                try
                {
                    cache = createCache(Z(Path.Combine(directory, "packer-cache")));
                    if (cache < 0 || !bindCache(packer, cache) || !compress(packer, 1) ||
                        !add(packer, Z("0"), true, Z(raw), Z(emptyFiles)) ||
                        !add(packer, Z(package), true, Z(raw), Z(files)) || !save(packer))
                        throw new IOException("SDK не смог упаковать совместимые строки.");
                }
                finally
                {
                    destroy(packer);
                    if (cache >= 0) destroyCache(cache);
                    GC.KeepAlive(SilentLog);
                    GC.KeepAlive(SilentPrint);
                }
            }
            finally { FreeLibrary(module); }

            // Отдельный разбор реальных файлов SDK: никаких лишних ID и ни одного изменённого байта payload.
            byte[] ignored;
            var result = OverridePackage.Read(Path.Combine(packed, package, package + ".db"),
                Path.Combine(packed, package, package + "_0.db"), out ignored, buffers != null).ToDictionary(r => r.Id);
            if (result.Count != ids.Count) throw new IOException("SDK изменил набор упакованных ресурсов.");
            foreach (ulong id in ids)
            {
                OverrideResource resource;
                if (!result.TryGetValue(id, out resource) || resource.Sha != expectedSha[id] ||
                    resource.ChunkInfo.Sum(chunk => (long)chunk[2]) != expectedBytes[id])
                    throw new IOException("SDK изменил упакованный текст.");
            }
            return result;
        }
    }
}
