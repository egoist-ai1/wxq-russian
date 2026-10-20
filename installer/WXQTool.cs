// Вспомогательная программа установщика «Egoist · Русификатор Honor of Kings ACE».
//
// Воспроизводит проверки и откат из scripts/game_layer_deploy.py и scripts/pilot_contract.py:
// поиск установки, версия игры, SHA prime-файлов, чужие записи в override, процессы игры,
// резервная копия, атомарная замена файлов, откат по receipt. Ничего не пишет в prime.
//
// Собирается штатным csc.exe из .NET Framework 4 (есть в каждой Windows 10/11), C# 5.
// Команды: find, status, check, backup, unpack, verify, restore, purge, update, task-register, task-remove.
// update [--check-only]: автообновление из GitHub Releases (подписанный update.json), см. раздел «автообновление» в конце файла.
// Результат команды пишется в файл --out строками «ключ=значение» (UTF-8 с BOM).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;

namespace Egoist.WXQRussian
{
    // Отказ с понятным текстом: ничего не изменено (или изменения уже откатаны).
    class StopException : Exception
    {
        public string Code;
        public StopException(string code, string message) : base(message) { Code = code; }
    }

    static class Util
    {
        public static string Sha(string path)
        {
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 1 << 20))
            using (var hash = new SHA256CryptoServiceProvider())
            {
                // Без --progress считаем как раньше, одним вызовом; с ним — кусками по 1 МиБ, чтобы полоса двигалась плавно.
                if (!Progress.Active) return Hex(hash.ComputeHash(stream));
                Progress.Next(path);
                var buffer = new byte[1 << 20];
                int read;
                while ((read = stream.Read(buffer, 0, buffer.Length)) > 0)
                {
                    hash.TransformBlock(buffer, 0, read, null, 0);
                    Progress.Add(read);
                }
                hash.TransformFinalBlock(buffer, 0, 0);
                return Hex(hash.Hash);
            }
        }

        // Копирование как File.Copy; с --progress идёт кусками по 1 МиБ и сообщает прогресс (время и атрибуты переносятся).
        public static void Copy(string source, string destination, bool overwrite)
        {
            if (!Progress.Active) { File.Copy(source, destination, overwrite); return; }
            Progress.Next(source);
            using (var input = new FileStream(source, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 1 << 20))
            using (var output = new FileStream(destination, overwrite ? FileMode.Create : FileMode.CreateNew, FileAccess.Write, FileShare.None, 1 << 20))
            {
                var buffer = new byte[1 << 20];
                int read;
                while ((read = input.Read(buffer, 0, buffer.Length)) > 0)
                {
                    output.Write(buffer, 0, read);
                    Progress.Add(read);
                }
            }
            File.SetCreationTimeUtc(destination, File.GetCreationTimeUtc(source));
            File.SetLastWriteTimeUtc(destination, File.GetLastWriteTimeUtc(source));
            File.SetAttributes(destination, File.GetAttributes(source));
        }

        public static string Hex(byte[] bytes)
        {
            var text = new StringBuilder(bytes.Length * 2);
            foreach (byte b in bytes) text.Append(b.ToString("x2", CultureInfo.InvariantCulture));
            return text.ToString();
        }

        public static byte[] FromHex(string hex)
        {
            if (hex == null || hex.Length % 2 != 0) throw new FormatException("hex");
            var bytes = new byte[hex.Length / 2];
            for (int i = 0; i < bytes.Length; i++) bytes[i] = Convert.ToByte(hex.Substring(2 * i, 2), 16);
            return bytes;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool GetDiskFreeSpaceEx(string directory, out ulong freeToCaller, out ulong total, out ulong totalFree);

        // Свободное место на томе пути (доступное текущему пользователю) или -1, если узнать не удалось (тогда проверку не блокируем).
        // Тестовая сборка: WXQ_RU_TEST_FREE_BYTES подменяет ответ (проверка отказа disk_space без заполнения диска).
        public static long FreeBytes(string path)
        {
#if TESTBUILD
            long fake;
            string text = Environment.GetEnvironmentVariable("WXQ_RU_TEST_FREE_BYTES");
            if (text != null && long.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out fake)) return fake;
#endif
            try
            {
                string probe = Path.GetFullPath(path);
                while (!Directory.Exists(probe))
                {
                    probe = Path.GetDirectoryName(probe);
                    if (probe == null) return -1;
                }
                ulong caller, total, free;
                return GetDiskFreeSpaceEx(probe, out caller, out total, out free) ? (long)Math.Min(caller, (ulong)long.MaxValue) : -1;
            }
            catch (Exception) { return -1; }
        }

        // Диск заполнен: ERROR_DISK_FULL (112) и ERROR_HANDLE_DISK_FULL (39) в младших 16 битах HRESULT исключения ввода-вывода.
        public static bool IsDiskFull(Exception error)
        {
            var io = error as IOException;
            if (io == null) return false;
            int code = io.HResult & 0xFFFF;
            return code == 112 || code == 39;
        }

        public static void FlushToDisk(string path)
        {
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Write, FileShare.ReadWrite))
                stream.Flush(true);
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern uint GetLongPathName(string shortPath, StringBuilder longPath, uint size);

        // Короткие имена 8.3 (PROGRA~1) превращаются в длинные, чтобы сравнение корней с путями процессов не промахивалось. Несуществующий путь остаётся как есть.
        public static string LongPath(string path)
        {
            try
            {
                if (path == null || path.IndexOf('~') < 0) return path;
                var text = new StringBuilder(1024);
                uint n = GetLongPathName(path, text, (uint)text.Capacity);
                return n > 0 && n < text.Capacity ? text.ToString() : path;
            }
            catch (Exception) { return path; }
        }

        public static string NormPath(string path)
        {
            return LongPath(Path.GetFullPath(path).TrimEnd('\\', '/'));
        }

        public static bool SamePath(string a, string b)
        {
            return string.Equals(NormPath(a), NormPath(b), StringComparison.OrdinalIgnoreCase);
        }

        public static bool IsReparse(string path)
        {
            try { return (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0; }
            catch (IOException) { return false; }
        }

        // Временный файл рядом с целью не должен быть ссылкой: запись через неё ушла бы в чужое место.
        public static void RequireNotLink(string path)
        {
            if ((File.Exists(path) || Directory.Exists(path)) && IsReparse(path))
                throw new IOException("на месте временного файла лежит ссылка: " + path);
        }

        public static void ReplaceFile(string stage, string destination)
        {
            if (File.Exists(destination)) File.Replace(stage, destination, null);
            else File.Move(stage, destination);
        }

        public static Dictionary<string, object> ReadJson(string path)
        {
            var serializer = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
            return serializer.DeserializeObject(File.ReadAllText(path, Encoding.UTF8)) as Dictionary<string, object>;
        }

        public static void WriteJson(string path, object value)
        {
            var serializer = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            string temporary = path + ".new";
            File.WriteAllText(temporary, serializer.Serialize(value), new UTF8Encoding(false));
            FlushToDisk(temporary);
            ReplaceFile(temporary, path);
        }

        public static IEnumerable<object> Items(object value)
        {
            var list = value as System.Collections.IEnumerable;
            if (list == null || value is string) return new object[0];
            return list.Cast<object>();
        }

        public static string Str(Dictionary<string, object> row, string key)
        {
            object value;
            return row.TryGetValue(key, out value) && value != null ? Convert.ToString(value, CultureInfo.InvariantCulture) : null;
        }

        public static long Num(Dictionary<string, object> row, string key)
        {
            return Convert.ToInt64(row[key], CultureInfo.InvariantCulture);
        }
    }

    // Журнал без секретов: только команды, пути игры, имена файлов и SHA.
    static class Log
    {
        public static string File_;
        public static void Write(string text)
        {
            if (File_ == null) return;
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(File_));
                if (File.Exists(File_) && new FileInfo(File_).Length > 1024 * 1024)
                {
                    string old = File_ + ".1";
                    if (File.Exists(old)) File.Delete(old);
                    File.Move(File_, old);
                }
                File.AppendAllText(File_, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture) + "  " + text + Environment.NewLine, new UTF8Encoding(true));
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }

    // Живой прогресс для окна установщика (параметр --progress <файл>). Без параметра все методы ничего не делают.
    // Файл — UTF-8 без BOM, строки «ключ=значение»: phase, done, total, item, index, count, state (running|done).
    // Пишется целиком во временный файл рядом и подменяется атомарно, не чаще раза в ~120 мс, плюс в начале и в конце.
    // Любая ошибка записи проглатывается: прогресс никогда не роняет команду.
    static class Progress
    {
        public static bool Active;
        static string path, phase = "", item = "", state = "running";
        static long done, total, raw;
        static int index, count;
        static Stopwatch clock;
        static long lastWrite;

        public static void Start(string file, string command)
        {
            if (string.IsNullOrEmpty(file)) return;
            try { path = Path.GetFullPath(file); }
            catch (Exception) { return; }
            phase = command;
            baseCommand = command;
            Active = true;
            clock = Stopwatch.StartNew();
            Flush(false);
        }

        // Временная фаза «closing»: помощник закрывает мешающие процессы, item - их имена. Вызов с name == null возвращает фазу команды.
        static string baseCommand = "";

        public static void Phase(string name, string text)
        {
            if (!Active) return;
            phase = name ?? baseCommand;
            item = (text ?? "").Replace("\r", " ").Replace("\n", " ");
            Flush(false);
        }

        // Объём работы считается заранее по размерам файлов (без чтения содержимого); вызывается один раз после разбора плана.
        public static void SetPlan(long totalBytes, int files)
        {
            if (!Active) return;
            total = Math.Max(0, totalBytes);
            count = Math.Max(0, files);
            if (done > total) done = total;
            Flush(false);
        }

        // Начался следующий файл.
        // Установщик просит остановиться файлом «<файл прогресса>.cancel». Проверка только здесь, перед началом очередного файла (чтение, хеш, копирование),
        // никогда посреди записи; срабатывает один раз, чтобы откат после остановки не прерывался тем же флагом.
        static bool cancelFired;

        public static void Next(string file)
        {
            if (!Active) return;
            if (!cancelFired && path != null && File.Exists(path + ".cancel"))
            {
                cancelFired = true;
                throw new StopException("cancelled", "Операция остановлена между файлами по просьбе установщика (помощник долго не отвечал). Если запись уже шла, она откатана или будет откатана кнопкой «Удалить перевод».");
            }
            item = Short(file);
            if (index < int.MaxValue) index++;
            if (count > 0 && index > count) index = count;
        }

        // Обработано n байт; done только растёт и не выше total.
        public static void Add(long n)
        {
            if (!Active || n <= 0) return;
            raw += n;
            done = Math.Min(total, done + n);
            if (clock.ElapsedMilliseconds - lastWrite >= 120) Flush(false);
        }

        // Файл пропущен без чтения (результат взят из памятки), но в объёме работы он учтён.
        public static void Skip(string file, long n)
        {
            if (!Active) return;
            Next(file);
            Add(n);
        }

        public static void Finish()
        {
            if (!Active) return;
            done = total;
            state = "done";
            if (count > 0) index = count;
            Flush(true);
            Log.Write("прогресс: оценка " + total + " байт, фактически обработано " + raw);
        }

        // «пакет/имя» без полного пути; временный суффикс .ru-stage-… отбрасывается.
        static string Short(string file)
        {
            try
            {
                string name = Path.GetFileName(file);
                int cut = name.IndexOf(".ru-", StringComparison.Ordinal);
                if (cut > 0) name = name.Substring(0, cut);
                string dir = Path.GetFileName(Path.GetDirectoryName(file) ?? "");
                string text = dir.Length > 0 ? dir + "/" + name : name;
                return text.Replace("\r", " ").Replace("\n", " ");
            }
            catch (Exception) { return ""; }
        }

        static void Flush(bool last)
        {
            lastWrite = clock.ElapsedMilliseconds;
            try
            {
                string text = "phase=" + phase + "\r\ndone=" + done + "\r\ntotal=" + total + "\r\nitem=" + item
                    + "\r\nindex=" + index + "\r\ncount=" + count + "\r\nstate=" + (last ? "done" : state) + "\r\n";
                string dir = Path.GetDirectoryName(path);
                if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                string temporary = path + ".tmp";
                File.WriteAllText(temporary, text, new UTF8Encoding(false));
                // Читатель может держать файл открытым: подмена повторяется; на промежуточных записях ждём недолго и пропускаем.
                int attempts = last ? 60 : 3;
                for (int i = 0; i < attempts; i++)
                {
                    try
                    {
                        if (File.Exists(path)) File.Replace(temporary, path, null);
                        else File.Move(temporary, path);
                        return;
                    }
                    catch (IOException) { Thread.Sleep(last ? 25 : 4); }
                    catch (UnauthorizedAccessException) { Thread.Sleep(last ? 25 : 4); }
                }
                try { File.Delete(temporary); } catch (Exception) { }
            }
            catch (Exception) { }
        }
    }

    // Чтение индекса контейнера QtsVFS (порт QtsContainer из scripts/game_inspect.py, режим raw_values).
    class Catalog
    {
        byte[] data;
        public Dictionary<ulong, List<long>> Records = new Dictionary<ulong, List<long>>();
        public int Invalid;

        int I32(long offset)
        {
            if (offset < 0 || offset + 4 > data.Length) throw new FormatException("range");
            return BitConverter.ToInt32(data, (int)offset);
        }
        uint U32(long offset)
        {
            if (offset < 0 || offset + 4 > data.Length) throw new FormatException("range");
            return BitConverter.ToUInt32(data, (int)offset);
        }
        ulong U64(long offset)
        {
            if (offset < 0 || offset + 8 > data.Length) throw new FormatException("range");
            return BitConverter.ToUInt64(data, (int)offset);
        }
        int U16(long offset)
        {
            if (offset < 0 || offset + 2 > data.Length) throw new FormatException("range");
            return BitConverter.ToUInt16(data, (int)offset);
        }

        List<int[]> Tables(int offset)
        {
            var rows = new List<int[]>();
            if (offset == -1) return rows;
            int count = I32(offset + 4);
            if (count < 0 || count > 1000000 || (long)offset + 24 + 24L * count > data.Length) throw new FormatException("table");
            for (int i = 0; i < count; i++)
            {
                var row = new int[6];
                for (int k = 0; k < 6; k++) row[k] = I32(offset + 24 + 24L * i + 4 * k);
                rows.Add(row);
            }
            return rows;
        }

        public static Catalog Parse(byte[] bytes)
        {
            var catalog = new Catalog { data = bytes };
            catalog.Index();
            return catalog;
        }

        // Чанковый режим (хранилище <N>_0.db): запись = <ii> stored(=сжатый+4), распакованный размер на +24/+28, нагрузка с +32,
        // трейлер <Qii> (id, main, sub) на выровненном по 4 байтам конце нагрузки. Порт QtsContainer._index из game_inspect.py.
        public static Catalog ParseChunks(byte[] bytes)
        {
            var catalog = new Catalog { data = bytes, chunked = true };
            catalog.Index();
            foreach (var list in catalog.Chunks.Values)
                list.Sort((a, b) => a.Main != b.Main ? a.Main.CompareTo(b.Main) : a.Sub.CompareTo(b.Sub));
            return catalog;
        }

        bool chunked;
        public Dictionary<ulong, List<ChunkRef>> Chunks = new Dictionary<ulong, List<ChunkRef>>();
        const int MaxChunkBytes = 256 * 1024 * 1024;
        const long MaxEntryBytes = 64L * 1024 * 1024;

        // Разобранный (не распакованный) чанк: смещение нагрузки в файле, размеры, координаты (main, sub).
        public class ChunkRef { public long PayloadOffset; public int Compressed, Uncompressed, Main, Sub; }

        // Копия сжатой нагрузки чанка.
        public byte[] ChunkBytes(ChunkRef chunk)
        {
            var value = new byte[chunk.Compressed];
            Buffer.BlockCopy(data, (int)chunk.PayloadOffset, value, 0, chunk.Compressed);
            return value;
        }

        // Декодированное содержимое ресурса (порт decode_entry): только lz4, иначе FormatException.
        public byte[] DecodeEntry(ulong id)
        {
            List<ChunkRef> list;
            if (!Chunks.TryGetValue(id, out list) || list.Count == 0) throw new FormatException("entry");
            long total = 0;
            foreach (var c in list) total += c.Uncompressed;
            if (total > MaxEntryBytes) throw new FormatException("entry size");
            var result = new byte[total];
            long at = 0;
            foreach (var c in list)
            {
                if (Codec(c.PayloadOffset, c.Compressed) != "lz4") throw new FormatException("codec");
                Lz4.Decode(data, (int)c.PayloadOffset, c.Compressed, result, (int)at, c.Uncompressed);
                at += c.Uncompressed;
            }
            return result;
        }

        string Codec(long payload, int size)
        {
            if (size >= 4 && data[payload] == 0x28 && data[payload + 1] == 0xb5 && data[payload + 2] == 0x2f && data[payload + 3] == 0xfd) return "zstd";
            if (size >= 2 && (data[payload] == 0x8c || data[payload] == 0xcc) && data[payload + 1] == 0x0c) return "oodle";
            return "lz4";
        }

        void Index()
        {
            if (data.Length < 80 || U64(0) != 0x0403020102000001UL) throw new FormatException("magic");
            int entries2 = I32(16 + 8 * 5), entries3 = I32(16 + 8 * 6);
            var tables = Tables(entries2).Concat(Tables(entries3)).ToList();
            var blocks = new HashSet<int>();
            foreach (var row in tables) for (int k = 0; k < 3; k++) if (row[k] >= 0) blocks.Add(row[k]);
            var offsets = new HashSet<int>();
            foreach (int block in blocks)
            {
                int count = U16(block + 4080 + 12);
                int type = U16(block + 4080 + 14);
                if (type == 2) continue;
                if (count < 0 || count > 510) throw new FormatException("block");
                for (int i = 0; i < count; i++) offsets.Add(I32(block + 2040L + 4 * i));
            }
            var pending = new List<long>(offsets.Where(v => v >= 0).OrderBy(v => v).Select(v => (long)v));
            var visited = new HashSet<long>();
            while (pending.Count > 0)
            {
                long offset = pending[pending.Count - 1];
                pending.RemoveAt(pending.Count - 1);
                if (!visited.Add(offset)) continue;
                int own = I32(offset), next = I32(offset + 4);
                if (own != offset) { Invalid++; continue; }
                if (chunked)
                {
                    long compressed = (long)I32(offset + 24) - 4;
                    long raw = I32(offset + 28);
                    long start = offset + 32;
                    if (compressed < 0 || compressed > data.Length || raw < 0 || raw > MaxChunkBytes) throw new FormatException("chunk");
                    long end = (start + compressed + 3) & ~3L;
                    ulong cid = U64(end);
                    int main = I32(end + 8), sub = I32(end + 12);
                    if (start + compressed > data.Length) throw new FormatException("chunk bounds");
                    List<ChunkRef> chunkList;
                    if (!Chunks.TryGetValue(cid, out chunkList)) { chunkList = new List<ChunkRef>(); Chunks[cid] = chunkList; }
                    chunkList.Add(new ChunkRef { PayloadOffset = start, Compressed = (int)compressed, Uncompressed = (int)raw, Main = main, Sub = sub });
                    if (next != -1 && !visited.Contains(next)) pending.Add(next);
                    continue;
                }
                long size = U32(offset + 24);
                long payload = offset + 28;
                if (payload + size > data.Length) throw new FormatException("record");
                long trailer = (payload + size + 3) & ~3L;
                ulong id = U64(trailer);
                List<long> list;
                if (!Records.TryGetValue(id, out list)) { list = new List<long>(); Records[id] = list; }
                list.Add(offset);
                if (next != -1 && !visited.Contains(next)) pending.Add(next);
            }
        }

        public List<byte[]> RawValues(ulong id)
        {
            var values = new List<byte[]>();
            List<long> list;
            if (!Records.TryGetValue(id, out list)) return values;
            foreach (long offset in list)
            {
                long size = U32(offset + 24);
                if (offset + 28 + size > data.Length) throw new FormatException("value");
                var value = new byte[size];
                Buffer.BlockCopy(data, (int)(offset + 28), value, 0, (int)size);
                values.Add(value);
            }
            return values;
        }

        // Заголовок меняется в проверенном пустом шаблоне; старые записи и их байты в новый каталог не переносятся.
        public static byte[] HeaderOnly(byte[] header)
        {
            byte[] bytes;
            using (var stream = System.Reflection.Assembly.GetExecutingAssembly().GetManifestResourceStream("cache-template.db"))
            {
                if (stream == null) throw new StopException("payload", "В установщике нет шаблона очистки кэша. Скачайте установщик заново.");
                using (var memory = new MemoryStream()) { stream.CopyTo(memory); bytes = memory.ToArray(); }
            }
            var template = Parse(bytes);
            const ulong id = 0xe12bade797f29f55UL;
            if (header.Length != 120 || template.Invalid != 0 || template.Records.Count != 1 ||
                !template.Records.ContainsKey(id) || template.RawValues(id).Count != 1 || template.RawValues(id)[0].Length != 120)
                throw new StopException("payload", "Шаблон очистки кэша повреждён. В игру ничего не записано.");
            Buffer.BlockCopy(header, 0, bytes, (int)template.Records[id][0] + 28, header.Length);
            if (!Parse(bytes).RawValues(id)[0].SequenceEqual(header)) throw new FormatException("clean header");
            return bytes;
        }
    }

    // Raw LZ4 block (порт lz4_block_decode из game_inspect.py): строгие границы входа и выхода, любое нарушение - FormatException.
    static class Lz4
    {
        static long Extended(byte[] src, ref int pos, int end, int baseValue)
        {
            long value = baseValue;
            if (baseValue != 15) return value;
            while (true)
            {
                if (pos >= end) throw new FormatException("lz4 length");
                int b = src[pos++];
                value += b;
                if (b != 255) break;
            }
            return value;
        }

        public static void Decode(byte[] src, int start, int length, byte[] dst, int dstStart, int expected)
        {
            if (start < 0 || length < 0 || (long)start + length > src.Length || dstStart < 0 || expected < 0 || (long)dstStart + expected > dst.Length) throw new FormatException("lz4 range");
            int pos = start, end = start + length;
            long produced = 0;
            while (pos < end)
            {
                int token = src[pos++];
                long literal = Extended(src, ref pos, end, token >> 4);
                if (pos + literal > end || produced + literal > expected) throw new FormatException("lz4 literal");
                Buffer.BlockCopy(src, pos, dst, (int)(dstStart + produced), (int)literal);
                pos += (int)literal;
                produced += literal;
                if (pos == end) break;
                if (pos + 2 > end) throw new FormatException("lz4 offset");
                int offset = src[pos] | (src[pos + 1] << 8);
                pos += 2;
                if (offset == 0 || offset > produced) throw new FormatException("lz4 match offset");
                long match = Extended(src, ref pos, end, token & 15) + 4;
                if (produced + match > expected) throw new FormatException("lz4 match");
                long from = dstStart + produced - offset, to = dstStart + produced;
                for (long i = 0; i < match; i++) dst[to + i] = dst[from + i];   // перекрытие допустимо: копирование по байту
                produced += match;
            }
            if (produced != expected) throw new FormatException("lz4 size");
        }
    }

    // Один ресурс override-пакета: id, 56 байт метаданных, чанки (сжатые, с координатами), декодированная нагрузка и её SHA-256.
    class OverrideResource
    {
        public ulong Id;
        public byte[] Meta;
        public List<byte[]> ChunkData = new List<byte[]>();
        public List<int[]> ChunkInfo = new List<int[]>();   // {main, sub, uncompressed}
        public byte[] Payload;
        public string Sha;
    }

    // Пара <N>.db (метаданные + заголовок QTSF_PACKAGE) и <N>_0.db (LZ4-чанки) override-пакета.
    // Любая нестандартность (лишние/пропавшие записи, не 56 байт метаданных, дубли чанков, не lz4) - FormatException: пакет пропускается, установка не падает.
    static class OverridePackage
    {
        public const ulong HeaderId = 0xe12bade797f29f55UL;

        public static List<OverrideResource> Read(string metaPath, string dataPath, out byte[] header)
        {
            var meta = Catalog.Parse(File.ReadAllBytes(metaPath));
            var data = Catalog.ParseChunks(File.ReadAllBytes(dataPath));
            if (meta.Invalid != 0 || data.Invalid != 0) throw new FormatException("invalid records");
            if (!meta.Records.ContainsKey(HeaderId)) throw new FormatException("no header");
            var headers = meta.RawValues(HeaderId);
            if (headers.Count != 1 || headers[0].Length != HeaderBytes) throw new FormatException("header");
            header = headers[0];
            if (meta.Records.Count != data.Chunks.Count + 1) throw new FormatException("record sets differ");
            var result = new List<OverrideResource>();
            foreach (ulong id in data.Chunks.Keys.OrderBy(k => k))
            {
                if (!meta.Records.ContainsKey(id)) throw new FormatException("no metadata");
                var values = meta.RawValues(id);
                if (values.Count != 1 || values[0].Length != 56) throw new FormatException("metadata");
                var resource = new OverrideResource { Id = id, Meta = values[0] };
                var seen = new HashSet<long>();
                foreach (var chunk in data.Chunks[id])
                {
                    if (!seen.Add(((long)chunk.Main << 32) | (uint)chunk.Sub)) throw new FormatException("duplicate chunk");
                    resource.ChunkData.Add(data.ChunkBytes(chunk));
                    resource.ChunkInfo.Add(new[] { chunk.Main, chunk.Sub, chunk.Uncompressed });
                }
                resource.Payload = data.DecodeEntry(id);
                using (var hash = new SHA256CryptoServiceProvider()) resource.Sha = Util.Hex(hash.ComputeHash(resource.Payload));
                result.Add(resource);
            }
            return result;
        }

        const int HeaderBytes = 120;
    }

    // QtskDB.dll игры (x64, cdecl) для пересадки записей: тот же SDK, которым игра пишет override, и тот же порядок вызовов, что в
    // scripts/game_layer_native_worker.py (фаза insert). Библиотека грузится только из защищённой копии (SHA копии равен SHA источника); список известных SHA в install.json нужен лишь для журнала.
    static class Sdk
    {
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        delegate ulong OpenBaseFn(byte[] path, IntPtr config, [MarshalAs(UnmanagedType.U1)] bool flag, ref uint error);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        delegate IntPtr OpenTableFn(ref ulong baseHandle, byte[] name, uint[] config, IntPtr extra, ref uint error);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        [return: MarshalAs(UnmanagedType.U1)]
        delegate bool PutFn(ref IntPtr table, byte[] key, int keyLength, byte[] value, int valueLength, ref uint error);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        [return: MarshalAs(UnmanagedType.U1)]
        delegate bool CloseTableFn(ref IntPtr table, ref uint error);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        delegate void CloseBaseFn(ref ulong baseHandle);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern IntPtr LoadLibraryEx(string file, IntPtr reserved, uint flags);
        [DllImport("kernel32.dll", CharSet = CharSet.Ansi, ExactSpelling = true)]
        static extern IntPtr GetProcAddress(IntPtr module, string name);
        [DllImport("kernel32.dll")]
        static extern uint SetErrorMode(uint mode);

        static OpenBaseFn openBase;
        static OpenTableFn openTable, createTable;
        static PutFn put;
        static CloseTableFn closeTable;
        static CloseBaseFn closeBase;

        static T Bind<T>(IntPtr module, string name)
        {
            IntPtr address = GetProcAddress(module, name);
            if (address == IntPtr.Zero) throw new StopException("sdk", "В библиотеке игры QtskDB.dll нет функции " + name + ". Ничего не изменено.");
            return (T)(object)Marshal.GetDelegateForFunctionPointer(address, typeof(T));
        }

        public static void Load(string dll)
        {
            if (openBase != null) return;
            if (!Environment.Is64BitProcess) throw new StopException("sdk", "Помощник запущен как 32-разрядный процесс, а QtskDB.dll игры 64-разрядная. Ничего не изменено.");
            SetErrorMode(0x0001 | 0x0002 | 0x8000);
            // LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32: зависимости только из защищённой папки копии и System32.
            IntPtr module = LoadLibraryEx(dll, IntPtr.Zero, 0x100 | 0x800);
            if (module == IntPtr.Zero)
            {
                int win = Marshal.GetLastWin32Error();
                if (win == 126 || win == 127)
                    throw new StopException("sdk", "Не удалось загрузить QtskDB.dll игры: не найдены её зависимости (например MSVCP140.dll, VCRUNTIME140.dll из Microsoft Visual C++ Redistributable x64; код Windows " + win + "). Установите его и повторите. Ничего не изменено.");
                throw new StopException("sdk", "Не удалось загрузить библиотеку игры QtskDB.dll (код Windows " + win + "). Ничего не изменено.");
            }
            openTable = Bind<OpenTableFn>(module, "DB_OpenTable");
            createTable = Bind<OpenTableFn>(module, "DB_CreateTable");
            put = Bind<PutFn>(module, "DB_PutItem");
            closeTable = Bind<CloseTableFn>(module, "DB_CloseTable");
            closeBase = Bind<CloseBaseFn>(module, "DB_CloseBase");
            openBase = Bind<OpenBaseFn>(module, "DB_OpenBase");
        }

        static byte[] Utf8Z(string text)
        {
            byte[] raw = Encoding.UTF8.GetBytes(text);
            var result = new byte[raw.Length + 1];
            Buffer.BlockCopy(raw, 0, result, 0, raw.Length);
            return result;
        }

        // Скелет <package>.db уже лежит в directory; метаданные идут в таблицу <package>, чанки — в новую <package>_0.
        public static void Insert(string directory, string package, List<OverrideResource> resources)
        {
            List<KeyValuePair<byte[], byte[]>> meta, chunks;
            BuildRows(resources, out meta, out chunks);
            InsertRows(directory, package, meta, chunks);
        }

        // Строки для таблиц <package> (метаданные) и <package>_0 (чанки).
        static void BuildRows(List<OverrideResource> resources, out List<KeyValuePair<byte[], byte[]>> meta, out List<KeyValuePair<byte[], byte[]>> chunks)
        {
            meta = new List<KeyValuePair<byte[], byte[]>>();
            chunks = new List<KeyValuePair<byte[], byte[]>>();
            foreach (var r in resources.OrderBy(x => x.Id))
            {
                byte[] id = BitConverter.GetBytes(r.Id);
                meta.Add(new KeyValuePair<byte[], byte[]>(id, r.Meta));
                for (int i = 0; i < r.ChunkData.Count; i++)
                {
                    int[] info = r.ChunkInfo[i];
                    var key = new byte[16];
                    Buffer.BlockCopy(id, 0, key, 0, 8);
                    Buffer.BlockCopy(BitConverter.GetBytes(info[0]), 0, key, 8, 4);
                    Buffer.BlockCopy(BitConverter.GetBytes(info[1]), 0, key, 12, 4);
                    var value = new byte[4 + r.ChunkData[i].Length];
                    Buffer.BlockCopy(BitConverter.GetBytes(info[2]), 0, value, 0, 4);
                    Buffer.BlockCopy(r.ChunkData[i], 0, value, 4, r.ChunkData[i].Length);
                    chunks.Add(new KeyValuePair<byte[], byte[]>(key, value));
                }
            }
        }

        // В каталоге directory лежит <package>.db (скелет). Метаданные идут в таблицу <package> (она есть), чанки — в новую <package>_0.
        static void InsertRows(string directory, string package, List<KeyValuePair<byte[], byte[]>> meta, List<KeyValuePair<byte[], byte[]>> chunks)
        {
            uint error = 0;
            ulong baseHandle = openBase(Utf8Z(directory.Replace('\\', '/')), IntPtr.Zero, false, ref error);
            if (baseHandle == 0 || error != 0) throw new IOException("DB_OpenBase: ошибка " + error);
            try
            {
                Fill(ref baseHandle, package, true, meta);
                Fill(ref baseHandle, package + "_0", false, chunks);
            }
            finally { closeBase(ref baseHandle); }
        }

        static void Fill(ref ulong baseHandle, string name, bool exists, List<KeyValuePair<byte[], byte[]>> rows)
        {
            uint error = 0;
            IntPtr table = exists
                ? openTable(ref baseHandle, Utf8Z(name), new uint[] { 1, 0 }, IntPtr.Zero, ref error)          // чтение и запись, кэш по умолчанию
                : createTable(ref baseHandle, Utf8Z(name), new uint[] { 32, 0, 0 }, IntPtr.Zero, ref error);   // predict, cache, forceCreate=false
            if (table == IntPtr.Zero || error != 0) throw new IOException("DB_" + (exists ? "OpenTable " : "CreateTable ") + name + ": ошибка " + error);
            bool failed = true;
            try
            {
                foreach (var row in rows)
                {
                    error = 0;
                    if (!put(ref table, row.Key, row.Key.Length, row.Value, row.Value.Length, ref error) || error != 0)
                        throw new IOException("DB_PutItem " + name + ": ошибка " + error);
                }
                failed = false;
            }
            finally
            {
                error = 0;
                bool closed = closeTable(ref table, ref error);
                if (!failed && (!closed || error != 0)) throw new IOException("DB_CloseTable " + name + ": ошибка " + error);
            }
        }
    }

    class FileRow { public string Name, Sha, PayloadName; public long Bytes; }
    // Ресурс пакета в install.json (поля добавлены для пересадки записей между версиями игры; в старых install.json их нет).
    class ResRow { public string IdHex, Kind, OriginalSha, Sha; public long Bytes, Chunks; }
    class PrimeRow { public string Relative, Sha; public long Bytes; }

    class Pkg
    {
        public string Id, PrimePkgHash, HeaderHex, InfoSha, InfoVersion;
        public List<FileRow> Files = new List<FileRow>();
        public List<ResRow> Resources = new List<ResRow>();
        public FileRow File(string name) { return Files.First(f => f.Name == name); }
    }

    class Manifest
    {
        public string Product, Version, Built, GameVersion, IndexSha;
        public List<string> Candidates = new List<string>();
        public Dictionary<string, string> Sdk = new Dictionary<string, string>();
        // Допустимые SHA QtskDB.dll для пути пересадки (пусто в install.json старого формата: пересадка тогда отказывает).
        public HashSet<string> SdkKnown = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        public List<Pkg> Pkgs = new List<Pkg>();
        public List<PrimeRow> Prime = new List<PrimeRow>();
        // «packages/N/файл» -> SHA, которые считаются нашими прежними версиями.
        public Dictionary<string, HashSet<string>> Known = new Dictionary<string, HashSet<string>>();

        public static Manifest Load(string payload)
        {
            string path = Path.Combine(payload, "install.json");
            if (!File.Exists(path)) throw new StopException("payload", "В установщике нет данных перевода (install.json). Скачайте установщик заново.");
            var root = Util.ReadJson(path);
            var m = new Manifest();
            m.Product = Util.Str(root, "product");
            m.Version = Util.Str(root, "version");
            m.Built = Util.Str(root, "built");
            m.GameVersion = Util.Str(root, "game_version");
            m.IndexSha = Util.Str(root, "index_sha256");
            foreach (object c in Util.Items(root["candidates"])) m.Candidates.Add(Convert.ToString(c));
            foreach (var pair in (Dictionary<string, object>)root["sdk_hashes"]) m.Sdk[pair.Key] = Convert.ToString(pair.Value);
            object sdkKnown;
            if (root.TryGetValue("sdk_known", out sdkKnown))
                foreach (object o in Util.Items(sdkKnown)) m.SdkKnown.Add(Convert.ToString(o));
            foreach (object o in Util.Items(root["prime"]))
            {
                var row = (Dictionary<string, object>)o;
                m.Prime.Add(new PrimeRow { Relative = Util.Str(row, "relative"), Sha = Util.Str(row, "sha256"), Bytes = Util.Num(row, "bytes") });
            }
            foreach (object o in Util.Items(root["packages"]))
            {
                var row = (Dictionary<string, object>)o;
                var p = new Pkg { Id = Util.Str(row, "id"), PrimePkgHash = Util.Str(row, "prime_pkg_hash"), HeaderHex = Util.Str(row, "header_hex"), InfoSha = Util.Str(row, "info_sha256"), InfoVersion = Util.Str(row, "info_version") };
                foreach (object f in Util.Items(row["files"]))
                {
                    var file = (Dictionary<string, object>)f;
                    p.Files.Add(new FileRow { Name = Util.Str(file, "name"), Sha = Util.Str(file, "sha256"), Bytes = Util.Num(file, "bytes"), PayloadName = Util.Str(file, "payload") });
                }
                object resources;
                if (row.TryGetValue("resources", out resources))
                    foreach (object r in Util.Items(resources))
                    {
                        var res = (Dictionary<string, object>)r;
                        p.Resources.Add(new ResRow { IdHex = Util.Str(res, "id_hex"), Kind = Util.Str(res, "kind"), OriginalSha = Util.Str(res, "original_sha256"), Sha = Util.Str(res, "sha256"), Bytes = Util.Num(res, "bytes"), Chunks = Util.Num(res, "chunks") });
                    }
                m.Pkgs.Add(p);
            }
            foreach (var pair in (Dictionary<string, object>)root["known"])
                m.Known[pair.Key] = new HashSet<string>(Util.Items(pair.Value).Select(x => Convert.ToString(x)), StringComparer.OrdinalIgnoreCase);
            // Кандидаты этого установщика тоже «наши».
            foreach (var p in m.Pkgs)
                foreach (var f in p.Files)
                {
                    string key = "packages/" + p.Id + "/" + f.Name;
                    HashSet<string> set;
                    if (!m.Known.TryGetValue(key, out set)) { set = new HashSet<string>(StringComparer.OrdinalIgnoreCase); m.Known[key] = set; }
                    set.Add(f.Sha);
                }
            if (m.Pkgs.Count == 0 || m.Prime.Count == 0) throw new StopException("payload", "Данные перевода в установщике неполные. Скачайте установщик заново.");
            return m;
        }

        public bool IsKnown(string relative, string sha)
        {
            HashSet<string> set;
            return Known.TryGetValue(relative, out set) && set.Contains(sha);
        }
    }

    class Op
    {
        public Pkg Package;
        public FileRow Row;
        public string Relative, Target, Source, BeforeSha;
        public bool BeforeExists;
        public byte[] SourceBytes;
        public string Candidate { get { return Row.Sha; } }
        public bool Remove { get { return Row.Sha == null; } }
        public bool Unchanged { get { return Remove ? !BeforeExists : BeforeExists && BeforeSha == Row.Sha; } }
    }

    // Нужное место по томам (запас 10 %). Не хватает - StopException("disk_space"): всё проверяется до первой записи, в игру ничего не записано.
    class SpaceNeeds
    {
        readonly Dictionary<string, long> bytes = new Dictionary<string, long>(StringComparer.OrdinalIgnoreCase);
        readonly Dictionary<string, string> sample = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        public void Add(string path, long size)
        {
            string root = Path.GetPathRoot(Path.GetFullPath(path)) ?? path;
            long old;
            bytes.TryGetValue(root, out old);
            bytes[root] = old + Math.Max(0, size);
            sample[root] = path;
        }
        public void Require()
        {
            foreach (var pair in bytes)
            {
                long need = pair.Value + pair.Value / 10;   // запас 10 %
                long free = Util.FreeBytes(sample[pair.Key]);
                if (free < 0 || need <= free) continue;
                throw new StopException("disk_space", "Не хватает места на диске " + pair.Key + ": нужно около " + Megabytes(need) + " МБ, свободно " + Megabytes(free) + " МБ. Освободите место и повторите. В игру ничего не записано.");
            }
        }
        static string Megabytes(long n) { return ((n + 1048575) / 1048576).ToString(CultureInfo.InvariantCulture); }
    }

    // Закрытие игры и лаунчера (0.6.1). Работает только в режиме `--close all` (установка, удаление и откат в окне; явный тихий запуск без /UPDATE):
    // пользователь сам запустил установщик, значит согласился закрыть игру и лаунчер. Фоновое обновление ничего не закрывает.
    //
    // Что считается целью (все условия сразу):
    //  * корень игры (сама папка игры) и корень лаунчера (только папка WXQLauncher* с exe лаунчера внутри или на уровень ниже);
    //    корень диска, %windir%, Program Files, ProgramData, C:\Users и папка профиля корнем не бывают никогда;
    //  * образ внутри корня - цель при любом имени (кроме помощника и каталога данных); образ вне корней или с недоступным путём - только точное имя из закрытого списка;
    //  * никогда: PID <= 4, образ под %windir%, csrss/smss/wininit/winlogon/services/lsass/svchost/explorer/dwm, службы (родитель services.exe), сам помощник и его родители;
    //  * процесс другого сеанса Windows (другой пользователь) не закрывается: отказ running;
    //  * потомок цели закрывается, только если он сам цель по этим правилам (папки игры и лаунчера или имя из списка); остальные потомки (браузер, открытый лаунчером) остаются.
    // Завершение идёт по дескриптору, открытому при осмотре (PID не может «переехать» на чужой процесс), с проверкой времени старта.
    static class Closer
    {
        static readonly HashSet<string> ExactNames = new HashSet<string>(new[] { "WXQ", "WXQLauncher", "WXQLauncherRU", "HonorofKingsACE", "王者万象棋" }, StringComparer.OrdinalIgnoreCase);
        static readonly HashSet<string> NeverNames = new HashSet<string>(new[] { "system", "idle", "registry", "memcompression", "csrss", "smss", "wininit", "winlogon", "services", "lsass", "svchost", "explorer", "dwm" }, StringComparer.OrdinalIgnoreCase);
        static readonly Regex LauncherFolder = new Regex("^WXQLauncher", RegexOptions.IgnoreCase);
        static readonly string[] SystemFolders = { "windows", "users", "programdata", "program files", "program files (x86)" };
        public static bool KnownName(string name) { return ExactNames.Contains(name); }

        const uint AccessTerminate = 0x1, AccessSynchronize = 0x100000, AccessQueryLimited = 0x1000;

        [DllImport("kernel32.dll", SetLastError = true)]
        static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder name, ref uint size);
        [DllImport("kernel32.dll")]
        static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll")]
        static extern bool ProcessIdToSessionId(uint pid, out uint session);
        [DllImport("kernel32.dll")]
        static extern bool GetProcessTimes(IntPtr process, out long created, out long exited, out long kernel, out long user);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool TerminateProcess(IntPtr process, uint code);
        [DllImport("kernel32.dll")]
        static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint pid);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, EntryPoint = "Process32FirstW")]
        static extern bool Process32First(IntPtr snapshot, ref ProcessEntry entry);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, EntryPoint = "Process32NextW")]
        static extern bool Process32Next(IntPtr snapshot, ref ProcessEntry entry);

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        struct ProcessEntry
        {
            public uint Size, Usage, Pid;
            public IntPtr HeapId;
            public uint ModuleId, Threads, ParentPid;
            public int PriorityBase;
            public uint Flags;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string ExeFile;
        }

        public class Target : IDisposable
        {
            public int Pid, ParentPid;
            public string Name, Path, Why, How = "";
            public uint Session;
            public bool OtherSession, HasWindow, IsChild;
            public long Start, ParentStart;
            public IntPtr Handle;
            public Process Proc;
            public string Title { get { return Name + ".exe (PID " + Pid + ")"; } }
            public void Dispose()
            {
                if (Handle != IntPtr.Zero) { CloseHandle(Handle); Handle = IntPtr.Zero; }
                if (Proc != null) { Proc.Dispose(); Proc = null; }
            }
        }

        static string ImagePathOf(IntPtr handle)
        {
            var text = new StringBuilder(1024);
            uint size = (uint)text.Capacity;
            return QueryFullProcessImageName(handle, 0, text, ref size) ? text.ToString(0, (int)size) : null;
        }

        static string ImagePath(int pid)
        {
            IntPtr handle = OpenProcess(AccessQueryLimited, false, (uint)pid);
            if (handle == IntPtr.Zero) return null;
            try { return ImagePathOf(handle); }
            finally { CloseHandle(handle); }
        }

        // pid -> родитель по снимку процессов.
        static Dictionary<int, int> ParentMap()
        {
            var map = new Dictionary<int, int>();
            IntPtr snapshot = CreateToolhelp32Snapshot(2, 0);   // TH32CS_SNAPPROCESS
            if (snapshot == IntPtr.Zero || snapshot == new IntPtr(-1)) return map;
            try
            {
                var entry = new ProcessEntry();
                entry.Size = (uint)Marshal.SizeOf(typeof(ProcessEntry));
                for (bool more = Process32First(snapshot, ref entry); more; more = Process32Next(snapshot, ref entry))
                    map[(int)entry.Pid] = (int)entry.ParentPid;
            }
            finally { CloseHandle(snapshot); }
            return map;
        }

        // Свой процесс и родители (установщик, оболочка): их не закрываем ни при каких условиях.
        static HashSet<int> Protected(Dictionary<int, int> parents)
        {
            var set = new HashSet<int>();
            int pid = Process.GetCurrentProcess().Id;
            for (int i = 0; i < 12 && pid > 0 && set.Add(pid); i++)
            {
                int parent;
                if (!parents.TryGetValue(pid, out parent)) break;
                pid = parent;
            }
            return set;
        }

        static string WinDir()
        {
            string dir = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
            return string.IsNullOrEmpty(dir) ? null : Util.NormPath(dir) + "\\";
        }

        // Папка, которую нельзя считать корнем цели: корень диска, системные и общие папки, профиль.
        static bool Forbidden(string dir)
        {
            try
            {
                string d = Util.NormPath(dir).TrimEnd('\\');
                string drive = Path.GetPathRoot(d);
                if (d.Length < 3 || (drive != null && string.Equals(drive.TrimEnd('\\'), d, StringComparison.OrdinalIgnoreCase))) return true;
                string windir = WinDir();
                if (windir != null && (d + "\\").StartsWith(windir, StringComparison.OrdinalIgnoreCase)) return true;
                var bad = new List<string>
                {
                    Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),
                    Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                    Environment.GetEnvironmentVariable("ProgramW6432")
                };
                foreach (string b in bad)
                    if (!string.IsNullOrEmpty(b) && string.Equals(Util.NormPath(b).TrimEnd('\\'), d, StringComparison.OrdinalIgnoreCase)) return true;
                // Те же папки на другом диске: D:\Windows, D:\Program Files, D:\Users, D:\ProgramData.
                string parent = Path.GetDirectoryName(d);
                if (parent != null && Path.GetPathRoot(d) != null && string.Equals(parent.TrimEnd('\\'), Path.GetPathRoot(d).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase)
                    && SystemFolders.Contains(Path.GetFileName(d).ToLowerInvariant())) return true;
                return false;
            }
            catch (Exception) { return true; }
        }

        // Корень лаунчера: папка WXQLauncher* (регистр не важен) с exe лаунчера из закрытого списка внутри или на уровень ниже
        // (версионная папка, например 26101.0.1.30004\王者万象棋.exe). Иначе null: родитель игры (C:\WXQ, D:\Games, диск) корнем не считается.
        public static string LauncherRoot(string dir)
        {
            try
            {
                if (string.IsNullOrEmpty(dir)) return null;
                string d = Util.NormPath(dir).TrimEnd('\\');
                if (Forbidden(d) || !Directory.Exists(d) || !LauncherFolder.IsMatch(Path.GetFileName(d))) return null;
                foreach (string name in ExactNames)
                {
                    if (name == "WXQLauncherRU") continue;   // наш активатор лежит в {app}\launcher и признаком лаунчера не служит
                    if (File.Exists(Path.Combine(d, name + ".exe"))) return d + "\\";
                    foreach (string sub in Directory.GetDirectories(d))
                        if (File.Exists(Path.Combine(sub, name + ".exe"))) return d + "\\";
                }
                return null;
            }
            catch (Exception) { return null; }
        }

        // Корни цели: корень лаунчера (если он признан) и сама папка игры (если она не запрещённая); пусто - только по именам.
        public static List<string> Roots(Context ctx)
        {
            var roots = new List<string>();
            string launcher = LauncherRoot(ctx.Launcher);
            if (launcher != null) roots.Add(launcher);
            try
            {
                if (!string.IsNullOrEmpty(ctx.Game) && !Forbidden(ctx.Game)) roots.Add(Util.NormPath(ctx.Game).TrimEnd('\\') + "\\");
            }
            catch (Exception) { }
            return roots;
        }

        static bool Under(string path, IEnumerable<string> roots)
        {
            foreach (string root in roots) if (path.StartsWith(root, StringComparison.OrdinalIgnoreCase)) return true;
            return false;
        }

        // Жёсткий запрет: этот процесс не закрывается ни при каких правилах.
        static bool Denied(int pid, string name, string path, Dictionary<int, int> parents, string windir)
        {
            if (pid <= 4 || NeverNames.Contains(name)) return true;
            if (path != null && windir != null && path.StartsWith(windir, StringComparison.OrdinalIgnoreCase)) return true;
            int parent;
            if (parents.TryGetValue(pid, out parent) && parent > 4)
            {
                try { using (var p = Process.GetProcessById(parent)) if (string.Equals(p.ProcessName, "services", StringComparison.OrdinalIgnoreCase)) return true; }
                catch (Exception) { }
            }
            return false;
        }

        // Правило выбора. Образ в корне - цель при любом имени (кроме помощника и каталога данных). Вне корней или без пути - только точное имя из списка.
        static bool Eligible(string name, string path, List<string> roots, List<string> own, string testRoot, out string why)
        {
            why = null;
            if (!string.IsNullOrEmpty(testRoot) && (path == null || !path.StartsWith(testRoot, StringComparison.OrdinalIgnoreCase))) return false;
            bool exact = ExactNames.Contains(name);
            if (path != null && Under(path, roots))
            {
                if (!exact && Under(path, own)) return false;   // помощник и значок русификатора не закрываем
                why = exact ? "папка игры или лаунчера и имя из списка" : "папка игры или лаунчера";
                return true;
            }
            if (exact) { why = "имя из списка" + (path == null ? " (путь недоступен)" : " (образ вне папок игры и лаунчера)"); return true; }
            return false;
        }

        // Открывает цель с правом завершения, ожидания и запроса данных: дальше все действия идут через этот дескриптор.
        static Target Open(int pid, string name, string path, string why, uint mySession)
        {
            IntPtr handle = OpenProcess(AccessTerminate | AccessSynchronize | AccessQueryLimited, false, (uint)pid);
            long start = 0;
            if (handle != IntPtr.Zero)
            {
                long created, exited, kernel, user;
                if (GetProcessTimes(handle, out created, out exited, out kernel, out user)) start = created;
                string again = Util.LongPath(ImagePathOf(handle));
                if ((again != null && path != null && !string.Equals(again, path, StringComparison.OrdinalIgnoreCase)) || WaitForSingleObject(handle, 0) == 0)
                {
                    CloseHandle(handle);   // PID занят другим процессом либо процесс уже завершился
                    return null;
                }
            }
            var t = new Target { Pid = pid, Name = name, Path = path, Why = why, Handle = handle, Start = start };
            uint session;
            t.OtherSession = !ProcessIdToSessionId((uint)pid, out session) || session != mySession;
            t.Session = session;
            try
            {
                t.Proc = Process.GetProcessById(pid);
                t.HasWindow = !t.OtherSession && t.Proc.MainWindowHandle != IntPtr.Zero;
            }
            catch (Exception) { }   // окно не прочитано: считаем, что окна нет, закрытие пойдёт сразу принудительно
            return t;
        }

        public static List<Target> Scan(Context ctx, bool log)
        {
            var result = new List<Target>();
            var roots = Roots(ctx);
            var parents = ParentMap();
            var keep = Protected(parents);
            string windir = WinDir();
            var own = new List<string>();
            try { own.Add(Util.NormPath(Path.GetDirectoryName(Process.GetCurrentProcess().MainModule.FileName)).TrimEnd('\\') + "\\"); } catch (Exception) { }
            if (!string.IsNullOrEmpty(ctx.Data)) own.Add(Util.NormPath(ctx.Data).TrimEnd('\\') + "\\");
            string testRoot = null;
#if TESTBUILD
            testRoot = Environment.GetEnvironmentVariable("WXQ_RU_TEST_CLOSE_ROOT");   // тесты закрывают только свои заглушки, настоящая игра на этом ПК не затрагивается
            if (!string.IsNullOrEmpty(testRoot)) testRoot = Util.NormPath(testRoot).TrimEnd('\\') + "\\";
#endif
            uint mySession = 0;
            ProcessIdToSessionId((uint)Process.GetCurrentProcess().Id, out mySession);
            var seen = new HashSet<int>();
            foreach (var process in Process.GetProcesses())
            {
                try
                {
                    int pid = process.Id;
                    if (pid <= 4 || keep.Contains(pid)) continue;
                    string name = process.ProcessName, path = Util.LongPath(ImagePath(pid)), why;
                    if (Denied(pid, name, path, parents, windir) || !Eligible(name, path, roots, own, testRoot, out why)) continue;
                    var t = Open(pid, name, path, why, mySession);
                    if (t == null) continue;
                    result.Add(t); seen.Add(pid);
                }
                catch (Exception) { }
                finally { process.Dispose(); }
            }
            // Потомки целей: только те, что сами подходят под правила; остальные остаются и попадают в журнал.
            var queue = new Queue<Target>(result);
            while (queue.Count > 0)
            {
                var parent = queue.Dequeue();
                foreach (var pair in parents)
                {
                    if (pair.Value != parent.Pid) continue;
                    int pid = pair.Key;
                    if (pid <= 4 || seen.Contains(pid) || keep.Contains(pid)) continue;
                    string name, path, why;
                    try { using (var p = Process.GetProcessById(pid)) name = p.ProcessName; }
                    catch (Exception) { continue; }
                    path = Util.LongPath(ImagePath(pid));
                    if (Denied(pid, name, path, parents, windir)) continue;
                    if (!Eligible(name, path, roots, own, testRoot, out why))
                    {
                        if (log) Log.Write("  оставлен дочерний процесс " + name + ".exe (PID " + pid + ", путь " + (path ?? "недоступен") + "): он не из папок игры и лаунчера, и его имени нет в списке");
                        continue;
                    }
                    var child = Open(pid, name, path, why, mySession);
                    if (child == null) continue;
                    if (parent.Start != 0 && child.Start != 0 && child.Start < parent.Start) { child.Dispose(); continue; }   // PID родителя переиспользован: это не потомок
                    child.IsChild = true; child.ParentPid = parent.Pid; child.ParentStart = parent.Start;
                    child.Why = "дочерний процесс " + parent.Name + ".exe: " + why;
                    seen.Add(pid); result.Add(child); queue.Enqueue(child);
                }
            }
            return result;
        }

        // Имена запущенных процессов игры и лаунчера по тем же правилам (для проверки без закрытия).
        public static List<string> ActiveNames(Context ctx)
        {
            var found = Scan(ctx, false);
            try { return found.Select(t => t.Name).Distinct().ToList(); }
            finally { foreach (var t in found) t.Dispose(); }
        }

        static string Join(IEnumerable<Target> targets)
        {
            return string.Join(", ", targets.Select(t => t.Title).ToArray());
        }

        static bool Gone(Target t)
        {
            if (t.Handle != IntPtr.Zero) return WaitForSingleObject(t.Handle, 0) == 0;
            try { t.Proc.Refresh(); return t.Proc.HasExited; }
            catch (InvalidOperationException) { return true; }
            catch (System.ComponentModel.Win32Exception) { return false; }   // нет доступа: не считаем процесс завершившимся
            catch (NullReferenceException) { return false; }
        }

        static void WaitGone(List<Target> targets, int milliseconds)
        {
            var clock = Stopwatch.StartNew();
            while (clock.ElapsedMilliseconds < milliseconds && targets.Any(t => !Gone(t))) Thread.Sleep(100);
        }

        // Завершение через дескриптор. Потомка перед этим проверяем ещё раз: родитель и время старта те же, иначе PID мог достаться другому процессу.
        static void Terminate(Target t, HashSet<int> keep)
        {
            if (keep.Contains(t.Pid) || Gone(t)) return;
            if (t.IsChild)
            {
                int parent;
                bool same = ParentMap().TryGetValue(t.Pid, out parent) && parent == t.ParentPid && (t.ParentStart == 0 || t.Start == 0 || t.Start >= t.ParentStart);
                if (!same) { Log.Write("  " + t.Title + ": родитель или время старта изменились, не трогаю"); return; }
            }
#if TESTBUILD
            if (Environment.GetEnvironmentVariable("WXQ_RU_TEST_NOKILL") == "1") { Log.Write("  тест: принудительное завершение отключено, PID " + t.Pid + " не тронут"); return; }
#endif
            if (t.Handle == IntPtr.Zero) { Log.Write("  " + t.Title + ": нет прав открыть процесс для завершения"); return; }
            if (!TerminateProcess(t.Handle, 1)) Log.Write("  не удалось завершить " + t.Title + ": ошибка " + Marshal.GetLastWin32Error());
        }

        public static void Run(Context ctx, string mode)
        {
            if (mode != "all") return;
            try
            {
                for (int attempt = 1; attempt <= 3; attempt++)
                {
                    var found = Scan(ctx, attempt == 1);
                    try
                    {
                        if (found.Count == 0) return;
                        var foreign = found.Where(t => t.OtherSession).ToList();
                        if (foreign.Count > 0)
                        {
                            Log.Write("запущено у другого пользователя Windows или в другом сеансе: " + Join(foreign));
                            throw new StopException("running", "Игра или лаунчер запущены у другого пользователя Windows (" + Join(foreign) + "). Закрыть их отсюда нельзя: попросите этого пользователя закрыть игру или выйти из Windows и повторите.");
                        }
                        Progress.Phase("closing", string.Join(", ", found.Select(t => t.Name + ".exe").Distinct().ToArray()));
                        Log.Write("закрываю игру и лаунчер, проход " + attempt + ":");
                        foreach (var t in found)
                            Log.Write("  найден " + t.Name + ".exe, PID " + t.Pid + ", путь " + (t.Path ?? "недоступен") + ", окно " + (t.HasWindow ? "есть" : "нет") + ", причина: " + t.Why);
                        // 1. Корректное закрытие окон, ждём до 5 секунд.
                        var windowed = found.Where(x => x.HasWindow && x.Proc != null).ToList();
                        foreach (var t in windowed)
                        {
#if TESTBUILD
                            if (Environment.GetEnvironmentVariable("WXQ_RU_TEST_NOKILL") == "1") continue;
#endif
                            if (!Gone(t)) { try { t.Proc.CloseMainWindow(); } catch (Exception) { } }
                        }
                        WaitGone(windowed, 5000);
                        foreach (var t in windowed.Where(x => Gone(x))) t.How = "закрыт через окно (CloseMainWindow)";
                        // 2. Остальные - принудительно, сначала потомки (самые глубокие), потом родители; ждём до 10 секунд.
                        var keep = Protected(ParentMap());
                        var rest = found.Where(x => !Gone(x)).ToList();
                        foreach (var t in rest.Where(x => x.IsChild).Reverse()) Terminate(t, keep);
                        foreach (var t in rest.Where(x => !x.IsChild)) Terminate(t, keep);
                        WaitGone(rest, 10000);
                        foreach (var t in rest.Where(x => Gone(x))) t.How = "завершён принудительно" + (t.IsChild ? " как дочерний процесс" : "");
                        foreach (var t in found)
                            Log.Write("  " + t.Name + ".exe, PID " + t.Pid + ": " + (Gone(t) ? t.How : "не закрылся"));
                    }
                    finally { foreach (var t in found) t.Dispose(); }
                    Thread.Sleep(1200);   // лаунчер мог перезапустить свой процесс: следующий проход проверит заново
                }
                var left = Scan(ctx, false);
                try
                {
                    if (left.Count > 0)
                    {
                        Log.Write("не закрылись: " + Join(left));
                        throw new StopException("running", "Игра или лаунчер сейчас запущены, и закрыть их не удалось (" + Join(left) + "). Закройте их вручную: диспетчер задач, вкладка «Подробности», «Снять задачу», в том числе значок лаунчера в области уведомлений, и повторите.");
                    }
                }
                finally { foreach (var t in left) t.Dispose(); }
            }
            finally { Progress.Phase(null, ""); }
        }
    }

    class Context
    {
        public string Payload, Data, Game, Cache, Launcher;
        public Manifest M;
        public Dictionary<string, string> Result = new Dictionary<string, string>();
        public string BackupRoot { get { return Path.Combine(Data, "backup"); } }
        public string StateFile { get { return Path.Combine(Data, "state.json"); } }
        public string MemoFile { get { return Path.Combine(Data, "work", "prime-cache.json"); } }
    }

    static partial class Program
    {
        const ulong HeaderId = 0xe12bade797f29f55UL;
        const int HeaderBytes = 120;
        static readonly Regex PackageId = new Regex(@"^(0|[1-9][0-9]{0,9})$");
        static readonly Regex RelativeName = new Regex(@"^packages/(0|[1-9][0-9]{0,9})/(\1\.db|\1_[0-9]{1,10}\.db|\1\.info)$", RegexOptions.IgnoreCase);
        static readonly Regex MountLine = new Regex(@"MountPackage (\d+), prime, \d+ => succeed! .*?pkgHash:(\d+) basePkgHash:(\d+)");

        static string CloseMode = "none";

        static int Main(string[] argv)
        {
            var args = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            // --check-only — флаг без значения (команда update); остальные параметры идут парами «имя значение».
            bool checkOnly = argv.Any(a => a == "--check-only");
            if (checkOnly) argv = argv.Where(a => a != "--check-only").ToArray();
            string command = argv.Length > 0 ? argv[0] : "";
            for (int i = 1; i + 1 < argv.Length; i += 2) args[argv[i].TrimStart('-')] = argv[i + 1];
            // --close all|stale: сам закрыть мешающие процессы игры и лаунчера (см. Closer); без параметра только проверка и отказ running.
            CloseMode = args.ContainsKey("close") && (args["close"] == "all" || args["close"] == "stale") ? args["close"] : "none";
            var ctx = new Context();
            // Пути нормализуются сразу: Sdk.Load меняет текущую папку процесса, относительные пути после этого указывали бы не туда.
            ctx.Payload = args.ContainsKey("payload") && args["payload"].Length > 0 ? Util.NormPath(args["payload"]) : "";
            ctx.Data = args.ContainsKey("data") ? Util.NormPath(args["data"]) : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Egoist", "WXQ-Russian");
            string outFile = args.ContainsKey("out") && args["out"].Length > 0 ? Util.NormPath(args["out"]) : null;
            Log.File_ = null;   // журнал включается только после того, как SecureDataDir признал каталог данных доверенным
            bool dataTrusted = false;
            if (command == "check" || command == "backup" || command == "unpack" || command == "verify" || command == "restore")
                Progress.Start(args.ContainsKey("progress") ? args["progress"] : null, command);
            string status = "ok";
            int code = 0;
            Mutex mutex = null;
            try
            {
                bool readOnly = command == "find" || command == "status" || command == "task-register" || command == "task-remove";
                // parse-packages только читает два указанных файла: каталог данных, журнал и мьютекс не нужны.
                bool parseOnly = command == "parse-packages";
                // Каталог данных (резервные копии, receipt) закрывается от записи обычным пользователям до первой записи в него.
                if (parseOnly) { }
                else if (readOnly) { try { SecureDataDir(ctx); dataTrusted = true; } catch (Exception) { } }
                else { SecureDataDir(ctx); dataTrusted = true; }
                if (dataTrusted) Log.File_ = Path.Combine(ctx.Data, "install.log");
                Log.Write("команда " + command);
                // update берёт мьютекс сам и не на всё время: пока идёт скачивание, установка и удаление не должны упираться в «занято»,
                // а запущенный им Setup.exe запускает собственного помощника, которому нужен тот же Global-мьютекс.
                if (!readOnly && !parseOnly && command != "update" && command != "setup-update")
                {
                    bool created;
                    mutex = new Mutex(false, "Global\\Egoist.WXQRussian.Tool", out created);
                    bool acquired;
                    // Прежний процесс убит, не отпустив мьютекс: владение переходит нам, мьютекс считаем захваченным.
                    try { acquired = mutex.WaitOne(0); }
                    catch (AbandonedMutexException) { acquired = true; Log.Write("мьютекс остался от прерванного процесса, беру его"); }
                    if (!acquired) { mutex = null; throw new StopException("busy", "Уже выполняется другая операция русификатора. Дождитесь её окончания."); }
                }
                string gameArg = args.ContainsKey("game") && args["game"].Length > 0 ? args["game"] : null;
                switch (command)
                {
                    case "find": Find(ctx, gameArg); break;
                    case "status": Status(ctx, gameArg); break;
                    case "check": Load(ctx); Check(ctx, gameArg, false); break;
                    case "backup": Load(ctx); Backup(ctx, gameArg); break;
                    case "unpack": Load(ctx); Unpack(ctx, gameArg, args["label"]); break;
                    case "verify": Load(ctx); Verify(ctx, gameArg, args["label"]); break;
                    case "restore": Restore(ctx, gameArg); break;
                    case "purge": Purge(ctx, args.ContainsKey("keep") && args["keep"] == "1"); break;
                    case "parse-packages": ParsePackages(ctx, args); break;
                    case "update": Update(ctx, args, checkOnly); break;
                    case "setup-update": SetupUpdate(ctx, args); break;
                    case "legacy-cleanup": LegacyCleanup(ctx, args); break;
                    case "task-register": TaskRegister(ctx, args); break;
                    case "task-remove": TaskRemove(ctx, args); break;
#if TESTBUILD
                    // Только для тестов закрытия процессов: без игры, пакетов и receipt.
                    case "close-probe": ctx.Game = gameArg; ctx.Launcher = args.ContainsKey("launcher") ? args["launcher"] : (Directory.GetParent(Util.NormPath(gameArg)) == null ? null : Directory.GetParent(Util.NormPath(gameArg)).FullName); ctx.Result["roots"] = string.Join("|", Closer.Roots(ctx).ToArray()); Closer.Run(ctx, CloseMode); break;
#endif
                    default: throw new StopException("usage", "Неизвестная команда: " + command);
                }
            }
            catch (StopException stop)
            {
                status = "stop"; code = 2;
                ctx.Result["code"] = stop.Code;
                ctx.Result["message"] = stop.Message;
                Log.Write("остановка [" + stop.Code + "]: " + stop.Message);
            }
            catch (Exception error)
            {
                status = "error"; code = 3;
                ctx.Result["code"] = "unexpected";
                ctx.Result["message"] = "Непредвиденная ошибка: " + error.Message + " Подробности — в журнале install.log.";
                Log.Write("ошибка: " + error);
            }
            finally
            {
                if (mutex != null) { try { mutex.ReleaseMutex(); } catch (ApplicationException) { } }
            }
            ctx.Result["status"] = status;
            if ((command == "task-register" || command == "task-remove") && status != "ok")
                Console.WriteLine(command + ": " + (ctx.Result.ContainsKey("message") ? ctx.Result["message"] : status));
            // После отказа SecureDataDir (acl, ссылка) в каталог данных не пишем ничего, в том числе --out, если он указан внутри него.
            if (outFile != null && !dataTrusted && IsUnder(outFile, ctx.Data)) outFile = null;
            if (outFile != null)
            {
                var lines = ctx.Result.Select(p => p.Key + "=" + (p.Value ?? "").Replace("\r", " ").Replace("\n", " ")).ToArray();
                try { File.WriteAllLines(outFile, lines, new UTF8Encoding(true)); } catch (IOException) { code = 3; }
            }
            Progress.Finish();   // последним, после --out: увидев state=done, окно уже может читать результат
            Log.Write("команда " + command + " -> " + status);
            return code;
        }

        static bool IsUnder(string file, string dir)
        {
            try
            {
                string prefix = Path.GetFullPath(dir).TrimEnd('\\') + "\\";
                return Path.GetFullPath(file).StartsWith(prefix, StringComparison.OrdinalIgnoreCase);
            }
            catch (Exception) { return true; }
        }

        // Чистка каталога данных при удалении программы: state.json, work и (если не --keep 1) резервные копии. Каталог уже проверен SecureDataDir,
        // мьютекс взят общий с установкой и откатом: копии не удаляются под чужой операцией. Журнал install.log остаётся.
        static void Purge(Context ctx, bool keepBackups)
        {
            if (File.Exists(ctx.StateFile)) File.Delete(ctx.StateFile);
            // Файлы автообновления: статус, служебные данные и кэш манифеста (скачанные установщики лежат в updates\, он чистится ниже).
            foreach (string name in new[] { "update-status.json", "update-meta.json", "update-cache.json", "update-cache.sig" })
            {
                string file = Path.Combine(ctx.Data, name);
                if (File.Exists(file)) File.Delete(file);
            }
            foreach (string name in keepBackups ? new[] { "work", "updates" } : new[] { "work", "backup", "updates" })
            {
                string folder = Path.Combine(ctx.Data, name);
                if (!Directory.Exists(folder)) continue;
                if (Util.IsReparse(folder)) throw new StopException("acl", "Часть пути в папке данных является ссылкой (" + folder + "), чистка её не трогает.");
                Directory.Delete(folder, true);   // ссылки внутри удаляются как ссылки, по ним рекурсия не идёт
            }
            Log.Write("каталог данных очищен" + (keepBackups ? " (резервные копии оставлены)" : ""));
        }

        // parse-packages --pair <N.db>|<N_0.db> [--pair ...]: читает пары override-пакета без игры и записи (для тестов и этапа пересадки).
        // Параметры идут парами «имя значение», поэтому несколько пар передаются одним значением через «;»: --pairs "a.db|a_0.db;b.db|b_0.db".
        // Результат: pkg.<i>=ok|<число ресурсов>|<SHA заголовка> либо skip|<причина>; res.<i>.<id_hex>=<SHA нагрузки>|<байт>|<чанков>|<метаданные hex>.
        static void ParsePackages(Context ctx, Dictionary<string, string> args)
        {
            if (!args.ContainsKey("pairs")) throw new StopException("usage", "parse-packages: нужен параметр --pairs.");
            string[] pairs = args["pairs"].Split(new[] { ';' }, StringSplitOptions.RemoveEmptyEntries);
            for (int i = 0; i < pairs.Length; i++)
            {
                string[] parts = pairs[i].Split('|');
                if (parts.Length != 2) throw new StopException("usage", "parse-packages: пара должна быть «метаданные|данные».");
                try
                {
                    byte[] header;
                    var resources = OverridePackage.Read(parts[0], parts[1], out header);
                    using (var hash = new SHA256CryptoServiceProvider())
                        ctx.Result["pkg." + i] = "ok|" + resources.Count + "|" + Util.Hex(hash.ComputeHash(header));
                    foreach (var r in resources)
                        ctx.Result["res." + i + "." + r.Id.ToString("x16", CultureInfo.InvariantCulture)] = r.Sha + "|" + r.Payload.Length + "|" + r.ChunkData.Count + "|" + Util.Hex(r.Meta);
                }
                catch (FormatException error)
                {
                    ctx.Result["pkg." + i] = "skip|" + error.Message;   // пакет пропущен, остальные читаются дальше
                }
            }
        }

        static void Load(Context ctx)
        {
            ctx.M = Manifest.Load(ctx.Payload);
        }

        // ---- поиск игры -------------------------------------------------------------------

        static IEnumerable<string> Strings(object value)
        {
            if (value is string) { yield return (string)value; yield break; }
            var dict = value as Dictionary<string, object>;
            if (dict != null) { foreach (var v in dict.Values) foreach (var s in Strings(v)) yield return s; yield break; }
            var list = value as System.Collections.IEnumerable;
            if (list != null) foreach (var v in list) foreach (var s in Strings(v)) yield return s;
        }

        static List<string> PathsFromLauncherFile(string file)
        {
            var found = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            try
            {
                var root = new JavaScriptSerializer().DeserializeObject(File.ReadAllText(file, Encoding.UTF8));
                foreach (string text in Strings(root))
                {
                    string trimmed = text.Trim();
                    if (trimmed.Length == 0) continue;
                    // Берём только настоящие папки игры (есть WXQ.exe): прочие строки файла лаунчера не должны давать «несколько папок».
                    try
                    {
                        if (!Path.IsPathRooted(trimmed)) continue;
                        string norm = Util.NormPath(trimmed);
                        if (File.Exists(Path.Combine(norm, "WXQ.exe"))) found[norm] = trimmed;
                    }
                    catch (Exception) { }
                }
            }
            catch (Exception) { }
            return found.Keys.ToList();
        }

        static string RequireStructure(string root, bool full)
        {
            if (!Directory.Exists(root)) throw new StopException("game_missing", "Папка игры не найдена: " + root);
            if (!File.Exists(Path.Combine(root, "WXQ.exe"))) throw new StopException("game_missing", "В папке нет WXQ.exe, это не папка игры: " + root);
            if (!Directory.Exists(Path.Combine(root, "WXQ_Data"))) throw new StopException("game_missing", "В папке нет WXQ_Data, это не папка игры: " + root);
            if (full)
            {
                if (!Directory.Exists(Path.Combine(root, "WXQ_Data", "Package"))) throw new StopException("game_missing", "В папке игры нет WXQ_Data\\Package: " + root);
                if (!Directory.Exists(Path.Combine(root, "QtsDataFolder"))) throw new StopException("cache_missing", "У игры ещё нет папки QtsDataFolder. Запустите игру один раз, дойдите до меню, закройте её и повторите установку.");
            }
            return Util.NormPath(root);
        }

        static string ResolveGame(string explicitPath)
        {
            if (explicitPath != null)
            {
                Log.Write("папка игры задана параметром: " + explicitPath);
                return RequireStructure(explicitPath, true);
            }
            const string tail = @"os-launcher\Launcher\game-install-path.json";
            var own = new List<string>();
            // Только профиль самого устанавливающего пользователя; чужие профили не читаются (там файл мог подложить кто угодно).
            // Папка берётся у системы (SHGetKnownFolderPath), а не из переменной окружения; тестовая сборка принимает APPDATA из окружения.
            string appdata = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
#if TESTBUILD
            if (Environment.GetEnvironmentVariable("APPDATA") != null) appdata = Environment.GetEnvironmentVariable("APPDATA");
#endif
            if (!string.IsNullOrEmpty(appdata) && File.Exists(Path.Combine(appdata, tail))) own = PathsFromLauncherFile(Path.Combine(appdata, tail));
            if (own.Count == 1) { Log.Write("лаунчер текущего пользователя указывает: " + own[0]); return RequireStructure(own[0], true); }
            if (own.Count > 1) throw new StopException("game_ambiguous", "Лаунчер записал несколько папок игры, выбрать наугад нельзя. Запустите установщик с параметром /GAMEDIR=\"полный путь к папке WXQ\".");
            bool isolated = false;   // тестовый режим: стандартные папки не смотрим
#if TESTBUILD
            // WXQ_RU_USERS_ROOT - только в тестовой сборке: изолирует проверки от настоящих папок Program Files.
            if (Environment.GetEnvironmentVariable("WXQ_RU_USERS_ROOT") != null) isolated = true;
#endif
            // Запасной путь: единственная папка WXQ* рядом с лаунчером в Program Files.
            var guesses = new List<string>();
            var pfRoots = new List<string> { Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86) };
#if TESTBUILD
            pfRoots = new List<string> { Environment.GetEnvironmentVariable("ProgramFiles"), Environment.GetEnvironmentVariable("ProgramFiles(x86)") };
#endif
            var found = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (string pf in isolated ? new List<string>() : pfRoots)
            {
                if (string.IsNullOrEmpty(pf)) continue;
                AddGamesFromRoot(Path.Combine(pf, "WXQLauncher"), found);
            }
            // Другие источники: ключи удаления лаунчера, общие ярлыки, лаунчер на другом диске.
            if (!isolated)
            {
                foreach (string hint in GameHintsFromRegistry()) AddGameFromHint(hint, found);
                foreach (string hint in GameHintsFromShortcuts()) AddGameFromHint(hint, found);
                foreach (string hint in GameHintsFromDrives()) AddGameFromHint(hint, found);
            }
#if TESTBUILD
            // Подсказки для тестов: строки так же, как значения реестра (путь, путь в кавычках, путь,0), через «;».
            string testHints = Environment.GetEnvironmentVariable("WXQ_RU_TEST_HINTS");
            if (!string.IsNullOrEmpty(testHints))
                foreach (string hint in testHints.Split(new[] { ';' }, StringSplitOptions.RemoveEmptyEntries)) AddGameFromHint(hint, found);
#endif
            foreach (string path in found.Values) guesses.Add(path);
            if (guesses.Count == 1) { Log.Write("игра найдена по стандартным источникам: " + guesses[0]); return RequireStructure(guesses[0], true); }
            if (guesses.Count > 1)
                throw new StopException("game_ambiguous", "Найдено несколько папок игры (" + string.Join(", ", guesses.ToArray()) + "), выбрать наугад нельзя. Нажмите «Выбрать папку игры» в окне установщика или запустите установщик с параметром /GAMEDIR=\"полный путь к папке WXQ\".");
            throw new StopException("game_not_found", "Не удалось найти установленную игру. Запустите лаунчер Honor of Kings ACE хотя бы один раз и повторите установку. Если игра стоит в нестандартной папке, нажмите «Выбрать папку игры» в окне установщика или запустите установщик с параметром /GAMEDIR=\"полный путь к папке WXQ\".");
        }

        static readonly Regex GameHintName = new Regex("WXQ|王者万象棋", RegexOptions.IgnoreCase);

        // Корень лаунчера: подпапки WXQ* с WXQ.exe внутри.
        static void AddGamesFromRoot(string launcherRoot, Dictionary<string, string> found)
        {
            try
            {
                if (!Directory.Exists(launcherRoot)) return;
                foreach (string dir in Directory.GetDirectories(launcherRoot, "WXQ*"))
                    if (File.Exists(Path.Combine(dir, "WXQ.exe"))) { string norm = Util.NormPath(dir); found[norm] = norm; }
            }
            catch (Exception) { }
        }

        // Подсказка (значение реестра, цель ярлыка): путь к exe, возможно в кавычках, с «,0» или аргументами, либо папка. Игра - папка с WXQ.exe:
        // сама, любая WXQ* рядом с exe лаунчера или внутри его папки.
        static void AddGameFromHint(string hint, Dictionary<string, string> found)
        {
            if (string.IsNullOrWhiteSpace(hint)) return;
            try
            {
                string text = hint.Trim();
                if (text.StartsWith("\"")) { int end = text.IndexOf('"', 1); if (end > 1) text = text.Substring(1, end - 1); }
                int exe = text.IndexOf(".exe", StringComparison.OrdinalIgnoreCase);
                if (exe > 0) text = text.Substring(0, exe + 4);
                else { int comma = text.LastIndexOf(','); if (comma > 3) text = text.Substring(0, comma); }
                if (!Path.IsPathRooted(text)) return;
                string dir = Directory.Exists(text) ? text : Path.GetDirectoryName(text);
                if (string.IsNullOrEmpty(dir)) return;
                dir = Util.NormPath(dir);
                if (File.Exists(Path.Combine(dir, "WXQ.exe"))) { found[dir] = dir; return; }
                AddGamesFromRoot(dir, found);
                string parent = Directory.GetParent(dir) == null ? null : Directory.GetParent(dir).FullName;
                if (parent != null) AddGamesFromRoot(parent, found);
            }
            catch (Exception) { }
        }

        // Ключи удаления в HKLM (64 и 32 бита): запись, в имени или путях которой есть WXQ / 王者万象棋.
        static List<string> GameHintsFromRegistry()
        {
            var hints = new List<string>();
            foreach (var hiveKind in new[] { Microsoft.Win32.RegistryHive.LocalMachine, Microsoft.Win32.RegistryHive.CurrentUser })
            foreach (var view in new[] { Microsoft.Win32.RegistryView.Registry64, Microsoft.Win32.RegistryView.Registry32 })
            {
                try
                {
                    using (var hive = Microsoft.Win32.RegistryKey.OpenBaseKey(hiveKind, view))
                    using (var uninstall = hive.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"))
                    {
                        if (uninstall == null) continue;
                        foreach (string name in uninstall.GetSubKeyNames())
                        {
                            try
                            {
                                using (var entry = uninstall.OpenSubKey(name))
                                {
                                    if (entry == null) continue;
                                    string display = Convert.ToString(entry.GetValue("DisplayName"), CultureInfo.InvariantCulture) ?? "";
                                    string location = Convert.ToString(entry.GetValue("InstallLocation"), CultureInfo.InvariantCulture) ?? "";
                                    string icon = Convert.ToString(entry.GetValue("DisplayIcon"), CultureInfo.InvariantCulture) ?? "";
                                    string command = Convert.ToString(entry.GetValue("UninstallString"), CultureInfo.InvariantCulture) ?? "";
                                    if (!GameHintName.IsMatch(name + " " + display + " " + location + " " + icon + " " + command)) continue;
                                    hints.Add(location); hints.Add(icon); hints.Add(command);
                                }
                            }
                            catch (Exception) { }
                        }
                    }
                }
                catch (Exception) { }
            }
            return hints;
        }

        // Общие ярлыки (меню «Пуск» для всех, общий рабочий стол): цель, в пути которой есть WXQ / 王者万象棋.
        static List<string> GameHintsFromShortcuts()
        {
            var hints = new List<string>();
            try
            {
                var roots = new List<string>();
                string programs = Environment.GetFolderPath(Environment.SpecialFolder.CommonPrograms);
                string desktop = Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory);
                if (!string.IsNullOrEmpty(programs)) roots.Add(programs);
                if (!string.IsNullOrEmpty(desktop)) roots.Add(desktop);
                string userPrograms = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
                string userDesktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                if (!string.IsNullOrEmpty(userPrograms)) roots.Add(userPrograms);
                if (!string.IsNullOrEmpty(userDesktop)) roots.Add(userDesktop);
                Type type = Type.GetTypeFromProgID("WScript.Shell");
                if (type == null) return hints;
                dynamic shell = Activator.CreateInstance(type);
                foreach (string root in roots)
                {
                    if (!Directory.Exists(root)) continue;
                    string[] links;
                    try { links = Directory.GetFiles(root, "*.lnk", SearchOption.AllDirectories); } catch (Exception) { continue; }
                    foreach (string link in links)
                    {
                        try
                        {
                            string target = shell.CreateShortcut(link).TargetPath as string;
                            if (!string.IsNullOrEmpty(target) && GameHintName.IsMatch(target)) hints.Add(target);
                        }
                        catch (Exception) { }
                    }
                }
            }
            catch (Exception) { }
            return hints;
        }

        // Лаунчер, поставленный на другой фиксированный диск: <диск>:\Program Files\WXQLauncher и подобные.
        static List<string> GameHintsFromDrives()
        {
            var hints = new List<string>();
            try
            {
                foreach (var drive in DriveInfo.GetDrives())
                {
                    try
                    {
                        if (drive.DriveType != DriveType.Fixed || !drive.IsReady) continue;
                        foreach (string sub in new[] { @"Program Files\WXQLauncher", @"Program Files (x86)\WXQLauncher", "WXQLauncher", @"Games\WXQLauncher" })
                        {
                            string dir = Path.Combine(drive.RootDirectory.FullName, sub);
                            if (Directory.Exists(dir)) hints.Add(dir);
                        }
                    }
                    catch (Exception) { }
                }
            }
            catch (Exception) { }
            return hints;
        }

        static void Configure(Context ctx, string explicitGame)
        {
            ctx.Game = ResolveGame(explicitGame);
            ctx.Launcher = Directory.GetParent(ctx.Game).FullName;
            ctx.Cache = Path.Combine(ctx.Game, "QtsDataFolder", "QtsVFSCache", "packages");
            if (!Directory.Exists(ctx.Cache)) throw new StopException("cache_missing", "У игры ещё нет кэша ресурсов (QtsVFSCache). Запустите игру один раз, дойдите до меню, закройте её и повторите установку.");
        }

        static void Find(Context ctx, string explicitGame)
        {
            Configure(ctx, explicitGame);
            ctx.Result["game"] = ctx.Game;
            ctx.Result["launcher_dir"] = ctx.Launcher;
            // Ярлык из родителя папки игры открывается кнопкой «Запустить игру»: берём его только если игра внутри Program Files без ссылок
            // (как оригинальный лаунчер, LauncherPathTrusted), иначе подложенный в чужой папке ярлык не используется.
            string lnk = null;
            if (LauncherTrustRoots().Any(root => !string.IsNullOrEmpty(root) && ctx.Game.StartsWith(Util.NormPath(root) + "\\", StringComparison.OrdinalIgnoreCase)) && LauncherPathTrusted(ctx.Game))
                lnk = Directory.GetFiles(ctx.Launcher, "*.lnk").OrderBy(x => x, StringComparer.OrdinalIgnoreCase).FirstOrDefault(x => LauncherPathTrusted(x));
            ctx.Result["launcher_lnk"] = lnk ?? "";
            FindLauncherExe(ctx);
            Log.Write("игра: " + ctx.Game);
        }

        // Доверенные корни для поиска обычного ярлыка .lnk рядом с игрой.
        // EXE русского активатора проверяется отдельно: допустим локальный путь без reparse-точек.
        // В тестовой сборке корень задаёт WXQ_RU_TRUST_ROOT.
        static List<string> LauncherTrustRoots()
        {
            var roots = new List<string>();
#if TESTBUILD
            string test = Environment.GetEnvironmentVariable("WXQ_RU_TRUST_ROOT");
            if (!string.IsNullOrEmpty(test)) roots.Add(test);
#else
            roots.Add(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles));
            roots.Add(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86));
#endif
            return roots;
        }

        // Локальный путь без reparse-точек; запуск отдельно проверяет активатор с учётом прав процесса.
        static bool LauncherPathTrusted(string exe)
        {
            try
            {
                string full = Util.NormPath(exe), root = Path.GetPathRoot(full);
                if (full.StartsWith("\\\\", StringComparison.Ordinal) || root == null || root.Length != 3 || root[1] != ':') return false;
                string current = root;
                if (Util.IsReparse(current)) return false;
                foreach (string part in full.Substring(root.Length).Split('\\'))
                {
                    current = Path.Combine(current, part);
                    if (Util.IsReparse(current)) return false;
                }
                return true;
            }
            catch (Exception) { return false; }
        }

        static void AddLauncherHint(string hint, HashSet<string> roots)
        {
            if (string.IsNullOrWhiteSpace(hint)) return;
            try
            {
                string text = hint.Trim();
                if (text.StartsWith("\"")) { int end = text.IndexOf('"', 1); if (end > 1) text = text.Substring(1, end - 1); }
                int exe = text.IndexOf(".exe", StringComparison.OrdinalIgnoreCase);
                if (exe >= 0) text = text.Substring(0, exe + 4);
                if (!Path.IsPathRooted(text)) return;
                string dir = Directory.Exists(text) ? text : Path.GetDirectoryName(text);
                if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir)) return;
                roots.Add(Util.NormPath(dir));
                var parent = Directory.GetParent(dir);
                if (parent != null) roots.Add(Util.NormPath(parent.FullName));
            }
            catch (Exception) { }
        }

        // Оригинальный exe лаунчера для русского активатора: по списку сборок launcher-profiles.json (в payload), путь строго внутри папки лаунчера
        // и внутри Program Files без ссылок. Пусто, если списка нет или ни одна сборка не найдена: установщик тогда не ставит русский лаунчер
        // и не пишет target.txt. Хеши проверяет сам активатор при запуске.
        static void FindLauncherExe(Context ctx)
        {
            ctx.Result["launcher_exe"] = "";
            ctx.Result["launcher_build"] = "";
            ctx.Result["launcher_reason"] = "profile-not-found";
            string profiles = Path.Combine(ctx.Payload ?? "", "launcher-profiles.json");
            if (ctx.Payload == null || !File.Exists(profiles)) return;
            try
            {
                var roots = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { Util.NormPath(ctx.Launcher) };
                bool isolated = false;
#if TESTBUILD
                isolated = Environment.GetEnvironmentVariable("WXQ_RU_USERS_ROOT") != null;
                string hints = Environment.GetEnvironmentVariable("WXQ_RU_TEST_HINTS");
                if (!string.IsNullOrEmpty(hints)) foreach (string hint in hints.Split(';')) AddLauncherHint(hint, roots);
#endif
                if (!isolated)
                {
                    foreach (string pf in LauncherTrustRoots()) if (!string.IsNullOrEmpty(pf)) AddLauncherHint(Path.Combine(pf, "WXQLauncher"), roots);
                    foreach (string hint in GameHintsFromRegistry().Concat(GameHintsFromShortcuts()).Concat(GameHintsFromDrives())) AddLauncherHint(hint, roots);
                }
                var data = Util.ReadJson(profiles);
                if (data == null || Util.Num(data, "schema") != 1) return;
                var matches = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                foreach (object item in Util.Items(data["builds"]))
                {
                    var row = item as Dictionary<string, object>;
                    string rel = row == null ? null : Util.Str(row, "exe_rel");
                    if (string.IsNullOrEmpty(rel)) continue;
                    rel = rel.Replace('/', '\\').Trim('\\');
                    if (rel.Length == 0 || rel.Contains("..") || Path.IsPathRooted(rel) || !rel.EndsWith(".exe", StringComparison.OrdinalIgnoreCase)) continue;
                    foreach (string directory in roots)
                    {
                        string exe = Path.GetFullPath(Path.Combine(directory, rel));
                        if (!exe.StartsWith(directory.TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase) || !File.Exists(exe)) continue;
                        if (!LauncherPathTrusted(exe)) { ctx.Result["launcher_reason"] = "unsafe-path"; continue; }
                        matches[exe] = Util.Str(row, "build") ?? "";
                    }
                }
                if (matches.Count > 1) { ctx.Result["launcher_reason"] = "ambiguous"; return; }
                if (matches.Count == 0) return;
                var match = matches.First();
                ctx.Result["launcher_exe"] = match.Key;
                ctx.Result["launcher_build"] = match.Value;
                ctx.Result["launcher_reason"] = "found";
                Log.Write("оригинальный лаунчер: " + match.Key);
            }
            catch (Exception error) { Log.Write("список сборок лаунчера не прочитан: " + error.Message); }
        }

        // ---- каталог данных и список receipt ---------------------------------------------------

        static readonly SecurityIdentifier SidAdmins = new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null);
        static readonly SecurityIdentifier SidSystem = new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null);

        // Права на запись, наличие которых у постороннего субъекта делает файл или папку недоверенными.
        const FileSystemRights WriteLike = FileSystemRights.WriteData | FileSystemRights.AppendData | FileSystemRights.WriteExtendedAttributes |
            FileSystemRights.WriteAttributes | FileSystemRights.DeleteSubdirectoriesAndFiles | FileSystemRights.Delete |
            FileSystemRights.ChangePermissions | FileSystemRights.TakeOwnership;

        // Дополнительный доверенный владелец сверх Администраторов и SYSTEM. Область: "dir" (каталог данных), "parent" (родитель), "nested" (вложенные файлы).
        // Релиз: никого сверх Администраторов и SYSTEM (ни каталог данных, ни родитель, ни вложенные файлы).
        // Тестовая сборка (без повышения): текущий пользователь; WXQ_RU_TEST_STRICT_OWNER=<область> лишает его доверия в этой области (проверка отказа).
        static SecurityIdentifier TrustedUser(string scope)
        {
#if TESTBUILD
            string strict = Environment.GetEnvironmentVariable("WXQ_RU_TEST_STRICT_OWNER");
            if (strict == scope) return null;
            return WindowsIdentity.GetCurrent().User;
#else
            return null;   // релиз: доверены только Администраторы и SYSTEM во всех областях
#endif
        }

        static bool IsTrusted(SecurityIdentifier sid, string scope)
        {
            if (sid == null) return false;
            if (sid.Equals(SidAdmins) || sid.Equals(SidSystem)) return true;
            var user = TrustedUser(scope);
            return user != null && user.Equals(sid);
        }

        static FileSystemSecurity ReadSecurity(string path, bool isDirectory)
        {
            var sections = AccessControlSections.Owner | AccessControlSections.Access;
            return isDirectory ? (FileSystemSecurity)Directory.GetAccessControl(path, sections) : File.GetAccessControl(path, sections);
        }

        static SecurityIdentifier OwnerOf(string path, bool isDirectory)
        {
            return ReadSecurity(path, isDirectory).GetOwner(typeof(SecurityIdentifier)) as SecurityIdentifier;
        }

        // Всё внутри каталога данных после того, как каталог закрыт: ни ссылок, ни чужих владельцев, ни собственных (не унаследованных) разрешений на запись
        // для посторонних. Правило: отказ (acl), а не молчаливое исправление; пользователь убирает такую папку сам и устанавливает заново.
        static void CheckDataTree(string dir)
        {
            foreach (FileSystemInfo item in new DirectoryInfo(dir).GetFileSystemInfos())
            {
                if ((item.Attributes & FileAttributes.ReparsePoint) != 0)
                    throw new StopException("acl", "В папке данных русификатора лежит ссылка (" + item.FullName + "), установщик такой папке не доверяет. Уберите папку от имени администратора и повторите.");
                bool isDirectory = (item.Attributes & FileAttributes.Directory) != 0;
                var security = ReadSecurity(item.FullName, isDirectory);
                if (!IsTrusted(security.GetOwner(typeof(SecurityIdentifier)) as SecurityIdentifier, "nested"))
                    throw new StopException("acl", "Файл в папке данных русификатора создан не администратором (" + item.FullName + "), установщик такой папке не доверяет. Уберите папку от имени администратора и повторите.");
                foreach (FileSystemAccessRule rule in security.GetAccessRules(true, false, typeof(SecurityIdentifier)))
                {
                    if (rule.AccessControlType != AccessControlType.Allow || (rule.FileSystemRights & WriteLike) == 0) continue;
                    if (!IsTrusted(rule.IdentityReference as SecurityIdentifier, "nested"))
                        throw new StopException("acl", "У файла в папке данных русификатора есть чужое разрешение на запись (" + item.FullName + "), установщик такой папке не доверяет. Уберите папку от имени администратора и повторите.");
                }
                if (isDirectory) CheckDataTree(item.FullName);
            }
        }

        // Каталог данных (резервные копии, receipt, state) пишут только Администраторы и SYSTEM; обычные пользователи читают.
        // Иначе обычный пользователь подменил бы receipt, и откат под администратором писал бы по чужим путям.
        // Родитель (%ProgramData%\Egoist) обязан принадлежать Администраторам или SYSTEM, его права не меняются (ими пользуются другие программы Egoist).
        // Каталог данных: владелец не Администраторы/SYSTEM - отказ (acl), пустая папка или нет; владение не забирается. Затем права выставляются заново
        // (наследование отключено), и всё вложенное проверяется (CheckDataTree) уже после закрытия, чтобы файл, подложенный между проверками, не прошёл.
        static void SecureDataDir(Context ctx)
        {
            string dir = ctx.Data;
            try
            {
                if (Directory.Exists(dir) && Util.IsReparse(dir))
                    throw new StopException("acl", "Папка данных русификатора является ссылкой (" + dir + "), установщик её не использует.");
                string parent = Path.GetDirectoryName(dir);
                if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
                if (!string.IsNullOrEmpty(parent))
                {
                    if (Util.IsReparse(parent))
                        throw new StopException("acl", "Папка " + parent + " является ссылкой, установщик её не использует.");
                    if (!IsTrusted(OwnerOf(parent, true), "parent"))
                        throw new StopException("acl", "Папка " + parent + " принадлежит не администратору (создана обычным пользователем). Установщик ей не доверяет и ничего в неё не пишет.");
                }
                var users = new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid, null);
                // Чужой владелец - всегда отказ, пустая папка или нет: владелец мог сохранить открытый дескриптор и вернуть себе права. Владение не забираем.
                if (!IsTrusted(OwnerOf(dir, true), "dir"))
                    throw new StopException("acl", "Папка данных русификатора (" + dir + ") создана не администратором. Установщик ей не доверяет и ничего в неё не пишет: уберите папку от имени администратора и повторите.");
                var security = new DirectorySecurity();
                security.SetAccessRuleProtection(true, false);   // наследование от родителя отключено, чужие записи не копируются
                var inherit = InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit;
                security.AddAccessRule(new FileSystemAccessRule(SidAdmins, FileSystemRights.FullControl, inherit, PropagationFlags.None, AccessControlType.Allow));
                security.AddAccessRule(new FileSystemAccessRule(SidSystem, FileSystemRights.FullControl, inherit, PropagationFlags.None, AccessControlType.Allow));
#if TESTBUILD
                // Только тестовая сборка: без прав текущего пользователя тест запер бы себя. В релизе такой записи нет.
                var me = WindowsIdentity.GetCurrent().User;
                if (me != null) security.AddAccessRule(new FileSystemAccessRule(me, FileSystemRights.FullControl, inherit, PropagationFlags.None, AccessControlType.Allow));
#endif
                security.AddAccessRule(new FileSystemAccessRule(users, FileSystemRights.ReadAndExecute, inherit, PropagationFlags.None, AccessControlType.Allow));
                Directory.SetAccessControl(dir, security);
                if (!IsTrusted(OwnerOf(dir, true), "dir"))
                    throw new StopException("acl", "Не удалось сделать владельцем папки данных русификатора администраторов (" + dir + "). Запустите установщик от администратора.");
                CheckDataTree(dir);
            }
            catch (StopException) { throw; }
            catch (Exception error)
            {
                throw new StopException("acl", "Не удалось закрыть папку данных русификатора от записи обычным пользователям (" + dir + "): " + error.Message + " Запустите установщик от администратора.");
            }
        }

        // Метка резервной копии -> время UTC (запасной вариант, если в receipt нет created_utc).
        static DateTime LabelTime(string label)
        {
            DateTime parsed;
            string clean = (label ?? "").TrimEnd('x');
            if (DateTime.TryParseExact(clean, "yyyyMMdd-HHmmss", CultureInfo.InvariantCulture, DateTimeStyles.AssumeLocal | DateTimeStyles.AdjustToUniversal, out parsed)) return parsed;
            return DateTime.MinValue;
        }

        static DateTime ReceiptTime(string dir, Dictionary<string, object> receipt)
        {
            DateTime parsed;
            string created = Util.Str(receipt, "created_utc");
            if (created != null && DateTime.TryParse(created, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out parsed)) return parsed;
            return LabelTime(Path.GetFileName(dir));
        }

        // Все receipt, от новых к старым (по created_utc, а не по местному времени в метке). Нечитаемые пропускаются с записью в журнал.
        static List<KeyValuePair<string, Dictionary<string, object>>> ListReceipts(Context ctx)
        {
            var rows = new List<KeyValuePair<string, Dictionary<string, object>>>();
            if (!Directory.Exists(ctx.BackupRoot)) return rows;
            foreach (string dir in Directory.GetDirectories(ctx.BackupRoot))
            {
                string file = Path.Combine(dir, "receipt.json");
                if (!File.Exists(file)) continue;
                try
                {
                    var receipt = Util.ReadJson(file);
                    if (receipt != null) rows.Add(new KeyValuePair<string, Dictionary<string, object>>(dir, receipt));
                }
                catch (Exception error) { Log.Write("receipt " + Path.GetFileName(dir) + " нечитаем, пропуск: " + error.Message); }
            }
            return rows.OrderByDescending(r => ReceiptTime(r.Key, r.Value)).ThenByDescending(r => Path.GetFileName(r.Key), StringComparer.Ordinal).ToList();
        }

        // Статусы receipt, при которых файлы игры могут быть в промежуточном состоянии.
        static bool IsPartialStatus(string status)
        {
            return status == "applying" || status == "partial-requires-restore" || status == "restore-in-progress" || status == "restore-partial";
        }

        static bool SameGame(Dictionary<string, object> receipt, string game)
        {
            string root = Util.Str(receipt, "game_root");
            return root != null && game != null && Util.SamePath(root, game);
        }

        static void Status(Context ctx, string gameArg)
        {
            ctx.Result["installed"] = "no";
            ctx.Result["pending"] = "no";
            ctx.Result["interrupted"] = "no";
            // Прерванная установка: есть receipt не в состоянии restored/prepared. Считаем только свою игру (или существующие папки игры).
            string current = null;
            try { current = ResolveGame(gameArg); } catch (Exception) { }
            var all = ListReceipts(ctx);
            if (current != null && !all.Any(r => SameGame(r.Value, current))) current = null;   // найденная игра не наша: смотрим игры из самих receipt
            foreach (var pair in all)
            {
                string status = Util.Str(pair.Value, "status");
                if (status == "restored" || status == "prepared") continue;
                string root = Util.Str(pair.Value, "game_root");
                bool mine = current != null ? SameGame(pair.Value, current) : (root != null && Directory.Exists(root));
                if (!mine) continue;
                ctx.Result["pending"] = "yes";
                if (IsPartialStatus(status)) ctx.Result["interrupted"] = "yes";
            }
            if (!File.Exists(ctx.StateFile)) return;
            try
            {
                var state = Util.ReadJson(ctx.StateFile);
                ctx.Result["installed"] = Util.Str(state, "installed") == "True" || Util.Str(state, "installed") == "true" ? "yes" : "no";
                ctx.Result["version"] = Util.Str(state, "version") ?? "";
                ctx.Result["built"] = Util.Str(state, "built") ?? "";
                ctx.Result["game_version"] = Util.Str(state, "game_version") ?? "";
                ctx.Result["game"] = Util.Str(state, "game_root") ?? "";
            }
            catch (Exception) { }
        }

        // ---- проверки ---------------------------------------------------------------------

        static void CheckPayload(Context ctx)
        {
            foreach (var p in ctx.M.Pkgs)
                foreach (var f in p.Files)
                {
                    string path = Path.Combine(ctx.Payload, f.PayloadName);
                    if (!File.Exists(path) || new FileInfo(path).Length != f.Bytes || Util.Sha(path) != f.Sha)
                        throw new StopException("payload", "Файл перевода в установщике повреждён (" + f.PayloadName + "). Скачайте установщик заново. В игру ничего не записано.");
                }
        }

        static void RequireClosed(Context ctx)
        {
            // Те же правила, что у Closer: корни игры и лаунчера, точные имена вне корней, запрет системных процессов.
            var active = Closer.ActiveNames(ctx);
            if (active.Count > 0)
                throw new StopException("running", "Игра или лаунчер сейчас запущены (" + string.Join(", ", active.Distinct().ToArray()) + "). Закройте их полностью, в том числе в области уведомлений, и повторите.");
        }

        static string ReadVersion(string infoPath)
        {
            try
            {
                var info = Util.ReadJson(infoPath);
                return Util.Str(info, "version");
            }
            catch (Exception) { return null; }
        }

        // Хеши prime. Для того же файла (путь, размер, время) в пределах сеанса установки повторно не считаются.
        static void VerifyPrime(Context ctx, bool useMemo)
        {
            var memo = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            if (useMemo && File.Exists(ctx.MemoFile))
            {
                try
                {
                    var saved = Util.ReadJson(ctx.MemoFile);
                    if ((DateTime.UtcNow - DateTime.Parse(Util.Str(saved, "utc"), CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind)).TotalMinutes < 30)
                        foreach (var pair in (Dictionary<string, object>)saved["files"]) memo[pair.Key] = Convert.ToString(pair.Value);
                }
                catch (Exception) { memo.Clear(); }
            }
            var fresh = new Dictionary<string, object>();
            string builtin = Path.Combine(ctx.Game, "WXQ_Data", "Package", "builtin");
            foreach (var row in ctx.M.Prime)
            {
                string path = Path.Combine(builtin, row.Relative.Replace('/', '\\'));
                if (!File.Exists(path) || Util.IsReparse(path))
                    throw new StopException("version", "Не найден файл игры " + row.Relative + ". Скорее всего, версия игры не та, для которой собран перевод. Ничего не изменено.");
                var info = new FileInfo(path);
                if (info.Length != row.Bytes)
                    throw new StopException("version", "Файл игры " + row.Relative + " не совпадает с ожидаемым (другой размер). Скорее всего, игра обновилась и перевод для неё ещё не готов. Ничего не изменено.");
            }
            foreach (var row in ctx.M.Prime)
            {
                string path = Path.Combine(builtin, row.Relative.Replace('/', '\\'));
                var info = new FileInfo(path);
                string stamp = info.Length + "|" + info.LastWriteTimeUtc.Ticks;
                string sha = null;
                string cached;
                if (memo.TryGetValue(path, out cached) && cached.StartsWith(stamp + "|", StringComparison.Ordinal) && cached.Substring(stamp.Length + 1) == row.Sha) { sha = row.Sha; Progress.Skip(path, info.Length); }
                if (sha == null) sha = Util.Sha(path);
                if (!string.Equals(sha, row.Sha, StringComparison.OrdinalIgnoreCase))
                    throw new StopException("version", "Файл игры " + row.Relative + " не совпадает с ожидаемым (другое содержимое). Скорее всего, игра обновилась или файл повреждён. Ничего не изменено.");
                fresh[path] = stamp + "|" + sha;
            }
            Util.WriteJson(ctx.MemoFile, new Dictionary<string, object> { { "utc", DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture) }, { "files", fresh } });
            Log.Write("prime-файлы игры совпали с ожидаемыми: " + ctx.M.Prime.Count);
        }

        static void CheckIndexAndSdk(Context ctx)
        {
            string index = Path.Combine(ctx.Cache, "builtin", "Globalindex", "GlobalIndexPrime.data");
            if (!File.Exists(index) || Util.Sha(index) != ctx.M.IndexSha)
                throw new StopException("version", "Служебный индекс игры (GlobalIndexPrime.data) не совпадает с ожидаемым. Скорее всего, игра обновилась. Ничего не изменено.");
            foreach (var pair in ctx.M.Sdk)
            {
                string dll = Path.Combine(ctx.Game, "WXQ_Data", "Plugins", "x86_64", pair.Key);
                if (!File.Exists(dll) || Util.Sha(dll) != pair.Value)
                    throw new StopException("version", "Библиотека игры " + pair.Key + " не совпадает с ожидаемой. Скорее всего, игра обновилась. Ничего не изменено.");
            }
        }

        // Мягкая проверка pkgHash по журналам QtsVFS: противоречие — остановка, отсутствие записи — только заметка в журнале
        // (жёстко версию подтверждают SHA prime и заголовок override).
        static void CheckMountLog(Context ctx)
        {
            string dir = Path.Combine(ctx.Game, "QtsDataFolder", "QtsVFSCache");
            var seen = new Dictionary<string, string>();
            foreach (string name in new[] { "QtsVFS.log", "QtsVFS1.log", "QtsVFS2.log", "QtsVFS3.log" })
            {
                string path = Path.Combine(dir, name);
                if (!File.Exists(path)) continue;
                string text;
                try { using (var s = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete)) using (var r = new StreamReader(s, Encoding.UTF8)) text = r.ReadToEnd(); }
                catch (IOException) { continue; }
                foreach (Match m in MountLine.Matches(text))
                {
                    if (m.Groups[2].Value != m.Groups[3].Value) continue;
                    if (!seen.ContainsKey(m.Groups[1].Value)) seen[m.Groups[1].Value] = m.Groups[2].Value;
                }
            }
            foreach (var p in ctx.M.Pkgs)
            {
                string actual;
                if (!seen.TryGetValue(p.Id, out actual)) { Log.Write("в журналах QtsVFS нет записи о prime-пакете " + p.Id + " (пропуск мягкой проверки)"); continue; }
                if (actual != p.PrimePkgHash)
                    throw new StopException("version", "Хеш пакета " + p.Id + " в журнале игры не совпадает с ожидаемым: перевод собран для другой сборки игры. Ничего не изменено.");
            }
        }

        // Возвращает null, если каталог «чистый» (только заголовок, 120 байт, без хранилища), иначе причину.
        static string ForeignReason(string catalogPath, string storagePath, byte[] expectedHeader)
        {
            try
            {
                var info = new FileInfo(catalogPath);
                if (info.Length > 64L * 1024 * 1024) return "каталог необычно велик";
                var catalog = Catalog.Parse(File.ReadAllBytes(catalogPath));
                if (catalog.Invalid > 0) return "повреждённые записи";
                if (catalog.Records.Count != 1 || !catalog.Records.ContainsKey(HeaderId))
                    return (catalog.Records.Count - (catalog.Records.ContainsKey(HeaderId) ? 1 : 0)) + " запис(ей) кроме заголовка";
                var values = catalog.RawValues(HeaderId);
                if (values.Count != 1 || values[0].Length != HeaderBytes) return "необычный заголовок";
                if (expectedHeader != null && !values[0].SequenceEqual(expectedHeader)) return "заголовок не совпадает с ожидаемым (другая сборка игры)";
                if (File.Exists(storagePath)) return "рядом с пустым каталогом есть файл данных";
            }
            catch (FormatException) { return "каталог нечитаем"; }
            catch (IOException) { return "каталог недоступен"; }
            return null;
        }

        static string TargetPath(Context ctx, string relative)
        {
            var match = RelativeName.Match(relative);
            if (!match.Success) throw new StopException("internal", "Недопустимое имя файла в данных перевода: " + relative);
            string target = Util.NormPath(Path.Combine(ctx.Cache, match.Groups[1].Value, relative.Substring(relative.LastIndexOf('/') + 1)));
            if (!target.StartsWith(Util.NormPath(ctx.Cache) + "\\", StringComparison.OrdinalIgnoreCase))
                throw new StopException("internal", "Путь вне папки кэша игры: " + relative);
            RequireNoLinks(ctx, target);
            return target;
        }

        // Ни одна часть пути от папки игры до файла (QtsDataFolder, QtsVFSCache, packages, packages\N, сам файл) не должна быть
        // ссылкой (junction/symlink): запись или удаление по ней ушли бы за пределы игры.
        static void RequireNoLinks(Context ctx, string target)
        {
            string root = Util.NormPath(ctx.Game);
            string full = Util.NormPath(target);
            if (!full.StartsWith(root + "\\", StringComparison.OrdinalIgnoreCase))
                throw new StopException("internal", "Путь вне папки игры: " + target);
            string current = root;
            foreach (string part in full.Substring(root.Length + 1).Split('\\'))
            {
                current = current + "\\" + part;
                if ((Directory.Exists(current) || File.Exists(current)) && Util.IsReparse(current))
                    throw new StopException("reparse", "Часть пути в папке игры является ссылкой (" + current + "); установщик по ссылкам ничего не пишет и не удаляет. Ничего не изменено.");
            }
        }

        // Прошлая установка или удаление оборвались на середине: файлы игры в промежуточном состоянии, их надо сначала откатить.
        static void RequireNoPending(Context ctx)
        {
            foreach (var pair in ListReceipts(ctx))
            {
                string status = Util.Str(pair.Value, "status");
                if (!SameGame(pair.Value, ctx.Game)) continue;
                if (IsPartialStatus(status))
                    throw new StopException("pending", "Прошлая установка или удаление перевода не завершились (" + Path.GetFileName(pair.Key) + "). Закройте игру и лаунчер и нажмите «Удалить перевод» в окне установщика: файлы вернутся к прежнему состоянию. Потом повторите установку.");
            }
        }

        // Состояние override: список операций и общий вид («fresh», «update», «same»).
        static List<Op> PlanOverride(Context ctx, out string state)
        {
            var ops = new List<Op>();
            PlanOtherPackages(ctx, ops);
            PlanOwnPackages(ctx, ops, out state);
            if (state == "same" && ops.Any(o => !o.Unchanged)) state = "update";
            return ops;
        }

        static byte[] CacheHeader(Context ctx, string id, string catalog, byte[] expected)
        {
            RequireNoLinks(ctx, catalog);
            try
            {
                if (new FileInfo(catalog).Length > 64L * 1024 * 1024) throw new FormatException("size");
                var table = Catalog.Parse(File.ReadAllBytes(catalog));
                var values = table.RawValues(HeaderId);
                if (table.Invalid != 0 || values.Count != 1 || values[0].Length != HeaderBytes) throw new FormatException("header");
                var header = values[0];
                if (BitConverter.ToInt32(header, 0) != 12 || Encoding.ASCII.GetString(header, 4, 12) != "QTSF_PACKAGE")
                    throw new FormatException("package signature");
                if (expected != null && !header.SequenceEqual(expected)) throw new FormatException("different build or official update");
                if (HeaderHash(header, 80) != HeaderHash(header, 88)) throw new FormatException("official update");
                return header;
            }
            catch (FormatException)
            {
                throw new StopException("foreign", "Кэш пакета " + id + " повреждён или содержит официальное обновление для другой сборки. Его нельзя заменить старым переводом. Выполните штатную проверку файлов игры и повторите установку. Ничего не изменено.");
            }
        }

        static Op CacheOp(Context ctx, Pkg p, string name, byte[] bytes)
        {
            string relative = "packages/" + p.Id + "/" + name;
            string target = TargetPath(ctx, relative);
            bool exists = File.Exists(target);
            return new Op { Package = p, Relative = relative, Target = target, SourceBytes = bytes,
                Row = new FileRow { Name = name, Sha = bytes == null ? null : Sha256Of(bytes), Bytes = bytes == null ? 0 : bytes.Length },
                BeforeExists = exists, BeforeSha = exists ? Util.Sha(target) : null };
        }

        // Только файлы данных данного numeric-пакета: не .info, логи, пользовательские файлы или соседние каталоги.
        static IEnumerable<string> CacheStorages(Context ctx, string id)
        {
            string dir = Path.Combine(ctx.Cache, id);
            RequireNoLinks(ctx, dir);
            if (!Directory.Exists(dir)) return new string[0];
            var pattern = new Regex("^" + Regex.Escape(id) + @"_[0-9]{1,10}\.db$", RegexOptions.IgnoreCase);
            var files = Directory.GetFiles(dir).Where(f => pattern.IsMatch(Path.GetFileName(f))).OrderBy(f => f, StringComparer.OrdinalIgnoreCase).ToList();
            foreach (string file in files) RequireNoLinks(ctx, file);
            return files;
        }

        // Старые переводы могут оставлять ресурсы и в пакетах, которых уже нет в новом payload.
        // Официальную delta отличаем по заголовку и журналу: сбрасывать её под видом перевода нельзя.
        static void PlanOtherPackages(Context ctx, List<Op> ops)
        {
            var own = new HashSet<string>(ctx.M.Pkgs.Select(p => p.Id));
            foreach (string dir in Directory.GetDirectories(ctx.Cache))
            {
                string id = Path.GetFileName(dir);
                if (!PackageId.IsMatch(id) || own.Contains(id)) continue;
                RequireNoLinks(ctx, dir);
                string catalog = Path.Combine(dir, id + ".db"), storage = Path.Combine(dir, id + "_0.db");
                var storages = CacheStorages(ctx, id).ToList();
                if (!File.Exists(catalog))
                {
                    if (storages.Count != 0) throw new StopException("foreign", "У пакета " + id + " отсутствует каталог ресурсов. Выполните штатную проверку игры. Ничего не изменено.");
                    continue;
                }
                byte[] header = CacheHeader(ctx, id, catalog, null);
                var table = Catalog.Parse(File.ReadAllBytes(catalog));
                if (table.Records.Count == 1 && storages.Count == 0) continue;
                var p = new Pkg { Id = id };
                string info = TargetPath(ctx, "packages/" + id + "/" + id + ".info");
                if (ReadVersion(info) != ctx.M.GameVersion && !TransplantMode(ctx))
                    throw new StopException("foreign", "Версия соседнего пакета " + id + " не совпала с игрой. Выполните штатную проверку файлов игры. Ничего не изменено.");
                string prime = Path.Combine(ctx.Game, "WXQ_Data", "Package", "builtin", id, id + "_0.db");
                RequireNoLinks(ctx, prime);
                if (!File.Exists(prime) || HeaderHash(header, 80) != RequireLogHash(ctx, p, null))
                    throw new StopException("foreign", "Не подтверждён оригинал соседнего пакета " + id + ". Выполните штатную проверку файлов игры. Ничего не изменено.");
                ops.Add(CacheOp(ctx, p, id + ".db", Catalog.HeaderOnly(header)));
                foreach (string file in storages) ops.Add(CacheOp(ctx, p, Path.GetFileName(file), null));
                Log.Write("предусмотрена очистка прежнего перевода: пакет " + id + ", записей " + (table.Records.Count - 1));
            }
        }

        static List<Op> PlanOwnPackages(Context ctx, List<Op> ops, out string state)
        {
            bool allSame = true, anyOurs = false;
            foreach (var p in ctx.M.Pkgs)
            {
                string dir = Path.Combine(ctx.Cache, p.Id);
                string catalog = Path.Combine(dir, p.Id + ".db"), storage = Path.Combine(dir, p.Id + "_0.db"), info = Path.Combine(dir, p.Id + ".info");
                RequireNoLinks(ctx, info);
                RequireNoLinks(ctx, catalog);
                if (!File.Exists(info) || !File.Exists(catalog))
                    throw new StopException("cache_missing", "Игра ещё не создала кэш ресурсов для пакета " + p.Id + ". Запустите игру один раз, дойдите до меню, закройте её и повторите установку.");
                string version = ReadVersion(info);
                if (version != p.InfoVersion || version != ctx.M.GameVersion)
                    throw new StopException("version", "Версия игры (" + (version ?? "неизвестна") + ") не совпадает с версией, для которой собран перевод (" + ctx.M.GameVersion + "). Ничего не изменено. " + RefreshHint);
                if (Util.Sha(info) != p.InfoSha)
                    throw new StopException("version", "Служебный файл версии пакета " + p.Id + " отличается от ожидаемого. Скорее всего, игра обновилась. Ничего не изменено.");
                string catalogSha = Util.Sha(catalog);
                bool ours = ctx.M.IsKnown("packages/" + p.Id + "/" + p.Id + ".db", catalogSha);
                CacheHeader(ctx, p.Id, catalog, Util.FromHex(p.HeaderHex));
                if (ours) anyOurs = true;
                var payloadNames = new HashSet<string>(p.Files.Select(f => f.Name), StringComparer.OrdinalIgnoreCase);
                foreach (string file in CacheStorages(ctx, p.Id))
                    if (!payloadNames.Contains(Path.GetFileName(file)))
                    {
                        ops.Add(CacheOp(ctx, p, Path.GetFileName(file), null));
                        allSame = false;
                    }
                foreach (var f in p.Files)
                {
                    string relative = "packages/" + p.Id + "/" + f.Name;
                    var op = new Op { Package = p, Row = f, Relative = relative, Target = TargetPath(ctx, relative), Source = Path.Combine(ctx.Payload, f.PayloadName) };
                    op.BeforeExists = File.Exists(op.Target);
                    op.BeforeSha = op.BeforeExists ? Util.Sha(op.Target) : null;
                    if (!op.Unchanged) allSame = false;
                    ops.Add(op);
                }
            }
            state = allSame ? "same" : (anyOurs ? "update" : "fresh");
            return ops;
        }

        // ---- оценка объёма работы для --progress (только размеры файлов, содержимое не читается) -----

        class Plan
        {
            public long Total;
            public int Count;
            public void Add(long bytes, int times) { Total += bytes * times; Count += times; }
            public void Apply() { Progress.SetPlan(Total, Count); }
            // Размер файла или -1, если его нет.
            public static long Size(string path)
            {
                try { var info = new FileInfo(path); return info.Exists ? info.Length : -1; }
                catch (Exception) { return -1; }
            }
        }

        static void PlanPayload(Context ctx, Plan plan)
        {
            foreach (var p in ctx.M.Pkgs)
                foreach (var f in p.Files)
                {
                    long size = Plan.Size(Path.Combine(ctx.Payload, f.PayloadName));
                    plan.Add(size >= 0 ? size : f.Bytes, 1);
                }
        }

        // Хеши prime, индекса, библиотек и override из PreflightAll.
        static void PlanPreflight(Context ctx, Plan plan)
        {
            foreach (var row in ctx.M.Prime) plan.Add(row.Bytes, 1);
            plan.Add(Math.Max(0, Plan.Size(Path.Combine(ctx.Cache, "builtin", "Globalindex", "GlobalIndexPrime.data"))), 1);
            foreach (var pair in ctx.M.Sdk) plan.Add(Math.Max(0, Plan.Size(Path.Combine(ctx.Game, "WXQ_Data", "Plugins", "x86_64", pair.Key))), 1);
            PlanOverrideHashes(ctx, plan);
        }

        // Хеши, которые считает PlanOverride: чужие каталоги, а у своих пакетов info, каталог, хранилище и целевые файлы.
        static void PlanOverrideHashes(Context ctx, Plan plan)
        {
            try
            {
                var own = new HashSet<string>(ctx.M.Pkgs.Select(p => p.Id));
                foreach (string dir in Directory.GetDirectories(ctx.Cache))
                {
                    string id = Path.GetFileName(dir);
                    if (!PackageId.IsMatch(id) || own.Contains(id)) continue;
                    long size = Plan.Size(Path.Combine(dir, id + ".db"));
                    if (size >= 0) plan.Add(size, 1);
                }
            }
            catch (Exception) { }
            foreach (var p in ctx.M.Pkgs)
            {
                string dir = Path.Combine(ctx.Cache, p.Id);
                foreach (string name in new[] { p.Id + ".info", p.Id + ".db", p.Id + "_0.db" })
                {
                    long size = Plan.Size(Path.Combine(dir, name));
                    if (size >= 0) plan.Add(size, 1);
                }
                foreach (var f in p.Files)
                {
                    long size = Plan.Size(Path.Combine(dir, f.Name));
                    if (size >= 0) plan.Add(size, 1);
                }
            }
        }

        static void PlanRestore(Context ctx, List<KeyValuePair<string, Dictionary<string, object>>> pending)
        {
            var plan = new Plan();
            try
            {
                foreach (var pair in pending)
                {
                    if (!SameGame(pair.Value, ctx.Game)) continue;
                    foreach (var row in Util.Items(pair.Value["operations"]).Cast<Dictionary<string, object>>())
                    {
                        string relative = Util.Str(row, "relative");
                        if (relative == null || !RelativeName.IsMatch(relative)) continue;
                        if (Util.Str(row, "before_sha256") == Util.Str(row, "candidate_sha256")) continue;
                        long current = Plan.Size(Path.Combine(ctx.Cache, relative.Substring("packages/".Length).Replace('/', '\\')));
                        if (current >= 0) plan.Add(current, 1);
                        // Прежний файл: хеш копии, чтение при копировании, хеш подготовленного файла.
                        long backup = Plan.Size(Path.Combine(pair.Key, relative.Replace('/', '\\')));
                        if (backup >= 0 && Convert.ToBoolean(row["before_exists"])) plan.Add(backup, 3);
                    }
                }
            }
            catch (Exception) { }
            plan.Apply();
        }

        static void Check(Context ctx, string explicitGame, bool useMemo)
        {
            Configure(ctx, explicitGame);
            if (Progress.Active) { var plan = new Plan(); PlanPayload(ctx, plan); if (!TransplantMode(ctx)) PlanPreflight(ctx, plan); plan.Apply(); }
            CheckPayload(ctx);
            RequireNoPending(ctx);
            string state = PreflightAll(ctx, useMemo);
            ctx.Result["game"] = ctx.Game;
            ctx.Result["game_version"] = ctx.M.GameVersion;
            ctx.Result["state"] = state;
            Log.Write("проверка пройдена, состояние override: " + state);
        }

        // Все проверки, ничего не пишущие в игру. Порядок: версия (дёшево) -> prime -> индекс/SDK -> процессы -> override.
        static string PreflightAll(Context ctx, bool useMemo)
        {
            if (TransplantMode(ctx))
            {
                // Пересадка: SHA prime, индекс, QtsVFS.dll и .info не проверяются (игра новее сборки); жёстко проверяются скелет, журнал и QtskDB.dll.
                CheckSdkFile(ctx);
                Closer.Run(ctx, CloseMode);
                RequireClosed(ctx);
                string transplantState;
                PlanTransplant(ctx, out transplantState);
                return transplantState;
            }
            foreach (var p in ctx.M.Pkgs)
            {
                string info = Path.Combine(ctx.Cache, p.Id, p.Id + ".info");
                if (File.Exists(info))
                {
                    string version = ReadVersion(info);
                    if (version != null && version != ctx.M.GameVersion)
                        throw new StopException("version", "Версия игры (" + version + ") не совпадает с версией, для которой собран перевод (" + ctx.M.GameVersion + "). Ничего не изменено. " + RefreshHint);
                }
            }
            Closer.Run(ctx, CloseMode);   // версия сошлась, дальше читаются файлы игры: мешающие процессы закрываются здесь
            VerifyPrime(ctx, useMemo);
            CheckIndexAndSdk(ctx);
            CheckMountLog(ctx);
            RequireClosed(ctx);
            string state;
            PlanOverride(ctx, out state);
            return state;
        }

        // ---- пересадка записей: игра новее сборки ---------------------------------------------------------
        // Быстрый путь (версия в <N>.info равна версии сборки) выше не менялся. Здесь версия не совпала или привязка потеряна: заголовок пакета берётся из
        // скелета <N>.db, который игра пересоздала после обновления, а записи сборки вставляются в его копию только для тех ресурсов, чей оригинал
        // в prime игры совпал с оригиналом, из которого собран перевод. Остальное остаётся как в игре (китайским).

        const string RefreshHint = "Обновите игру, запустите её один раз до меню, закройте и повторите установку; новое останется китайским.";

        static bool TransplantMode(Context ctx)
        {
            foreach (var p in ctx.M.Pkgs)
            {
                string info = Path.Combine(ctx.Cache, p.Id, p.Id + ".info");
                if (!File.Exists(info)) return true;
                string version = ReadVersion(info);
                if (version == null || version != ctx.M.GameVersion || version != p.InfoVersion) return true;
            }
            return false;
        }

        static bool IsTransplantReceipt(Dictionary<string, object> receipt)
        {
            object value;
            return receipt.TryGetValue("transplant", out value) && value is bool && (bool)value;
        }

        // pkgHash / basePkgHash заголовка: два u32 LE (старшая, затем младшая часть), как в pilot_contract.header_hashes.
        static string HeaderHash(byte[] header, int offset)
        {
            uint high = BitConverter.ToUInt32(header, offset), low = BitConverter.ToUInt32(header, offset + 4);
            return ((((ulong)high) << 32) | low).ToString(CultureInfo.InvariantCulture);
        }

        // Хеш prime-пакета из последней строки «MountPackage N, prime» самого свежего журнала QtsVFS*.log; null, если строки нет или pkgHash != basePkgHash.
        static string PrimeHashFromLog(Context ctx, string id, out DateTime logTime)
        {
            logTime = DateTime.MinValue;
            string dir = Path.Combine(ctx.Game, "QtsDataFolder", "QtsVFSCache");
            var files = new List<FileInfo>();
            foreach (string name in new[] { "QtsVFS.log", "QtsVFS1.log", "QtsVFS2.log", "QtsVFS3.log" })
            {
                string path = Path.Combine(dir, name);
                if (File.Exists(path)) files.Add(new FileInfo(path));
            }
            foreach (var file in files.OrderByDescending(f => f.LastWriteTimeUtc))
            {
                string text;
                try { using (var s = new FileStream(file.FullName, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete)) using (var r = new StreamReader(s, Encoding.UTF8)) text = r.ReadToEnd(); }
                catch (IOException) { continue; }
                Match last = null;
                foreach (Match m in MountLine.Matches(text)) if (m.Groups[1].Value == id) last = m;
                if (last == null) continue;
                logTime = file.LastWriteTimeUtc;
                return last.Groups[2].Value == last.Groups[3].Value ? last.Groups[2].Value : null;
            }
            return null;
        }

        static string SdkSource(Context ctx)
        {
            return Path.Combine(ctx.Game, "WXQ_Data", "Plugins", "x86_64", "QtskDB.dll");
        }

        // Без загрузки: файл есть, не ссылка, не больше 64 МиБ, заголовок MZ / PE\0\0, машина x64. Хеш в списке install.json — только пометка в журнале.
        static void RequirePe(string path)
        {
            const string bad = "QtskDB.dll игры не похожа на 64-разрядную библиотеку Windows. Перевод не установлен, ничего не изменено.";
            try
            {
                var info = new FileInfo(path);
                if (!info.Exists || info.Length < 1024 || info.Length > 64L * 1024 * 1024) throw new StopException("sdk", bad);
                using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                {
                    var head = new byte[0x40];
                    if (stream.Read(head, 0, head.Length) != head.Length || head[0] != (byte)'M' || head[1] != (byte)'Z') throw new StopException("sdk", bad);
                    int pe = BitConverter.ToInt32(head, 0x3C);
                    if (pe < 0x40 || pe > (1 << 20)) throw new StopException("sdk", bad);
                    stream.Position = pe;
                    var nt = new byte[6];
                    if (stream.Read(nt, 0, nt.Length) != nt.Length || nt[0] != (byte)'P' || nt[1] != (byte)'E' || nt[2] != 0 || nt[3] != 0 || BitConverter.ToUInt16(nt, 4) != 0x8664)
                        throw new StopException("sdk", bad);
                }
            }
            catch (IOException) { throw new StopException("sdk", bad); }
            catch (UnauthorizedAccessException) { throw new StopException("sdk", bad); }
        }

        static void CheckSdkFile(Context ctx)
        {
            string dll = SdkSource(ctx);
            RequireNoLinks(ctx, dll);
            RequirePe(dll);
        }

        // Копия QtskDB.dll в защищённую папку данных; SHA копии равен SHA источника, в списке install.json он только помечается в журнале. Возвращает путь копии.
        static string StageSdk(Context ctx)
        {
            string work = Path.Combine(ctx.Data, "work"), folder = Path.Combine(work, "sdk");
            Directory.CreateDirectory(folder);
            if (Util.IsReparse(work) || Util.IsReparse(folder))
                throw new StopException("acl", "Папка данных русификатора содержит ссылку (" + folder + "), установщик такой папке не доверяет.");
            string copy = Path.Combine(folder, "QtskDB.dll");
            Util.RequireNotLink(copy);
            if (File.Exists(copy)) File.Delete(copy);
            string source = SdkSource(ctx);
            RequirePe(source);
            string sourceSha = Util.Sha(source);
            Util.Copy(source, copy, true);
            Util.FlushToDisk(copy);
            string copySha = Util.Sha(copy);
            if (copySha != sourceSha || Util.Sha(source) != sourceSha)
            {
                try { File.Delete(copy); } catch (IOException) { }
                throw new StopException("sdk", "QtskDB.dll игры изменилась во время копирования. Перевод не установлен, ничего не изменено; повторите установку.");
            }
            RequirePe(copy);
            Log.Write("QtskDB.dll " + copySha + ", " + (ctx.M.SdkKnown.Contains(copySha) ? "известная" : "неизвестная") + " установщику (список только для журнала); результат сборки проверяется независимо");
            return copy;
        }

        class TransplantItem { public Pkg Package; public string Catalog, Storage, CatalogSha; public byte[] Header; }

        // Скелет: ровно заголовок QTSF_PACKAGE (120 байт), без файла данных, pkgHash == basePkgHash == хеш prime из журнала игры.
        static byte[] ReadSkeleton(Context ctx, Pkg p, string catalog, string storage)
        {
            byte[] header = CacheHeader(ctx, p.Id, catalog, null);
            // Старый перевод мог быть записан позже журнала; актуальность подтверждается датами prime, а не датой overlay.
            bool clean = ForeignReason(catalog, storage, null) == null && !CacheStorages(ctx, p.Id).Any();
            string logged = RequireLogHash(ctx, p, clean ? catalog : null);
            if (HeaderHash(header, 80) != logged || HeaderHash(header, 88) != logged)
                throw new StopException("cache_missing", "Заголовок кэша пакета " + p.Id + " не соответствует установленной версии игры (pkgHash не совпал с журналом). " + RefreshHint);
            return header;
        }

        // pkgHash prime из журнала QtsVFS. Журнал обязан быть не старше файлов prime пакета (и файла newerThan, если задан): иначе он описывает прежнюю версию игры.
        // В заголовке prime-пакета <N>.db хеша нет (game_inspect.QtsContainer его не читает), поэтому единственная независимая сверка — время записи.
        static string RequireLogHash(Context ctx, Pkg p, string newerThan)
        {
            DateTime logTime;
            string logged = PrimeHashFromLog(ctx, p.Id, out logTime);
            if (logged == null)
                throw new StopException("cache_missing", "В журнале игры QtsVFS нет строки «MountPackage " + p.Id + ", prime» с одинаковыми pkgHash и basePkgHash. " + RefreshHint);
            DateTime newest = DateTime.MinValue;
            string primeDir = Path.Combine(ctx.Game, "WXQ_Data", "Package", "builtin", p.Id);
            if (Directory.Exists(primeDir))
                foreach (string file in Directory.GetFiles(primeDir, "*.db"))
                {
                    DateTime t = File.GetLastWriteTimeUtc(file);
                    if (t > newest) newest = t;
                }
            if (newerThan != null && File.Exists(newerThan))
            {
                DateTime t = File.GetLastWriteTimeUtc(newerThan);
                if (t > newest) newest = t;
            }
            if (logTime < newest)
                throw new StopException("cache_missing", "Журнал игры QtsVFS старше файлов пакета " + p.Id + ": он мог остаться от прежней версии игры. " + RefreshHint);
            return logged;
        }

        static Dictionary<string, object> FindAppliedTransplant(Context ctx)
        {
            foreach (var pair in ListReceipts(ctx))
            {
                if (!SameGame(pair.Value, ctx.Game) || Util.Str(pair.Value, "status") != "applied") continue;
                return IsTransplantReceipt(pair.Value) ? pair.Value : null;
            }
            return null;
        }

        // Файлы пакета в игре сейчас ровно такие, какими их записала наша пересадка (SHA совпали с candidate_sha256 receipt).
        // Если SHA совпали, заголовок файла дополнительно должен нести pkgHash из журнала игры; иначе игра обновилась поверх нашей пересадки — стоп reinstall.
        static bool ReceiptOwns(Context ctx, Dictionary<string, object> receipt, Pkg p)
        {
            string id = p.Id;
            string prefix = "packages/" + id + "/";
            int count = 0;
            foreach (var row in Util.Items(receipt["operations"]).Cast<Dictionary<string, object>>())
            {
                string relative = Util.Str(row, "relative");
                if (relative == null || !relative.StartsWith(prefix, StringComparison.Ordinal)) continue;
                string target = TargetPath(ctx, relative);
                if ((File.Exists(target) ? Util.Sha(target) : null) != Util.Str(row, "candidate_sha256")) return false;
                count++;
            }
            if (count == 0) return false;
            var expectedStorages = new HashSet<string>(Util.Items(receipt["operations"]).Cast<Dictionary<string, object>>()
                .Where(r => (Util.Str(r, "relative") ?? "").StartsWith(prefix, StringComparison.Ordinal) && Util.Str(r, "candidate_sha256") != null)
                .Select(r => Util.Str(r, "relative")), StringComparer.OrdinalIgnoreCase);
            foreach (string file in CacheStorages(ctx, id))
                if (!expectedStorages.Contains(prefix + Path.GetFileName(file))) return false;
            string reinstall = "Перевод установлен пересадкой, но версия игры с тех пор изменилась (заголовок пакета " + id + " не соответствует журналу игры). Нажмите «Удалить перевод» и повторите установку. Ничего не изменено.";
            byte[] header;
            try { header = Catalog.Parse(File.ReadAllBytes(TargetPath(ctx, prefix + id + ".db"))).RawValues(HeaderId)[0]; }
            catch (FormatException) { throw new StopException("reinstall", reinstall); }
            string logged = RequireLogHash(ctx, p, null);
            if (HeaderHash(header, 80) != logged || HeaderHash(header, 88) != logged) throw new StopException("reinstall", reinstall);
            return true;
        }

        static List<TransplantItem> PlanTransplant(Context ctx, out string state)
        {
            var cleanup = new List<Op>();
            PlanOtherPackages(ctx, cleanup);
            var applied = FindAppliedTransplant(ctx);
            var items = new List<TransplantItem>();
            bool same = applied != null && Util.Str(applied, "version") == ctx.M.Version && Util.Str(applied, "built") == ctx.M.Built && cleanup.Count == 0;
            var skippedBefore = new HashSet<string>(applied == null ? new string[0] : Util.Items(applied.ContainsKey("skipped_packages") ? applied["skipped_packages"] : new List<object>()).Select(x => Convert.ToString(x)));
            foreach (var p in ctx.M.Pkgs)
            {
                string dir = Path.Combine(ctx.Cache, p.Id);
                string catalog = Path.Combine(dir, p.Id + ".db"), storage = Path.Combine(dir, p.Id + "_0.db"), info = Path.Combine(dir, p.Id + ".info");
                if (!File.Exists(info) || !File.Exists(catalog))
                    throw new StopException("cache_missing", "Игра ещё не создала кэш ресурсов для пакета " + p.Id + ". " + RefreshHint);
                RequireNoLinks(ctx, catalog);
                bool owned = applied != null && ReceiptOwns(ctx, applied, p);
                bool cleanSkipped = skippedBefore.Contains(p.Id) && ForeignReason(catalog, storage, null) == null && !CacheStorages(ctx, p.Id).Any();
                if (!owned && !cleanSkipped) same = false;
                items.Add(new TransplantItem { Package = p, Catalog = catalog, Storage = storage,
                    Header = owned ? CacheHeader(ctx, p.Id, catalog, null) : ReadSkeleton(ctx, p, catalog, storage), CatalogSha = Util.Sha(catalog) });
            }
            state = same ? "same" : "fresh";
            return same ? new List<TransplantItem>() : items;
        }

        class BuildResult
        {
            public List<string> Applied = new List<string>(), Skipped = new List<string>();
            public string CatalogSha, StorageSha;
        }

        static string Sha256Of(byte[] data)
        {
            using (var hash = new SHA256CryptoServiceProvider()) return Util.Hex(hash.ComputeHash(data));
        }

        // SHA декодированных оригиналов из prime игры (WXQ_Data\Package\builtin\<N>\<N>_k.db) для нужных id. Файлы prime только читаются.
        static Dictionary<string, string> PrimeShas(Context ctx, Pkg p, HashSet<string> wanted, Dictionary<string, string> reasons, List<string> stamps)
        {
            string dir = Path.Combine(ctx.Game, "WXQ_Data", "Package", "builtin", p.Id);
            var result = new Dictionary<string, string>();
            for (int k = 0; ; k++)
            {
                string path = Path.Combine(dir, p.Id + "_" + k.ToString(CultureInfo.InvariantCulture) + ".db");
                if (!File.Exists(path))
                {
                    if (k == 0) throw new StopException("version", "Не найден файл игры " + p.Id + "\\" + p.Id + "_0.db (prime). Ничего не изменено.");
                    break;
                }
                RequireNoLinks(ctx, path);
                var fileInfo = new FileInfo(path);
                stamps.Add(path + "|" + fileInfo.Length + "|" + fileInfo.LastWriteTimeUtc.Ticks);
                Catalog chunks;
                try { chunks = Catalog.ParseChunks(File.ReadAllBytes(path)); }
                catch (FormatException error) { throw new StopException("version", "Файл игры " + p.Id + "_" + k + ".db не удалось разобрать (" + error.Message + "): формат изменился. Ничего не изменено."); }
                foreach (string id in wanted)
                {
                    if (result.ContainsKey(id)) continue;
                    ulong number = Convert.ToUInt64(id, 16);
                    if (!chunks.Chunks.ContainsKey(number)) continue;
                    try { result[id] = Sha256Of(chunks.DecodeEntry(number)); }
                    catch (FormatException error) { reasons[id] = "оригинал не прочитан (" + error.Message + ")"; }
                }
            }
            return result;
        }

        static StopException BadBuild(string id, string reason)
        {
            return new StopException("transplant", "Собранный пакет " + id + " не прошёл проверку (" + reason + "). В игру ничего не записано.");
        }

        // Собирает <N>.db и <N>_0.db в builtDir. Applied пуст — у пакета нет ни одного подходящего ресурса (файлы не создаются).
        static BuildResult BuildTransplant(Context ctx, TransplantItem item, string builtDir, string sdkCopy)
        {
            Pkg p = item.Package;
            var result = new BuildResult();
            if (p.Resources.Count == 0)
                throw new StopException("payload", "В данных установщика нет списка ресурсов пакета " + p.Id + " (install.json старого формата): перенос на другую версию игры невозможен. Скачайте установщик заново.");
            byte[] ignored;
            List<OverrideResource> payload;
            try { payload = OverridePackage.Read(Path.Combine(ctx.Payload, p.File(p.Id + ".db").PayloadName), Path.Combine(ctx.Payload, p.File(p.Id + "_0.db").PayloadName), out ignored); }
            catch (FormatException error) { throw new StopException("payload", "Файлы перевода пакета " + p.Id + " в установщике не разобрались (" + error.Message + "). Скачайте установщик заново."); }
            var byId = payload.ToDictionary(r => r.Id.ToString("x16", CultureInfo.InvariantCulture));
            var wanted = new HashSet<string>();
            foreach (var row in p.Resources)
            {
                OverrideResource r;
                if (!byId.TryGetValue(row.IdHex, out r) || !string.Equals(r.Sha, row.Sha, StringComparison.OrdinalIgnoreCase))
                    throw new StopException("payload", "Ресурс " + row.IdHex + " пакета " + p.Id + " в установщике не совпал с install.json. Скачайте установщик заново.");
                wanted.Add(row.IdHex);
            }
            var reasons = new Dictionary<string, string>();
            var stamps = new List<string>();
            var prime = PrimeShas(ctx, p, wanted, reasons, stamps);
            var matched = new List<OverrideResource>();
            foreach (var row in p.Resources)
            {
                string sha;
                if (!prime.TryGetValue(row.IdHex, out sha))
                {
                    string reason;
                    result.Skipped.Add(row.IdHex + ": " + (reasons.TryGetValue(row.IdHex, out reason) ? reason : "нет в оригинальных ресурсах этой версии игры"));
                }
                else if (!string.Equals(sha, row.OriginalSha, StringComparison.OrdinalIgnoreCase))
                    result.Skipped.Add(row.IdHex + ": оригинал в игре изменился");
                else
                {
                    result.Applied.Add(row.IdHex);
                    matched.Add(byId[row.IdHex]);
                }
            }
            Log.Write("пакет " + p.Id + ": подошло ресурсов " + result.Applied.Count + ", пропущено " + result.Skipped.Count);
            foreach (string line in result.Skipped.Take(50)) Log.Write("пакет " + p.Id + " пропущен ресурс " + line);
            if (matched.Count == 0) return result;

            // Сборка в защищённой папке копии над копией скелета; результат дальше проверяется независимо (заголовок == скелет, набор записей,
            // SHA нагрузок заново из собранных файлов), любое исключение разбора — stop transplant.
            Directory.CreateDirectory(builtDir);
            string dbPath = Path.Combine(builtDir, p.Id + ".db"), storagePath = Path.Combine(builtDir, p.Id + "_0.db");
            Util.RequireNotLink(dbPath);
            Util.RequireNotLink(storagePath);
            File.WriteAllBytes(dbPath, Catalog.HeaderOnly(item.Header));
            Util.FlushToDisk(dbPath);
            if (Util.Sha(item.Catalog) != item.CatalogSha) throw BadBuild(p.Id, "кэш изменился во время подготовки");
            Sdk.Load(sdkCopy);
            try { Sdk.Insert(builtDir, p.Id, matched); }
            catch (IOException error) { throw new StopException("sdk", "Библиотека игры не смогла собрать пакет " + p.Id + " (" + error.Message + "). В игру ничего не записано."); }
            Util.FlushToDisk(dbPath);
            Util.FlushToDisk(storagePath);
#if TESTBUILD
            if (Environment.GetEnvironmentVariable("WXQ_TEST_TAMPER") == "1")
            {
                using (var tamper = new FileStream(storagePath, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
                {
                    tamper.Position = tamper.Length / 2;
                    int one = tamper.ReadByte();
                    tamper.Position = tamper.Length / 2;
                    tamper.WriteByte((byte)(one ^ 0x5A));
                }
            }
#endif

            byte[] header;
            List<OverrideResource> back;
            try { back = OverridePackage.Read(dbPath, storagePath, out header); }
            catch (StopException) { throw; }
            catch (Exception error) { throw BadBuild(p.Id, "повторный разбор: " + error.Message); }
            if (!header.SequenceEqual(item.Header)) throw BadBuild(p.Id, "заголовок не равен скелету");
            var expected = matched.ToDictionary(r => r.Id);
            if (back.Count != expected.Count) throw BadBuild(p.Id, "набор записей не равен заголовку и подошедшим ресурсам");
            foreach (var r in back)
            {
                OverrideResource e;
                if (!expected.TryGetValue(r.Id, out e)) throw BadBuild(p.Id, "лишняя запись " + r.Id.ToString("x16", CultureInfo.InvariantCulture));
                if (r.Sha != e.Sha || !r.Meta.SequenceEqual(e.Meta) || r.ChunkData.Count != e.ChunkData.Count)
                    throw BadBuild(p.Id, "запись " + r.Id.ToString("x16", CultureInfo.InvariantCulture) + " не совпала с исходной");
                for (int i = 0; i < r.ChunkData.Count; i++)
                    if (!r.ChunkData[i].SequenceEqual(e.ChunkData[i]) || !r.ChunkInfo[i].SequenceEqual(e.ChunkInfo[i]))
                        throw BadBuild(p.Id, "чанки записи " + r.Id.ToString("x16", CultureInfo.InvariantCulture) + " не совпали с исходными");
            }
            foreach (string stamp in stamps)
            {
                string[] parts = stamp.Split('|');
                var now = new FileInfo(parts[0]);
                if (!now.Exists || now.Length.ToString(CultureInfo.InvariantCulture) != parts[1] || now.LastWriteTimeUtc.Ticks.ToString(CultureInfo.InvariantCulture) != parts[2])
                    throw BadBuild(p.Id, "файл prime игры изменился во время сборки");
            }
            result.CatalogSha = Util.Sha(dbPath);
            result.StorageSha = Util.Sha(storagePath);
            return result;
        }

        static Dictionary<string, object> TransplantRow(string relative, string target, bool beforeExists, string beforeSha, string candidate)
        {
            return new Dictionary<string, object>
            {
                { "relative", relative }, { "target", target }, { "before_exists", beforeExists },
                { "before_sha256", beforeSha }, { "candidate_sha256", candidate }, { "unchanged", false }
            };
        }

        static void BackupCacheOp(Context ctx, string labelDir, Op op)
        {
            RequireNoLinks(ctx, op.Target);
            if (!op.BeforeExists || op.Unchanged) return;
            string backup = Path.Combine(labelDir, op.Relative.Replace('/', '\\'));
            Directory.CreateDirectory(Path.GetDirectoryName(backup));
            Util.Copy(op.Target, backup, false);
            Util.FlushToDisk(backup);
            if (Util.Sha(backup) != op.BeforeSha || Util.Sha(op.Target) != op.BeforeSha)
                throw new StopException("backup", "Кэш изменился во время резервного копирования. Ничего не изменено, повторите установку.");
        }

        static void BackupTransplant(Context ctx)
        {
            string state;
            var items = PlanTransplant(ctx, out state);
            var cleanup = new List<Op>();
            PlanOtherPackages(ctx, cleanup);
            var needs = new SpaceNeeds();
            long dataNeed = 1048576, cacheNeed = 0;
            foreach (var item in items)
            {
                long a = item.Package.File(item.Package.Id + ".db").Bytes, b = item.Package.File(item.Package.Id + "_0.db").Bytes;
                dataNeed += 2 * Math.Max(0, Plan.Size(item.Catalog)) + a + b;   // копия скелета и собранные файлы (верхняя оценка)
                cacheNeed = Math.Max(cacheNeed, Math.Max(a, b));
                foreach (string storage in CacheStorages(ctx, item.Package.Id)) dataNeed += Math.Max(0, Plan.Size(storage));
            }
            foreach (var op in cleanup) { dataNeed += Math.Max(0, Plan.Size(op.Target)) + op.Row.Bytes; cacheNeed = Math.Max(cacheNeed, op.Row.Bytes); }
            needs.Add(ctx.Data, dataNeed);
            needs.Add(ctx.Cache, cacheNeed);
            needs.Require();
            string sdk = StageSdk(ctx);
            string label = DateTime.Now.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture);
            while (Directory.Exists(Path.Combine(ctx.BackupRoot, label))) label += "x";
            string labelDir = Path.Combine(ctx.BackupRoot, label);
            var rows = new List<object>();
            var applied = new Dictionary<string, object>();
            var skipped = new Dictionary<string, object>();
            var skippedPackages = new List<object>();
            bool receiptWritten = false;
            try
            {
                foreach (var item in items)
                {
                    string id = item.Package.Id;
                    var built = BuildTransplant(ctx, item, Path.Combine(labelDir, "built", id), sdk);
                    skipped[id] = built.Skipped.Cast<object>().ToList();
                    if (built.Applied.Count == 0)
                    {
                        skippedPackages.Add(id);
                        // Старый перевод в несовместимом пакете тоже снимается: останется проверенный оригинальный base header.
                        var reset = CacheOp(ctx, item.Package, id + ".db", Catalog.HeaderOnly(item.Header));
                        string clean = Path.Combine(labelDir, "built", id, id + ".db");
                        Directory.CreateDirectory(Path.GetDirectoryName(clean));
                        File.WriteAllBytes(clean, reset.SourceBytes);
                        Util.FlushToDisk(clean);
                        BackupCacheOp(ctx, labelDir, reset);
                        rows.Add(TransplantRow(reset.Relative, reset.Target, reset.BeforeExists, reset.BeforeSha, reset.Candidate));
                        foreach (string storage in CacheStorages(ctx, id))
                        {
                            var remove = CacheOp(ctx, item.Package, Path.GetFileName(storage), null);
                            BackupCacheOp(ctx, labelDir, remove);
                            rows.Add(TransplantRow(remove.Relative, remove.Target, remove.BeforeExists, remove.BeforeSha, null));
                        }
                        continue;
                    }
                    applied[id] = built.Applied.Cast<object>().ToList();
                    string catalogRelative = "packages/" + id + "/" + id + ".db", storageRelative = "packages/" + id + "/" + id + "_0.db";
                    string catalogTarget = TargetPath(ctx, catalogRelative), storageTarget = TargetPath(ctx, storageRelative);
                    string backup = Path.Combine(labelDir, catalogRelative.Replace('/', '\\'));
                    Directory.CreateDirectory(Path.GetDirectoryName(backup));
                    Util.Copy(catalogTarget, backup, false);
                    Util.FlushToDisk(backup);
                    if (Util.Sha(backup) != item.CatalogSha || Util.Sha(catalogTarget) != item.CatalogSha)
                        throw new StopException("backup", "Файл игры изменился во время создания резервной копии. Ничего не изменено, повторите установку.");
                    rows.Add(TransplantRow(catalogRelative, catalogTarget, true, item.CatalogSha, built.CatalogSha));
                    Op previousStorage = null;
                    foreach (string storage in CacheStorages(ctx, id))
                    {
                        string relative = "packages/" + id + "/" + Path.GetFileName(storage);
                        var op = CacheOp(ctx, item.Package, Path.GetFileName(storage), null);
                        BackupCacheOp(ctx, labelDir, op);
                        if (!string.Equals(relative, storageRelative, StringComparison.OrdinalIgnoreCase)) rows.Add(TransplantRow(relative, storage, true, op.BeforeSha, null));
                        else previousStorage = op;
                    }
                    rows.Add(TransplantRow(storageRelative, storageTarget, previousStorage != null, previousStorage == null ? null : previousStorage.BeforeSha, built.StorageSha));
                }
                if (applied.Count == 0)
                    throw new StopException("no_match", "В установленной версии игры нет ни одного ресурса, к которому подходит этот перевод (игра изменила все переводимые файлы). Ничего не изменено.");
                foreach (var op in cleanup)
                {
                    BackupCacheOp(ctx, labelDir, op);
                    if (!op.Remove)
                    {
                        string built = Path.Combine(labelDir, "built", op.Package.Id, op.Row.Name);
                        Directory.CreateDirectory(Path.GetDirectoryName(built));
                        File.WriteAllBytes(built, op.SourceBytes);
                        Util.FlushToDisk(built);
                    }
                    rows.Add(TransplantRow(op.Relative, op.Target, op.BeforeExists, op.BeforeSha, op.Candidate));
                }
                var receipt = new Dictionary<string, object>
                {
                    { "schema", 1 }, { "status", "prepared" }, { "label", label }, { "product", ctx.M.Product }, { "version", ctx.M.Version }, { "built", ctx.M.Built },
                    { "created_utc", DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture) }, { "game_root", ctx.Game }, { "cache_root", ctx.Cache },
                    { "game_version", ctx.M.GameVersion }, { "operations", rows },
                    { "transplant", true }, { "applied_resources", applied }, { "skipped_resources", skipped }, { "skipped_packages", skippedPackages }
                };
                WriteReceipt(ctx, label, receipt);
                receiptWritten = true;
            }
            catch (Exception error)
            {
                if (!receiptWritten)
                {
                    try { if (Directory.Exists(labelDir) && !File.Exists(Path.Combine(labelDir, "receipt.json")) && !Util.IsReparse(labelDir)) Directory.Delete(labelDir, true); }
                    catch (Exception) { Log.Write("не удалось убрать неполную папку копии " + labelDir); }
                }
                if (Util.IsDiskFull(error))
                    throw new StopException("disk_space", "Не хватает места на диске для сборки пакетов. Освободите место и повторите. В игру ничего не записано.");
                throw;
            }
            int appliedCount = applied.Values.Sum(v => ((List<object>)v).Count), skippedCount = skipped.Values.Sum(v => ((List<object>)v).Count);
            ctx.Result["label"] = label;
            ctx.Result["transplant"] = "1";
            ctx.Result["files"] = rows.Count.ToString(CultureInfo.InvariantCulture);
            ctx.Result["backed_up"] = rows.Count(r => (bool)((Dictionary<string, object>)r)["before_exists"]).ToString(CultureInfo.InvariantCulture);
            ctx.Result["applied_resources"] = appliedCount.ToString(CultureInfo.InvariantCulture);
            ctx.Result["skipped_resources"] = skippedCount.ToString(CultureInfo.InvariantCulture);
            Log.Write("пересадка " + label + ": ресурсов перенесено " + appliedCount + ", пропущено " + skippedCount + ", пакетов без совпадений " + skippedPackages.Count);
        }

        // Операции замены из receipt пересадки: источник — собранный файл в папке копии.
        static List<Op> TransplantOps(Context ctx, Dictionary<string, object> receipt, string label)
        {
            var ops = new List<Op>();
            foreach (var row in Util.Items(receipt["operations"]).Cast<Dictionary<string, object>>())
            {
                string relative = Util.Str(row, "relative");
                var match = relative == null ? null : RelativeName.Match(relative);
                if (match == null || !match.Success || relative.EndsWith(".info", StringComparison.Ordinal))
                    throw new StopException("restore", "В резервной копии недопустимое имя файла: " + relative);
                string id = match.Groups[1].Value;
                var pkg = ctx.M.Pkgs.FirstOrDefault(x => x.Id == id) ?? new Pkg { Id = id };
                string name = relative.Substring(relative.LastIndexOf('/') + 1);
                string source = Path.Combine(ctx.BackupRoot, Util.Str(receipt, "label") ?? "", "built", id, name);
                bool remove = Util.Str(row, "candidate_sha256") == null;
                if (!remove && (!File.Exists(source) || Util.IsReparse(source)))
                    throw new StopException("restore", "Собранный файл " + relative + " не найден в резервной копии. Повторите установку.");
                var op = new Op { Package = pkg, Row = new FileRow { Name = name, Sha = Util.Str(row, "candidate_sha256"), Bytes = remove ? 0 : new FileInfo(source).Length }, Relative = relative, Target = TargetPath(ctx, relative), Source = source };
                op.BeforeExists = File.Exists(op.Target);
                op.BeforeSha = op.BeforeExists ? Util.Sha(op.Target) : null;
                ops.Add(op);
            }
            return ops;
        }

        static void CheckTransplantShape(Context ctx, Dictionary<string, object> receipt, bool installed)
        {
            var rows = Util.Items(receipt["operations"]).Cast<Dictionary<string, object>>().ToDictionary(r => Util.Str(r, "relative"), StringComparer.OrdinalIgnoreCase);
            var ids = new HashSet<string>(ctx.M.Pkgs.Select(p => p.Id));
            foreach (string relative in rows.Keys) ids.Add(RelativeName.Match(relative).Groups[1].Value);
            foreach (string id in ids)
                foreach (string file in CacheStorages(ctx, id))
                {
                    string relative = "packages/" + id + "/" + Path.GetFileName(file);
                    if (!rows.ContainsKey(relative) || (installed && Util.Str(rows[relative], "candidate_sha256") == null))
                        throw new StopException(installed ? "verify" : "changed", "В кэше пакета " + id + " появился остаточный файл " + Path.GetFileName(file) + ". Повторите установку.");
                }
            var cleanup = new List<Op>();
            PlanOtherPackages(ctx, cleanup);
            if (cleanup.Any(o => installed || !rows.ContainsKey(o.Relative)))
                throw new StopException(installed ? "verify" : "changed", "Состав соседних пакетов изменился после подготовки установки. Повторите установку.");
        }

        static void VerifyTransplant(Context ctx, string label)
        {
            Dictionary<string, object> receipt;
            if (!string.IsNullOrEmpty(label))
            {
                receipt = Util.ReadJson(ReceiptPath(ctx, label));
                if (!SameGame(receipt, ctx.Game)) throw new StopException("verify", "Резервная копия относится к другой папке игры.");
            }
            else receipt = FindAppliedTransplant(ctx);
            if (receipt == null || !IsTransplantReceipt(receipt))
                throw new StopException("verify", "Не найдена запись об установке пересадкой. Выполните удаление перевода и повторите.");
            CheckTransplantShape(ctx, receipt, true);
            foreach (var row in Util.Items(receipt["operations"]).Cast<Dictionary<string, object>>())
            {
                string target = TargetPath(ctx, Util.Str(row, "relative"));
                if ((File.Exists(target) ? Util.Sha(target) : null) != Util.Str(row, "candidate_sha256"))
                    throw new StopException("verify", "После установки файл " + Util.Str(row, "relative") + " не совпал с эталоном. Выполните удаление перевода и повторите.");
            }
            var appliedAll = (Dictionary<string, object>)receipt["applied_resources"];
            foreach (var pair in appliedAll)
            {
                string meta = TargetPath(ctx, "packages/" + pair.Key + "/" + pair.Key + ".db"), data = TargetPath(ctx, "packages/" + pair.Key + "/" + pair.Key + "_0.db");
                try
                {
                    byte[] header;
                    var resources = OverridePackage.Read(meta, data, out header);
                    var have = new HashSet<string>(resources.Select(r => r.Id.ToString("x16", CultureInfo.InvariantCulture)));
                    var want = new HashSet<string>(Util.Items(pair.Value).Select(x => Convert.ToString(x)));
                    if (!have.SetEquals(want)) throw new StopException("verify", "Набор записей пакета " + pair.Key + " в игре не совпал с установленным. Выполните удаление перевода и повторите.");
                }
                catch (FormatException)
                {
                    throw new StopException("verify", "Пакет " + pair.Key + " в игре не разбирается после установки. Выполните удаление перевода и повторите.");
                }
            }
            // game_version — фактическая версия игры из <N>.info (первый пакет сборки), а не версия, под которую собран перевод.
            string actual = null;
            foreach (var p in ctx.M.Pkgs)
            {
                string info = Path.Combine(ctx.Cache, p.Id, p.Id + ".info");
                if (File.Exists(info)) { actual = ReadVersion(info); break; }
            }
            var stateFile = new Dictionary<string, object>
            {
                { "installed", true }, { "version", ctx.M.Version }, { "built", ctx.M.Built }, { "game_version", actual ?? ctx.M.GameVersion }, { "build_game_version", ctx.M.GameVersion },
                { "game_root", ctx.Game }, { "label", label ?? "" }, { "installed_utc", DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture) },
                { "packages", appliedAll.Keys.Select(k => (object)k).ToList() }, { "candidates", ctx.M.Candidates.Cast<object>().ToList() }, { "transplant", true }
            };
            Util.WriteJson(ctx.StateFile, stateFile);
            try { File.Delete(ctx.MemoFile); } catch (IOException) { }
            ctx.Result["game"] = ctx.Game;
            ctx.Result["transplant"] = "1";
            Log.Write("итоговая проверка пересадки пройдена");
        }

        // ---- резервная копия, замена, проверка -------------------------------------------

        static readonly Regex LabelFormat = new Regex(@"^[0-9]{8}-[0-9]{6}x*$");

        // Место до первой записи. Том данных: копии прежних файлов (при backup) и запас под receipt и журнал. Том игры: самый большой подготовленный файл
        // рядом с целью (stage; после замены он исчезает, поэтому берётся максимум, а не сумма) и такой же временный файл отката.
        static void RequireSpace(Context ctx, List<Op> ops, bool withBackupCopies)
        {
            var needs = new SpaceNeeds();
            long copies = 1048576;   // receipt, state и журнал
            long biggest = 0;
            foreach (var op in ops)
            {
                if (op.Unchanged) continue;
                long old = Plan.Size(op.Target);
                if (withBackupCopies && op.BeforeExists && old > 0) copies += old;
                biggest = Math.Max(biggest, Math.Max(op.Row.Bytes, Math.Max(old, 0)));
            }
            needs.Add(ctx.Data, copies);
            needs.Add(ctx.Cache, biggest);
            needs.Require();
        }

        static string ReceiptPath(Context ctx, string label)
        {
            if (label == null || !LabelFormat.IsMatch(label)) throw new StopException("internal", "Недопустимая метка резервной копии: " + label);
            return Path.Combine(ctx.BackupRoot, label, "receipt.json");
        }

        static void WriteReceipt(Context ctx, string label, Dictionary<string, object> receipt)
        {
            Util.WriteJson(ReceiptPath(ctx, label), receipt);
        }

        static void Backup(Context ctx, string explicitGame)
        {
            Configure(ctx, explicitGame);
            if (Progress.Active && TransplantMode(ctx)) { var tplan = new Plan(); PlanPayload(ctx, tplan); tplan.Apply(); }
            else if (Progress.Active)
            {
                var plan = new Plan();
                PlanPayload(ctx, plan); PlanPreflight(ctx, plan); PlanOverrideHashes(ctx, plan);
                // Копия прежнего файла: чтение при копировании, хеш копии и хеш исходного файла.
                foreach (var p in ctx.M.Pkgs)
                    foreach (var f in p.Files)
                    {
                        long old = Plan.Size(Path.Combine(ctx.Cache, p.Id, f.Name));
                        if (old >= 0 && old != f.Bytes) plan.Add(old, 3);
                    }
                plan.Apply();
            }
            CheckPayload(ctx);
            RequireNoPending(ctx);
            string state = PreflightAll(ctx, true);
            ctx.Result["state"] = state;
            if (state == "same")
            {
                ctx.Result["label"] = "";
                Log.Write("перевод уже установлен и совпадает с этим установщиком; резервная копия не нужна");
                return;
            }
            if (TransplantMode(ctx)) { BackupTransplant(ctx); return; }
            string state2;
            var ops = PlanOverride(ctx, out state2);
            RequireSpace(ctx, ops, true);
            string label = DateTime.Now.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture);
            while (Directory.Exists(Path.Combine(ctx.BackupRoot, label))) label += "x";
            var rows = new List<object>();
            string labelDir = Path.Combine(ctx.BackupRoot, label);
            bool receiptWritten = false;
            try
            {
                foreach (var op in ops)
                {
                    if (op.BeforeExists && !op.Unchanged)
                    {
                        string backup = Path.Combine(ctx.BackupRoot, label, op.Relative.Replace('/', '\\'));
                        Directory.CreateDirectory(Path.GetDirectoryName(backup));
                        Util.Copy(op.Target, backup, false);
                        Util.FlushToDisk(backup);
                        if (Util.Sha(backup) != op.BeforeSha || Util.Sha(op.Target) != op.BeforeSha)
                            throw new StopException("backup", "Файл игры изменился во время создания резервной копии. Ничего не изменено, повторите установку.");
                    }
                    rows.Add(new Dictionary<string, object>
                    {
                        { "relative", op.Relative }, { "target", op.Target }, { "before_exists", op.BeforeExists },
                        { "before_sha256", op.BeforeSha }, { "candidate_sha256", op.Candidate }, { "unchanged", op.Unchanged }
                    });
                }
                var receipt = new Dictionary<string, object>
                {
                    { "schema", 1 }, { "status", "prepared" }, { "label", label }, { "product", ctx.M.Product }, { "version", ctx.M.Version },
                    { "created_utc", DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture) }, { "game_root", ctx.Game }, { "cache_root", ctx.Cache },
                    { "game_version", ctx.M.GameVersion }, { "operations", rows }
                };
                WriteReceipt(ctx, label, receipt);
                receiptWritten = true;
            }
            catch (Exception error)
            {
                // Частичная папка копии без receipt при сбое удаляется: откат по ней невозможен, а оставшиеся файлы только занимают диск.
                if (!receiptWritten)
                {
                    try { if (Directory.Exists(labelDir) && !File.Exists(Path.Combine(labelDir, "receipt.json")) && !Util.IsReparse(labelDir)) Directory.Delete(labelDir, true); }
                    catch (Exception) { Log.Write("не удалось убрать неполную папку копии " + labelDir); }
                }
                if (Util.IsDiskFull(error))
                    throw new StopException("disk_space", "Не хватает места на диске для резервной копии. Освободите место и повторите. В игру ничего не записано.");
                throw;
            }
            ctx.Result["label"] = label;
            ctx.Result["files"] = rows.Count(r => !(bool)((Dictionary<string, object>)r)["unchanged"]).ToString(CultureInfo.InvariantCulture);
            ctx.Result["backed_up"] = ops.Count(o => o.BeforeExists && !o.Unchanged).ToString(CultureInfo.InvariantCulture);
            Log.Write("резервная копия " + label + ": файлов к замене " + ctx.Result["files"] + ", сохранено прежних " + ctx.Result["backed_up"]);
        }

        static void Unpack(Context ctx, string explicitGame, string label)
        {
            if (string.IsNullOrEmpty(label)) { Log.Write("распаковка не требуется"); return; }
            Configure(ctx, explicitGame);
            if (Progress.Active && TransplantMode(ctx)) { var tplan = new Plan(); PlanPayload(ctx, tplan); tplan.Apply(); }
            else if (Progress.Active)
            {
                var plan = new Plan();
                PlanPayload(ctx, plan); PlanPreflight(ctx, plan); PlanOverrideHashes(ctx, plan);
                // Замена: копирование из установщика, хеш подготовленного файла, хеш прежнего файла перед заменой, хеш записанного.
                foreach (var p in ctx.M.Pkgs)
                    foreach (var f in p.Files)
                    {
                        long old = Plan.Size(Path.Combine(ctx.Cache, p.Id, f.Name));
                        if (old >= 0 && old == f.Bytes) continue;
                        plan.Add(f.Bytes, 3);
                        if (old >= 0) plan.Add(old, 1);
                    }
                plan.Apply();
            }
            CheckPayload(ctx);
            string path = ReceiptPath(ctx, label);
            var receipt = Util.ReadJson(path);
            // Receipt обязан относиться к найденной игре: по чужим путям из receipt ничего не пишем.
            string receiptCache = Util.Str(receipt, "cache_root");
            if (!SameGame(receipt, ctx.Game) || receiptCache == null || !Util.SamePath(receiptCache, ctx.Cache))
                throw new StopException("restore", "Резервная копия относится к другой папке игры (" + Util.Str(receipt, "game_root") + "). Ничего не записано, повторите установку.");
            string state0 = PreflightAll(ctx, true);
            string state;
            bool transplant = IsTransplantReceipt(receipt);
            if (transplant != TransplantMode(ctx)) throw new StopException("changed", "Версия игры изменилась после создания резервной копии. Ничего не заменено, повторите установку.");
            if (transplant) CheckTransplantShape(ctx, receipt, false);
            var ops = transplant ? TransplantOps(ctx, receipt, label) : PlanOverride(ctx, out state);
            var recorded = Util.Items(receipt["operations"]).Cast<Dictionary<string, object>>().ToDictionary(r => Util.Str(r, "relative"), StringComparer.OrdinalIgnoreCase);
            if (ops.Count != recorded.Count || ops.Any(o => !recorded.ContainsKey(o.Relative)))
                throw new StopException("changed", "Состав кэша изменился после резервного копирования. Ничего не заменено, повторите установку.");
            foreach (var op in ops)
            {
                var r = recorded[op.Relative];
                if ((Util.Str(r, "before_sha256") ?? "") != (op.BeforeSha ?? ""))
                    throw new StopException("changed", "Файлы игры изменились после создания резервной копии. Ничего не заменено, повторите установку.");
            }
            RequireSpace(ctx, ops, false);
            var ordered = ops.OrderBy(o => o.Relative.EndsWith(".info") ? 2 : (o.Relative.EndsWith("_0.db") ? 0 : 1)).ToList();
            // С этого момента в игру могут попасть файлы: если процесс оборвут, receipt уже говорит, что нужен откат.
            receipt["status"] = "applying";
            WriteReceipt(ctx, label, receipt);
            try
            {
                foreach (var op in ordered)
                {
                    if (op.Unchanged) continue;
                    RequireClosed(ctx);
                    RequireNoLinks(ctx, op.Target);
                    if (op.Remove)
                    {
                        string current = File.Exists(op.Target) ? Util.Sha(op.Target) : null;
                        if (current != op.BeforeSha) throw new IOException("файл кэша изменился во время очистки");
                        if (current != null) File.Delete(op.Target);
                        if (File.Exists(op.Target)) throw new IOException("остаточный файл кэша не удалён");
                        Log.Write("очищен " + op.Relative);
                        continue;
                    }
                    string stage = op.Target + ".ru-stage-" + label;
                    try
                    {
                        Util.RequireNotLink(stage);
                        if (op.SourceBytes != null) File.WriteAllBytes(stage, op.SourceBytes);
                        else Util.Copy(op.Source, stage, true);
                        Util.FlushToDisk(stage);
                        if (Util.Sha(stage) != op.Candidate) throw new IOException("подготовленный файл не совпал с эталоном");
                        string now = File.Exists(op.Target) ? Util.Sha(op.Target) : null;
                        if (now != op.BeforeSha) throw new IOException("файл игры изменился во время установки");
                        Util.ReplaceFile(stage, op.Target);
                    }
                    finally { if (File.Exists(stage)) File.Delete(stage); }
                    if (Util.Sha(op.Target) != op.Candidate) throw new IOException("записанный файл не совпал с эталоном");
                    Log.Write("записан " + op.Relative);
                }
                receipt["status"] = "applied";
                WriteReceipt(ctx, label, receipt);
            }
            catch (Exception error)
            {
                Log.Write("сбой замены, откат: " + error.Message);
                receipt["status"] = "partial-requires-restore";
                try { WriteReceipt(ctx, label, receipt); } catch (Exception) { }
                bool rolledBack = false;
                string rollbackProblem = null;
                // Любая ошибка отката (в том числе «игра запущена» и остановки проверок) не должна выходить наружу как есть.
                try { RestoreReceipt(ctx, Path.GetDirectoryName(path), receipt); rolledBack = true; }
                catch (Exception rollbackError) { rollbackProblem = rollbackError.Message; Log.Write("откат не завершён: " + rollbackProblem); }
                if (rolledBack)
                {
                    receipt["status"] = "restored";
                    try { WriteReceipt(ctx, label, receipt); } catch (Exception) { }
                    throw new StopException("apply_reverted", "Не удалось записать файлы перевода (" + error.Message + "). Изменения отменены, игра осталась в прежнем состоянии.");
                }
                throw new StopException("apply_failed", "Установка прервана: не удалось записать файлы перевода (" + error.Message + "), и откат не завершён (" + rollbackProblem + "). Закройте игру и лаунчер и нажмите «Удалить перевод» в окне установщика: файлы вернутся к прежнему состоянию. Резервные копии лежат в " + ctx.BackupRoot + ".");
            }
        }

        static void Verify(Context ctx, string explicitGame, string label)
        {
            Configure(ctx, explicitGame);
            if (TransplantMode(ctx))
            {
                if (Progress.Active) { var tplan = new Plan(); PlanPayload(ctx, tplan); tplan.Apply(); }
                CheckPayload(ctx);
                VerifyTransplant(ctx, label);
                return;
            }
            if (Progress.Active)
            {
                var plan = new Plan();
                PlanPayload(ctx, plan);
                foreach (var p in ctx.M.Pkgs)
                    foreach (var f in p.Files) plan.Add(Math.Max(0, Plan.Size(Path.Combine(ctx.Cache, p.Id, f.Name))), 1);
                foreach (var row in ctx.M.Prime) plan.Add(row.Bytes, 1);
                PlanOverrideHashes(ctx, plan);
                plan.Apply();
            }
            CheckPayload(ctx);
            foreach (var p in ctx.M.Pkgs)
                foreach (var f in p.Files)
                {
                    string target = TargetPath(ctx, "packages/" + p.Id + "/" + f.Name);
                    if (!File.Exists(target) || Util.Sha(target) != f.Sha)
                        throw new StopException("verify", "После установки файл " + f.Name + " пакета " + p.Id + " не совпал с эталоном. Выполните удаление перевода и повторите.");
                }
            // prime должен остаться ровно таким, как был: пишем мы только в override.
            VerifyPrime(ctx, true);
            string state;
            PlanOverride(ctx, out state);
            if (state != "same") throw new StopException("verify", "Итоговая проверка кэша игры не прошла. Выполните удаление перевода и повторите.");
            if (!string.IsNullOrEmpty(label))
                foreach (var row in Util.Items(Util.ReadJson(ReceiptPath(ctx, label))["operations"]).Cast<Dictionary<string, object>>())
                {
                    string target = TargetPath(ctx, Util.Str(row, "relative"));
                    string actual = File.Exists(target) ? Util.Sha(target) : null;
                    if (actual != Util.Str(row, "candidate_sha256"))
                        throw new StopException("verify", "Проверка очистки кэша не прошла: " + Util.Str(row, "relative") + ". Выполните откат и повторите установку.");
                }
            var stateFile = new Dictionary<string, object>
            {
                { "installed", true }, { "version", ctx.M.Version }, { "built", ctx.M.Built }, { "game_version", ctx.M.GameVersion },
                { "game_root", ctx.Game }, { "label", label ?? "" }, { "installed_utc", DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture) },
                { "packages", ctx.M.Pkgs.Select(p => (object)p.Id).ToList() }, { "candidates", ctx.M.Candidates.Cast<object>().ToList() }
            };
            Util.WriteJson(ctx.StateFile, stateFile);
            try { File.Delete(ctx.MemoFile); } catch (IOException) { }
            ctx.Result["game"] = ctx.Game;
            Log.Write("итоговая проверка пройдена");
        }

        // ---- откат ------------------------------------------------------------------------

        static void RestoreReceipt(Context ctx, string receiptDir, Dictionary<string, object> receipt)
        {
            string gameRoot = Util.Str(receipt, "game_root");
            string cacheRoot = Util.Str(receipt, "cache_root");
            // Вызывающий уже отобрал receipt своей игры; проверка здесь страхует откат от чужих путей.
            if (ctx.Cache == null || cacheRoot == null || gameRoot == null || !Util.SamePath(cacheRoot, ctx.Cache) || !Util.SamePath(gameRoot, ctx.Game))
                throw new StopException("restore", "Резервная копия относится к другой папке игры (" + gameRoot + ").");
            var rows = Util.Items(receipt["operations"]).Cast<Dictionary<string, object>>().ToList();
            var work = new List<Dictionary<string, object>>();
            // Каталог и все его хранилища составляют один пакет. После официального обновления нельзя
            // вернуть удалённые старые _1/_2 к новому каталогу, даже если самих _1/_2 сейчас нет.
            var changedPackages = new HashSet<string>();
            foreach (var row in rows)
            {
                string relative = Util.Str(row, "relative");
                string destination = TargetPath(ctx, relative);
                string current = File.Exists(destination) ? Util.Sha(destination) : null;
                string before = Util.Str(row, "before_sha256"), candidate = Util.Str(row, "candidate_sha256");
                if (current != null && current != before && current != candidate)
                    changedPackages.Add(RelativeName.Match(relative).Groups[1].Value);
            }
            foreach (var row in rows)
            {
                string relative = Util.Str(row, "relative");
                string destination = TargetPath(ctx, relative);
                if (changedPackages.Contains(RelativeName.Match(relative).Groups[1].Value)) { SkipForeign(ctx, relative); continue; }
                if (!Util.SamePath(Util.Str(row, "target"), destination)) throw new StopException("restore", "Путь в резервной копии не совпадает с папкой игры: " + relative);
                bool beforeExists = Convert.ToBoolean(row["before_exists"]);
                string before = Util.Str(row, "before_sha256"), candidate = Util.Str(row, "candidate_sha256");
                if (before == candidate) continue;
                string current = File.Exists(destination) ? Util.Sha(destination) : null;
                if (beforeExists)
                {
                    string backup = Path.Combine(receiptDir, relative.Replace('/', '\\'));
                    if (!File.Exists(backup) || Util.Sha(backup) != before)
                        throw new StopException("restore", "Резервная копия файла " + relative + " повреждена или отсутствует: откат остановлен.");
                    if (current == before) continue;
                    if (current != null && current != candidate) { SkipForeign(ctx, relative); continue; }
                }
                else
                {
                    if (current == null) continue;
                    if (current != candidate) { SkipForeign(ctx, relative); continue; }
                }
                work.Add(row);
            }
            // При откате сначала каталог, потом хранилище: каталог без данных безопасен, данные без каталога тоже.
            foreach (var row in work.OrderBy(r => Util.Str(r, "relative").EndsWith(".info") ? 2 : (Util.Str(r, "relative").EndsWith("_0.db") ? 1 : 0)))
            {
                RequireClosed(ctx);
                string relative = Util.Str(row, "relative");
                string destination = TargetPath(ctx, relative);
                if (Convert.ToBoolean(row["before_exists"]))
                {
                    string backup = Path.Combine(receiptDir, relative.Replace('/', '\\'));
                    string stage = destination + ".ru-restore";
                    try
                    {
                        Util.RequireNotLink(stage);
                        Util.Copy(backup, stage, true);
                        Util.FlushToDisk(stage);
                        if (Util.Sha(stage) != Util.Str(row, "before_sha256")) throw new IOException("подготовленная копия не совпала");
                        Util.ReplaceFile(stage, destination);
                    }
                    finally { if (File.Exists(stage)) File.Delete(stage); }
                }
                else if (File.Exists(destination)) File.Delete(destination);
                Log.Write("восстановлен " + relative);
            }
        }

        // Файл уже не наш (его переписала игра или обновление): не трогаем, чтобы не затереть чужие данные.
        static void SkipForeign(Context ctx, string relative)
        {
            string old;
            ctx.Result.TryGetValue("skipped", out old);
            ctx.Result["skipped"] = (string.IsNullOrEmpty(old) ? "" : old + ", ") + relative;
            Log.Write("файл " + relative + " изменён игрой или обновлением, не наш: не трогаю");
        }

        static void Restore(Context ctx, string explicitGame)
        {
            int restored = 0, foreign = 0;
            // От новых к старым по created_utc (а не по метке в местном времени, которая сбивается при смене часов и пояса).
            var pending = ListReceipts(ctx).Where(r => Util.Str(r.Value, "status") != "restored").ToList();
            if (pending.Count == 0)
            {
                Log.Write("откатывать нечего");
                ctx.Result["restored"] = "0";
                RemoveState(ctx);
                return;
            }
            // Какую игру откатываем: заданную параметром, найденную лаунчером, иначе первую из существующих в receipt.
            string game = null;
            if (explicitGame != null) game = RequireStructure(explicitGame, true);
            else
            {
                string found = null;
                try { found = ResolveGame(null); } catch (StopException) { }
                if (found != null && pending.Any(r => SameGame(r.Value, found))) game = found;
                else
                    foreach (var pair in pending)
                    {
                        try { game = RequireStructure(Util.Str(pair.Value, "game_root"), true); break; } catch (StopException) { }
                    }
            }
            if (game == null)
            {
                // Игры, в которую ставился перевод, нет (удалена или переустановлена в другую папку): откатывать некуда, копии остаются.
                Log.Write("папка игры из резервных копий не найдена; receipt оставлены на месте: " + pending.Count);
                ctx.Result["restored"] = "0";
                ctx.Result["foreign"] = pending.Count.ToString(CultureInfo.InvariantCulture);
                return;
            }
            ctx.Game = game;
            ctx.Launcher = Directory.GetParent(ctx.Game).FullName;
            ctx.Cache = Path.Combine(ctx.Game, "QtsDataFolder", "QtsVFSCache", "packages");
            if (Progress.Active) PlanRestore(ctx, pending);
            Closer.Run(ctx, CloseMode);
            RequireClosed(ctx);
            foreach (var pair in pending)
            {
                string receiptFile = Path.Combine(pair.Key, "receipt.json");
                // Резервная копия другой папки игры (игру переустановили в другое место): не откатываем и не удаляем, не падаем.
                if (!SameGame(pair.Value, ctx.Game))
                {
                    foreign++;
                    Log.Write("резервная копия " + Path.GetFileName(pair.Key) + " относится к другой папке игры (" + Util.Str(pair.Value, "game_root") + "): пропуск, оставлена на месте");
                    continue;
                }
                pair.Value["status"] = "restore-in-progress";
                Util.WriteJson(receiptFile, pair.Value);
                try { RestoreReceipt(ctx, pair.Key, pair.Value); }
                catch (Exception)
                {
                    pair.Value["status"] = "restore-partial";
                    Util.WriteJson(receiptFile, pair.Value);
                    throw;
                }
                pair.Value["status"] = "restored";
                Util.WriteJson(receiptFile, pair.Value);
                restored++;
            }
            RemoveState(ctx);
            ctx.Result["restored"] = restored.ToString(CultureInfo.InvariantCulture);
            ctx.Result["foreign"] = foreign.ToString(CultureInfo.InvariantCulture);
            Log.Write("откат завершён, резервных копий применено: " + restored + ", чужих пропущено: " + foreign);
        }

        static void RemoveState(Context ctx)
        {
            try { if (File.Exists(ctx.StateFile)) File.Delete(ctx.StateFile); } catch (IOException) { }
            try { if (File.Exists(ctx.MemoFile)) File.Delete(ctx.MemoFile); } catch (IOException) { }
        }
    }

    // ================================================================================================
    // Автообновление: WXQTool.exe update [--check-only]
    //
    // Канал: GitHub Releases, фиксированные адреса <base>update.json и <base>update.json.sig. Подпись — RSA-3072, PKCS#1 v1.5, SHA-256 над точными байтами
    // update.json (формат и инструменты: scripts/publish_release.py); публичные ключи (основной и запасной) вшиты ресурсом update-config.json. Подпись
    // проверяется ДО разбора JSON. Скачанный Setup.exe запускается только после проверки размера и SHA-256 из подписанного манифеста.
    // Состояние для трея и пользователя — update-status.json в каталоге данных (пишет только помощник; пользователи читают).
    // ================================================================================================

    // Открытый ключ подписи: key_id = первые 16 hex SHA-256 от DER SubjectPublicKeyInfo.
    class UpdateKey { public string KeyId; public RSAParameters Params; }

    class UpdateManifest
    {
        public string Version, KeyId, Notes, SetupName, SetupSha;
        public DateTime Issued;
        public long SetupBytes;
        public int MinUpdater;
        public List<string> Bases = new List<string>();
        public List<string> GameVersions = new List<string>();
    }

    // Сетевой сбой одного запроса. Permanent — повторять бессмысленно (4xx, чужая схема, слишком большой ответ).
    class NetFail : Exception
    {
        public string Kind;
        public bool Permanent;
        public int Http;
        public NetFail(string kind, string message, bool permanent, int http) : base(message) { Kind = kind; Permanent = permanent; Http = http; }
    }

    class UpdHttp { public int Status; public byte[] Body; public string ETag; }

    // update-status.json: что видит трей. Запись целиком во временный файл и подмена (Util.WriteJson); сбой записи не роняет обновление.
    class UpdateStatus
    {
        readonly string path;
        public string StatusFile { get { return path; } }
        readonly Context ctx;
        readonly Stopwatch clock = Stopwatch.StartNew();
        long lastWrite = -1000;
        string state = "", code = "", message = "";
        public string Installed = "", Available = "", NextTry = "", Notes = "", GameVersion = "";
        public long BytesDone, BytesTotal, SetupBytes;
        public string SetupPath = "", SetupSha = "";   // готовый проверенный установщик для кнопки «Обновить сейчас»
        public string TextFile;

        public UpdateStatus(Context context, string file) { ctx = context; path = file; }

        public void Set(string newState, string newCode, string newMessage)
        {
            state = newState; code = newCode ?? ""; message = newMessage ?? "";
            // Готовый установщик теряет смысл, когда идёт загрузка новой версии, установка или всё уже актуально; при сбое сети и отсрочке он остаётся.
            if (newState == "downloading" || newState == "applying" || newState == "done" || newState == "idle") { SetupPath = ""; SetupSha = ""; SetupBytes = 0; }
            Write();
        }

        // Прогресс скачивания: не чаще раза в полсекунды.
        public void Tick()
        {
            if (clock.ElapsedMilliseconds - lastWrite >= 500) Write();
        }

        void Write()
        {
            lastWrite = clock.ElapsedMilliseconds;
            ctx.Result["state"] = state; ctx.Result["code"] = code; ctx.Result["message"] = message;
            ctx.Result["installed"] = Installed; ctx.Result["available"] = Available; ctx.Result["next_try_utc"] = NextTry;
            var data = new Dictionary<string, object>
            {
                { "state", state }, { "code", code }, { "message", message }, { "installed", Installed }, { "available", Available },
                { "next_try_utc", NextTry }, { "game_version", GameVersion }, { "setup_path", SetupPath }, { "setup_sha", SetupSha }, { "setup_bytes", SetupBytes }, { "bytes_done", BytesDone }, { "bytes_total", BytesTotal },
                { "updated_utc", DateTime.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture) }
            };
            try { Util.WriteJson(path, data); }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            if (!string.IsNullOrEmpty(TextFile))
            {
                var lines = data.Select(p => p.Key + "=" + Convert.ToString(p.Value, CultureInfo.InvariantCulture).Replace("\r", " ").Replace("\n", " ")).ToArray();
                // Inno определяет UTF-8 текстового протокола по BOM.
                try { Program.WriteSetupStatus(TextFile, Encoding.UTF8.GetBytes("\uFEFF" + string.Join("\n", lines) + "\n")); }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
            }
        }
    }

    static partial class Program
    {
        const int UpdaterVersion = 1;
        const long UpdMaxManifest = 256 * 1024, UpdMaxSig = 8 * 1024, UpdMaxSetup = 3072L * 1024 * 1024;
        const string UpdMutexName = "Global\\Egoist.WXQRussian.Update";
        const string SetupMutexName = "Global\\Egoist.WXQRussian.Setup";
        static readonly Regex UpdAssetName = new Regex(@"^[A-Za-z0-9][A-Za-z0-9._-]*$");
        static readonly Regex UpdVersionText = new Regex(@"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-rc[1-9][0-9]*)?$");
        static readonly Regex UpdSha = new Regex("^[0-9a-f]{64}$");
        static bool SetupFastNetwork;
        static DateTime SetupNetworkDeadline;

        public static void WriteSetupStatus(string path, byte[] bytes) { UpdWriteAtomic(path, bytes); }

        // Проверка канала из окна установщика не требует установленной игры и не скачивает EXE.
        static void SetupUpdate(Context ctx, Dictionary<string, string> args)
        {
            string mode = args.ContainsKey("mode") ? args["mode"] : "check";
            var status = new UpdateStatus(ctx, Path.Combine(ctx.Data, "setup-update-status.json"));
            status.TextFile = Path.Combine(ctx.Data, "setup-update-status.txt");
            Process owner = null;
            Mutex gate = null;
            bool held = false;
            try
            {
                if (mode == "launch") { SetupLaunch(ctx, args, status); return; }
                if (args.ContainsKey("parent"))
                {
                    int pid;
                    if (!int.TryParse(args["parent"], out pid) || pid < 1) throw new StopException("parent_changed", "Окно установщика уже закрыто.");
                    try { owner = Process.GetProcessById(pid); var ignored = owner.StartTime; if (owner.HasExited) throw new InvalidOperationException(); }
                    catch (Exception) { throw new StopException("parent_changed", "Окно установщика уже закрыто."); }
                    if (mode != "handoff")
                    {
                        var boundOwner = owner;
                        string cancel = args.ContainsKey("cancel") ? args["cancel"] : null;
                        var watcher = new Thread(delegate() { while (true) { try { if (boundOwner.HasExited || (cancel != null && File.Exists(cancel))) Environment.Exit(0); } catch (Exception) { Environment.Exit(0); } Thread.Sleep(150); } });
                        watcher.IsBackground = true;
                        watcher.Start();
                    }
                }
                gate = new Mutex(false, UpdMutexName);
                try { held = gate.WaitOne(0); } catch (AbandonedMutexException) { held = true; }
                if (!held) throw new StopException("busy", "Уже идёт другая проверка обновлений. Повторите через несколько секунд.");
                if (mode == "handoff") { SetupHandoff(ctx, args, owner, status); return; }
                if (mode != "check" && mode != "download") throw new StopException("usage", "Неизвестный режим обновления установщика.");
                string current = args.ContainsKey("current-version") ? args["current-version"] : "";
                if (!UpdVersionText.IsMatch(current)) throw new StopException("usage", "Не указана версия открытого установщика.");
                string installed, stamp, game;
                UpdReadState(ctx, out installed, out stamp, out game);
                status.Installed = installed ?? "";
                if (UpdVersionText.IsMatch(status.Installed) && UpdCmp(UpdVer(status.Installed), UpdVer(current)) > 0) current = status.Installed;
                status.Set("checking", "", "Проверяю новую версию…");
                SetupFastNetwork = mode == "check";
                SetupNetworkDeadline = DateTime.UtcNow.AddSeconds(8);
                if (SetupFastNetwork)
                {
                    var deadline = new Thread(delegate() { Thread.Sleep(8500); status.Set("failed", "network", "Нет быстрого ответа от сервера. Проверку можно повторить."); Environment.Exit(2); });
                    deadline.IsBackground = true; deadline.Start();
                }
                string baseUrl;
                var keys = UpdLoadKeys(out baseUrl);
#if TESTBUILD
                if (args.ContainsKey("test-base-url")) baseUrl = args["test-base-url"];
#endif
                if (string.IsNullOrEmpty(baseUrl) || keys.Count == 0 || !UpdUrlOk(baseUrl)) throw new StopException("not_configured", "В этой сборке не настроен канал обновлений.");
                var meta = UpdLoadMeta(ctx);
                bool noRelease;
                var fetched = UpdFetchManifest(ctx, baseUrl, keys, meta, out noRelease);
                if (fetched == null) { status.Set("current", "no_release", "Новых выпусков пока нет."); return; }
                var m = fetched.M;
                SetupAcceptManifest(ctx, m, meta);
                if (!fetched.FromCache)
                {
                    UpdWriteAtomic(Path.Combine(ctx.Data, "update-cache.json"), fetched.Data);
                    UpdWriteAtomic(Path.Combine(ctx.Data, "update-cache.sig"), fetched.Sig);
                    if (string.IsNullOrEmpty(fetched.NewEtag)) meta.Remove("etag"); else meta["etag"] = fetched.NewEtag;
                }
                meta["last_issued_utc"] = m.Issued.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture);
                UpdSaveMeta(ctx, meta);
                status.Available = m.Version;
                status.Notes = m.Notes;
                if (UpdCmp(UpdVer(m.Version), UpdVer(current)) <= 0) { status.Set("current", "up_to_date", "У вас актуальная версия."); return; }
                if (mode == "check") { status.Set("available", "", "Доступна новая версия " + m.Version + "."); return; }
                SetupFastNetwork = false;
                string updates = Path.Combine(ctx.Data, "updates");
                Directory.CreateDirectory(updates);
                CheckDataTree(ctx.Data);
                string tag = UpdCacheBase(m), exe = Path.Combine(updates, tag + ".exe"), part = Path.Combine(updates, tag + "-" + m.SetupSha.Substring(0, 16) + ".part");
                status.BytesTotal = m.SetupBytes;
                status.Set("downloading", "", "Скачиваю версию " + m.Version + "…");
                if (!File.Exists(exe) || !UpdFileOk(exe, m)) UpdDownload(m, part, exe, status);
                CheckDataTree(ctx.Data);
                if (!UpdFileOk(exe, m)) throw new StopException("sha_mismatch", "Скачанный установщик не прошёл проверку.");
                status.BytesDone = m.SetupBytes;
                status.SetupPath = exe; status.SetupSha = m.SetupSha; status.SetupBytes = m.SetupBytes;
                ctx.Result["setup_path"] = exe; ctx.Result["setup_sha"] = m.SetupSha;
                status.Set("ready", "", "Новая версия проверена. Перезапускаю установщик…");
            }
            catch (StopException stop) { status.Set("failed", stop.Code, stop.Message); throw; }
            catch (Exception) { status.Set("failed", "unexpected", "Проверка обновления не завершилась. Попробуйте ещё раз."); throw; }
            finally
            {
                SetupFastNetwork = false;
                if (held) { try { gate.ReleaseMutex(); } catch (ApplicationException) { } }
                if (gate != null) gate.Dispose();
                // Поток наблюдения сам завершается вместе с короткой командой; дескриптор родителя живёт до выхода процесса.
            }
        }

        static void SetupAcceptManifest(Context ctx, UpdateManifest m, Dictionary<string, object> meta)
        {
            if (m.MinUpdater > UpdaterVersion) throw new StopException("updater_too_old", "Обновите установщик со страницы релизов.");
            DateTime issued;
            if (UpdParseTime(Util.Str(meta, "last_issued_utc"), out issued) && m.Issued < issued)
                throw new StopException("replay", "Получен устаревший манифест. Повторите проверку позже.");
        }

        static UpdateManifest SetupCached(Context ctx, out string exe)
        {
            CheckDataTree(ctx.Data);
            string baseUrl;
            var keys = UpdLoadKeys(out baseUrl);
            byte[] bytes = File.ReadAllBytes(Path.Combine(ctx.Data, "update-cache.json"));
            byte[] sig;
            try { sig = Convert.FromBase64String(File.ReadAllText(Path.Combine(ctx.Data, "update-cache.sig"), Encoding.ASCII).Trim()); }
            catch (FormatException) { throw new StopException("bad_signature", "Подпись обновления не прошла проверку."); }
            var signer = UpdVerify(keys, bytes, sig);
            if (signer == null) throw new StopException("bad_signature", "Подпись обновления не прошла проверку.");
            var m = UpdParse(bytes, signer);
            SetupAcceptManifest(ctx, m, UpdLoadMeta(ctx));
            string installed, stamp, game;
            if (UpdReadState(ctx, out installed, out stamp, out game) && UpdCmp(UpdVer(m.Version), UpdVer(installed)) <= 0)
                throw new StopException("downgrade", "Эта версия уже установлена или заменена более новой.");
            exe = Path.Combine(ctx.Data, "updates", UpdCacheBase(m) + ".exe");
            if (!File.Exists(exe) || !UpdFileOk(exe, m)) throw new StopException("sha_mismatch", "Файл обновления изменился. Скачайте его заново.");
            return m;
        }

        static void SetupHandoff(Context ctx, Dictionary<string, string> args, Process parent, UpdateStatus status)
        {
            if (parent == null || parent.HasExited) throw new StopException("parent_changed", "Окно установщика уже закрыто.");
            string exe;
            var m = SetupCached(ctx, out exe);
            string ticket = Guid.NewGuid().ToString("N"), updates = Path.Combine(ctx.Data, "updates");
            string self = System.Reflection.Assembly.GetExecutingAssembly().Location;
            string staged = Path.Combine(updates, "handoff-" + ticket + ".exe"), ready = Path.Combine(updates, "handoff-" + ticket + ".ready");
            Util.Copy(self, staged, false);
            Util.FlushToDisk(staged);
            if (Util.Sha(staged) != Util.Sha(self)) throw new StopException("sha_mismatch", "Не удалось подготовить перезапуск установщика.");
            CheckDataTree(ctx.Data);
            string command = "setup-update --mode launch --data \"" + ctx.Data + "\" --parent " + parent.Id + " --parent-start " + parent.StartTime.ToUniversalTime().Ticks + " --ticket " + ticket;
            if (args.ContainsKey("game") && !string.IsNullOrEmpty(args["game"])) command += " --game \"" + Util.NormPath(args["game"]) + "\"";
            command += " --launcher " + (args.ContainsKey("launcher") && args["launcher"] == "0" ? "0" : "1");
            var child = Process.Start(new ProcessStartInfo(staged, command) { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = updates });
            if (child == null) throw new StopException("handoff", "Не удалось подготовить перезапуск. Старое окно остаётся открытым.");
            var clock = Stopwatch.StartNew();
            while (clock.ElapsedMilliseconds < 7000 && !File.Exists(ready) && !child.HasExited) Thread.Sleep(50);
            if (!File.Exists(ready) || File.ReadAllText(ready, Encoding.UTF8) != ticket || child.HasExited)
                throw new StopException("handoff", "Не удалось подготовить перезапуск. Повторите обновление.");
            status.Available = m.Version;
            status.Set("handoff", "", "Открываю новую версию установщика…");
        }

        static void SetupLaunch(Context ctx, Dictionary<string, string> args, UpdateStatus status)
        {
            int pid;
            long ticks;
            string ticket = args.ContainsKey("ticket") ? args["ticket"] : "";
            if (!Regex.IsMatch(ticket, "^[0-9a-f]{32}$") || !args.ContainsKey("parent") || !int.TryParse(args["parent"], out pid) || pid < 1 ||
                !args.ContainsKey("parent-start") || !long.TryParse(args["parent-start"], out ticks))
                throw new StopException("parent_changed", "Не удалось подтвердить окно установщика.");
            Process parent;
            try { parent = Process.GetProcessById(pid); if (parent.HasExited || parent.StartTime.ToUniversalTime().Ticks != ticks) throw new InvalidOperationException(); }
            catch (Exception) { throw new StopException("parent_changed", "Окно установщика сменилось. Откройте русификатор заново."); }
            string exe;
            var m = SetupCached(ctx, out exe);
            string ready = Path.Combine(ctx.Data, "updates", "handoff-" + ticket + ".ready");
            File.WriteAllText(ready, ticket, new UTF8Encoding(false));
            if (!parent.WaitForExit(30000)) throw new StopException("handoff", "Прежнее окно не закрылось. Обновление остановлено.");
            File.Delete(ready);
            // Inno держит глобальный SetupMutex до завершения старого мастера, включая его внешнюю оболочку.
            var clock = Stopwatch.StartNew();
            while (true)
            {
                bool exists = false;
                try { using (var old = Mutex.OpenExisting(SetupMutexName)) { exists = true; } } catch (WaitHandleCannotBeOpenedException) { }
                if (!exists) break;
                if (clock.ElapsedMilliseconds > 10000) throw new StopException("busy", "Другое окно установщика ещё работает. Откройте русификатор заново.");
                Thread.Sleep(100);
            }
            m = SetupCached(ctx, out exe);   // повторная проверка подписи и SHA непосредственно перед запуском
            string command = "/UPDATE=1 /CONTINUE=1 /NORESTART /LAUNCHERRU=" + (args.ContainsKey("launcher") && args["launcher"] == "0" ? "0" : "1");
            if (args.ContainsKey("game") && !string.IsNullOrEmpty(args["game"])) command += " /GAMEDIR=\"" + Util.NormPath(args["game"]) + "\"";
            command += " /LOG=\"" + Path.Combine(ctx.Data, "updates", "setup.log") + "\"";
            status.Available = m.Version;
            status.Set("handoff", "", "Открываю новую версию установщика…");
            var setup = Process.Start(new ProcessStartInfo(exe, command) { UseShellExecute = false, WorkingDirectory = Path.GetDirectoryName(exe) });
            if (setup == null) throw new StopException("handoff", "Не удалось открыть новую версию установщика.");
            setup.Dispose();
            status.Set("handoff", "launched", "Новая версия установщика открыта.");
        }

        // Удаление прежней фоновой интеграции. Только известные имена и точный путь нашего приложения.
        static void LegacyCleanup(Context ctx, Dictionary<string, string> args)
        {
            string app = args.ContainsKey("app") ? Util.NormPath(args["app"]) : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Egoist", "WXQ-Russian");
            bool allowed = Util.SamePath(app, Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Egoist", "WXQ-Russian"));
#if TESTBUILD
            string test = Environment.GetEnvironmentVariable("WXQ_RU_TEST_APP_ROOT");
            allowed = !string.IsNullOrEmpty(test) && Util.SamePath(test, app);
#endif
            if (!allowed || Util.IsReparse(app)) throw new StopException("dir", "Не подтверждена папка установленного русификатора.");
            string tray = Path.Combine(app, "WXQTray.exe");
#if !TESTBUILD
            foreach (var process in Process.GetProcessesByName("WXQTray"))
            {
                using (process)
                {
                    try
                    {
                        if (!Util.SamePath(process.MainModule.FileName, tray)) continue;
                        process.Kill(); process.WaitForExit(5000);
                    }
                    catch (Exception error) { Log.Write("прежний трей: " + error.Message); }
                }
            }
            foreach (var hive in new[] { Microsoft.Win32.RegistryHive.LocalMachine, Microsoft.Win32.RegistryHive.CurrentUser })
                foreach (var view in new[] { Microsoft.Win32.RegistryView.Registry64, Microsoft.Win32.RegistryView.Registry32 })
                    using (var root = Microsoft.Win32.RegistryKey.OpenBaseKey(hive, view))
                    using (var key = root.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", true))
                    {
                        if (key == null) continue;
                        string value = key.GetValue("EgoistWXQRussianTray") as string;
                        if (value != null && string.Equals(value.Trim().Trim('"'), tray, StringComparison.OrdinalIgnoreCase)) key.DeleteValue("EgoistWXQRussianTray", false);
                    }
            TaskRemove(ctx, new Dictionary<string, string>());
#endif
            foreach (string name in new[] { "WXQTray.exe", "WXQTray.exe.old" })
            {
                string file = Path.Combine(app, name);
                if (File.Exists(file) && !Util.IsReparse(file)) File.Delete(file);
            }
            ctx.Result["legacy_cleanup"] = "done";
        }

        // Точка входа: один обновляющий процесс на компьютер (отдельный Global-мьютекс). Статус пишется только после его захвата,
        // чтобы второй запуск не затёр статус работающего.
        static void Update(Context ctx, Dictionary<string, string> args, bool checkOnly)
        {
            bool created;
            var gate = new Mutex(false, UpdMutexName, out created);
            bool held;
            try { held = gate.WaitOne(0); }
            catch (AbandonedMutexException) { held = true; Log.Write("обновление: мьютекс остался от прерванного процесса, беру его"); }
            if (!held) { gate.Dispose(); throw new StopException("busy", "Обновление уже выполняется. Дождитесь его окончания."); }
            try
            {
                var status = new UpdateStatus(ctx, Path.Combine(ctx.Data, "update-status.json"));
                // Прежний готовый установщик (и доступная версия) переносятся в новый статус, пока файл на месте: сбой сети не должен убирать кнопку «Обновить сейчас».
                try
                {
                    var prev = Util.ReadJson(status.StatusFile);
                    string path = Util.Str(prev, "setup_path"), sha = Util.Str(prev, "setup_sha");
                    long bytes = UpdMetaNumber(prev, "setup_bytes");
                    string updatesDir = Path.GetFullPath(Path.Combine(ctx.Data, "updates")).TrimEnd('\\') + "\\";
                    if (!string.IsNullOrEmpty(path) && !string.IsNullOrEmpty(sha) && bytes > 0 && Path.GetFullPath(path).StartsWith(updatesDir, StringComparison.OrdinalIgnoreCase)
                        && File.Exists(path) && new FileInfo(path).Length == bytes)
                    { status.SetupPath = path; status.SetupSha = sha; status.SetupBytes = bytes; status.Available = Util.Str(prev, "available") ?? ""; }
                }
                catch (Exception) { }
                status.Set("checking", "", "");
                try { UpdateRun(ctx, args, checkOnly, status); }
                catch (StopException stop)
                {
                    if (stop.Code == "network" || stop.Code == "disk_space") status.NextTry = UpdIn(TimeSpan.FromMinutes(5));
                    else if (stop.Code == "av_blocked") status.NextTry = UpdIn(TimeSpan.FromHours(3));
                    status.Set("failed", stop.Code, stop.Message);
                    throw;
                }
                catch (Exception error)
                {
                    Log.Write("обновление: ошибка: " + error);
                    status.Set("failed", "unexpected", "Непредвиденная ошибка: " + error.Message);
                    throw;
                }
            }
            finally
            {
                try { gate.ReleaseMutex(); } catch (ApplicationException) { }
                gate.Dispose();
            }
        }

        // Текущее время UTC. Тестовая сборка может сдвинуть часы (WXQ_RU_TEST_CLOCK_HOURS), чтобы проверить сбитые часы.
        static DateTime UpdNow()
        {
#if TESTBUILD
            int hours;
            if (int.TryParse(Environment.GetEnvironmentVariable("WXQ_RU_TEST_CLOCK_HOURS"), NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out hours)) return DateTime.UtcNow.AddHours(hours);
#endif
            return DateTime.UtcNow;
        }

        static string UpdIn(TimeSpan span)
        {
            return UpdNow().Add(span).ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture);
        }

        static long UpdMetaNumber(Dictionary<string, object> meta, string key)
        {
            object value;
            if (!meta.TryGetValue(key, out value) || value == null) return 0;
            try { return Convert.ToInt64(value, CultureInfo.InvariantCulture); }
            catch (Exception) { return 0; }
        }

        // ---- отпечаток игры ----

        // Что с игрой относительно нашей последней установки.
        //   unknown            - сверять не с чем (нет receipt в состоянии applied, игры нет, файл не читается);
        //   intact             - файлы перевода в кэше игры такие, какими мы их записали;
        //   pending            - прошлая установка или откат оборвались (receipt applying / partial / restore-*);
        //   changed_not_ready  - игра обновилась и ещё не пересоздала чистый кэш: надо один раз запустить её до меню;
        //   changed_ready      - кэш пересоздан: перевод можно пересадить сохранённым установщиком.
        class UpdProbe
        {
            public string Kind = "unknown";
            public string GameVersion = "";
            public string Fingerprint { get { return GameVersion; } }
        }

        static UpdProbe UpdProbeGame(Context ctx, string gameRoot)
        {
            var probe = new UpdProbe();
            try
            {
                if (string.IsNullOrEmpty(gameRoot) || !Directory.Exists(gameRoot)) return probe;
                ctx.Game = Util.NormPath(gameRoot);
                ctx.Launcher = Directory.GetParent(ctx.Game).FullName;
                ctx.Cache = Path.Combine(ctx.Game, "QtsDataFolder", "QtsVFSCache", "packages");
                Dictionary<string, object> newest = null;
                var rows = new Dictionary<string, Dictionary<string, object>>();   // relative -> самая свежая строка из receipt в состоянии applied
                foreach (var pair in ListReceipts(ctx))
                {
                    if (!SameGame(pair.Value, ctx.Game)) continue;
                    string st = Util.Str(pair.Value, "status");
                    if (IsPartialStatus(st)) { probe.Kind = "pending"; return probe; }
                    if (st != "applied") continue;
                    if (newest == null) newest = pair.Value;
                    foreach (var row in Util.Items(pair.Value["operations"]).Cast<Dictionary<string, object>>())
                    {
                        string relative = Util.Str(row, "relative");
                        if (relative != null && RelativeName.IsMatch(relative) && !rows.ContainsKey(relative)) rows[relative] = row;
                    }
                }
                if (newest == null || rows.Count == 0) return probe;
                var ids = new List<string>();
                bool ours = true;
                foreach (var pair in rows)
                {
                    string id = RelativeName.Match(pair.Key).Groups[1].Value;
                    if (!ids.Contains(id)) ids.Add(id);
                    string before = Util.Str(pair.Value, "before_sha256"), wanted = Util.Str(pair.Value, "candidate_sha256");
                    if (before == wanted) continue;
                    string target = Path.Combine(ctx.Cache, id, pair.Key.Substring(pair.Key.LastIndexOf('/') + 1));
                    if (ours && (File.Exists(target) ? Util.Sha(target) : null) != wanted) ours = false;
                }
                foreach (string id in ids)
                {
                    string version = ReadVersion(Path.Combine(ctx.Cache, id, id + ".info"));
                    if (!string.IsNullOrEmpty(version)) { probe.GameVersion = version; break; }
                }
                if (ours && IsTransplantReceipt(newest))
                {
                    // Пересадка: файлы совпали, но заголовок пакета обязан нести pkgHash нынешнего prime из журнала игры.
                    var merged = new Dictionary<string, object> { { "operations", rows.Values.Cast<object>().ToList() } };
                    foreach (string id in ids)
                    {
                        bool owns;
                        try { owns = ReceiptOwns(ctx, merged, new Pkg { Id = id }); }
                        catch (StopException) { owns = false; }
                        if (!owns) { ours = false; break; }
                    }
                }
                if (ours) { probe.Kind = "intact"; return probe; }
                bool ready = true;
                foreach (string id in ids)
                {
                    string dir = Path.Combine(ctx.Cache, id);
                    string catalog = Path.Combine(dir, id + ".db"), storage = Path.Combine(dir, id + "_0.db");
                    if (!File.Exists(Path.Combine(dir, id + ".info")) || !File.Exists(catalog)) { ready = false; break; }
                    try { ReadSkeleton(ctx, new Pkg { Id = id }, catalog, storage); }
                    catch (StopException) { ready = false; break; }
                }
#if TESTBUILD
                // Тестовая сборка: готовность кэша задаётся из окружения (разбор настоящих db-файлов проверяют тесты пересадки).
                string forced = Environment.GetEnvironmentVariable("WXQ_RU_TEST_CACHE_READY");
                if (forced == "1") ready = true; else if (forced == "0") ready = false;
#endif
                probe.Kind = ready ? "changed_ready" : "changed_not_ready";
            }
            catch (Exception error)
            {
                Log.Write("обновление: отпечаток игры не снят: " + error.Message);
                probe.Kind = "unknown";
            }
            Log.Write("обновление: игра: " + probe.Kind + ", версия " + (probe.GameVersion.Length > 0 ? probe.GameVersion : "?"));
            return probe;
        }

        // Имя кэшированного установщика: для окна UAC понятное. Версия проверена регулярным выражением (цифры, точки, -rcN), SHA-256 от имени не зависит.
        static string UpdCacheBase(UpdateManifest m) { return "WXQ-Russian-Setup-" + m.Version; }

        // ---- сохранённый установщик текущей версии ----

        // updates\installed-<sha16>.exe; SHA-256 и размер записаны в update-meta.json (скачан и проверен по подписанному манифесту).
        // requiredVersion - версия из state.json: копия другой версии не берётся (иначе пересадка поставила бы старую версию поверх новой).
        // null - версия не сверяется (state.json снят откатом оборванного обновления, нужна копия «до обновления»).
        static string UpdSavedInstaller(Context ctx, Dictionary<string, object> meta, string requiredVersion)
        {
            try
            {
                if (requiredVersion != null && Util.Str(meta, "installed_setup_version") != requiredVersion) return null;
                string sha = Util.Str(meta, "installed_setup_sha");
                long bytes = UpdMetaNumber(meta, "installed_setup_bytes");
                if (sha == null || !UpdSha.IsMatch(sha) || bytes < 1) return null;
                string updates = Path.Combine(ctx.Data, "updates");
                string path = Path.Combine(updates, "installed-" + sha.Substring(0, 16) + ".exe");
                if (!File.Exists(path) || Util.IsReparse(updates) || Util.IsReparse(path)) return null;
                if (new FileInfo(path).Length != bytes || Util.Sha(path) != sha) return null;
                return path;
            }
            catch (Exception) { return null; }
        }

        // После успеха скачанный установщик не удаляется: он остаётся единственной копией «текущей версии».
        static void UpdKeepInstalled(Dictionary<string, object> meta, string exe, string sha, long bytes, string version)
        {
            try
            {
                string updates = Path.GetDirectoryName(exe);
                string keep = Path.Combine(updates, "installed-" + sha.Substring(0, 16) + ".exe");
                if (!string.Equals(exe, keep, StringComparison.OrdinalIgnoreCase))
                {
                    if (File.Exists(keep)) File.Delete(keep);
                    File.Move(exe, keep);
                }
                foreach (string other in Directory.GetFiles(updates, "installed-*.exe"))
                    if (!string.Equals(other, keep, StringComparison.OrdinalIgnoreCase)) { try { File.Delete(other); } catch (IOException) { } }
                meta["installed_setup_sha"] = sha; meta["installed_setup_bytes"] = bytes; meta["installed_setup_version"] = version;
            }
            catch (IOException error) { Log.Write("обновление: копия установщика не сохранена: " + error.Message); }
            catch (UnauthorizedAccessException error) { Log.Write("обновление: копия установщика не сохранена: " + error.Message); }
        }

        static StopException UpdAvBlocked(string updates)
        {
            return new StopException("av_blocked", "Антивирус, похоже, заблокировал или удалил скачанный установщик обновления. Откройте карантин антивируса и восстановите файл или добавьте папку " + updates + " в исключения, затем нажмите «Проверить сейчас».");
        }

        // Запуск Setup.exe обновления с /UPDATE=1. Исчезнувший файл и отказ запуска (225 - вирус, 5 - доступ, 2/3 - файла нет, 1260 - политика) - av_blocked.
        static int UpdRunSetup(string exe, string updates, string gameRoot, bool launcherOn)
        {
            if (!File.Exists(exe)) throw UpdAvBlocked(updates);
            string setupLog = Path.Combine(updates, "setup.log");
            string arguments = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /UPDATE=1 /GAMEDIR=\"" + gameRoot.TrimEnd('\\') + "\" /LOG=\"" + setupLog + "\"";
            string extra = "";
#if TESTBUILD
            extra = Environment.GetEnvironmentVariable("WXQ_RU_TEST_SETUP_ARGS") ?? "";
#endif
            // Русский лаунчер: сохраняем выбор пользователя (по умолчанию установщик включает его, а отказавшийся не должен получить его обратно).
            if (extra.IndexOf("/LAUNCHERRU=", StringComparison.OrdinalIgnoreCase) < 0) arguments += " /LAUNCHERRU=" + (launcherOn ? "1" : "0");
            if (extra.Length > 0) arguments += " " + extra;
            Log.Write("обновление: запускаю " + Path.GetFileName(exe) + " " + arguments);
            var info = new ProcessStartInfo { FileName = exe, Arguments = arguments, UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = updates };
            Process process;
            try { process = Process.Start(info); }
            catch (System.ComponentModel.Win32Exception error)
            {
                int native = error.NativeErrorCode;
                if (native == 225 || native == 5 || native == 2 || native == 3 || native == 1260) throw UpdAvBlocked(updates);
                throw;
            }
            using (process)
            {
                if (!process.WaitForExit(90 * 60 * 1000))
                    throw new StopException("setup_timeout", "Установщик обновления работает дольше 90 минут. Я его не трогаю; проверьте, что он не завис.");
                Log.Write("обновление: установщик завершился с кодом " + process.ExitCode);
                return process.ExitCode;
            }
        }

        // state.json после установщика: установлен ли перевод, его версия и метка установки.
        static bool UpdReadState(Context ctx, out string version, out string stamp, out string gameRoot)
        {
            version = ""; stamp = ""; gameRoot = null;
            try
            {
                if (!File.Exists(ctx.StateFile)) return false;
                var s = Util.ReadJson(ctx.StateFile);
                if (s == null) return false;
                string flag = Util.Str(s, "installed");
                version = Util.Str(s, "version") ?? "";
                stamp = Util.Str(s, "installed_utc") ?? "";
                gameRoot = Util.Str(s, "game_root");
                return (flag == "True" || flag == "true") && !string.IsNullOrEmpty(gameRoot);
            }
            catch (Exception) { return false; }
        }

        // Последний код остановки помощника в журнале («остановка [код]: ...»).
        static string UpdLastStopCode(Context ctx)
        {
            try
            {
                string log = Path.Combine(ctx.Data, "install.log");
                if (!File.Exists(log)) return "";
                foreach (string line in File.ReadAllLines(log, Encoding.UTF8).Reverse().Take(80))
                {
                    var match = Regex.Match(line, @"остановка \[([a-z_]+)\]");
                    if (match.Success) return match.Groups[1].Value;
                }
            }
            catch (Exception) { }
            return "";
        }

        // Постоянные причины (не исчезнут сами, пока не изменится игра): версия игры, остатки прежней установки, нет чистого кэша.
        static bool UpdPermanentCode(string code)
        {
            return code == "version" || code == "reinstall" || code == "cache_missing";
        }

        // Запись о сбое версии: постоянная причина ждёт сутки (и сбрасывается, когда у игры другая версия), временная - три часа.
        static void UpdRecordFailure(Context ctx, Dictionary<string, object> meta, string key, UpdProbe probe, string code, string text, UpdateStatus status)
        {
            TimeSpan span = TimeSpan.FromHours(UpdPermanentCode(code) ? 24 : 3);
            meta["failed_version"] = key; meta["failed_until_utc"] = UpdIn(span); meta["failed_message"] = text;
            meta["failed_fp"] = probe.Fingerprint; meta["failed_code"] = code;
            UpdSaveMeta(ctx, meta);
            status.NextTry = UpdIn(span);
        }

        static void UpdClearFailure(Dictionary<string, object> meta)
        {
            foreach (string name in new[] { "failed_version", "failed_until_utc", "failed_message", "failed_fp", "failed_code" }) meta.Remove(name);
        }

        // Запись о сбое действует, пока срок не вышел. Срок дальше 25 часов от нынешнего времени (мы пишем не больше суток) считается истёкшим:
        // часы уходили вперёд и вернулись, либо метка испорчена.
        static bool UpdFailureActive(Dictionary<string, object> meta, string key, UpdProbe probe, out DateTime until)
        {
            until = DateTime.MinValue;
            if (Util.Str(meta, "failed_version") != key) return false;
            string fp = Util.Str(meta, "failed_fp");
            if (fp != null && fp != probe.Fingerprint) return false;
            if (!UpdParseTime(Util.Str(meta, "failed_until_utc"), out until)) return false;
            DateTime now = UpdNow();
            return until > now && until <= now.AddHours(25);
        }

        // ---- манифест ----

        class UpdFetched
        {
            public UpdateManifest M;
            public UpdateKey Signer;
            public byte[] Data, Sig;
            public string NewEtag;
            public bool FromCache;
        }

        // Условный запрос (ETag), подпись, только потом разбор. Подпись свежего ответа не прошла - один повтор через минуту (CDN мог отдать
        // обрывок или устаревшую пару), подпись кэша не прошла - кэш удаляется и запрос идёт заново. null с noRelease=true: релизов пока нет (404).
        static UpdFetched UpdFetchManifest(Context ctx, string root, List<UpdateKey> keys, Dictionary<string, object> meta, out bool noRelease)
        {
            noRelease = false;
            string cacheJson = Path.Combine(ctx.Data, "update-cache.json"), cacheSig = Path.Combine(ctx.Data, "update-cache.sig");
            bool haveCache = File.Exists(cacheJson) && File.Exists(cacheSig);
            string etag = haveCache ? Util.Str(meta, "etag") : null;
            int freshTries = 0;
            while (true)
            {
                UpdHttp got;
                try { got = UpdRetry("update.json", delegate { return UpdGetSmall(root + "update.json", etag, UpdMaxManifest); }); }
                catch (NetFail fail)
                {
                    if (fail.Http == 404) { noRelease = true; return null; }
                    throw new StopException("network", "Нет связи с сервером обновлений: " + fail.Message);
                }
                var result = new UpdFetched();
                result.FromCache = got.Status == 304;
                if (result.FromCache)
                {
                    if (!File.Exists(cacheJson) || !File.Exists(cacheSig)) { etag = null; meta.Remove("etag"); continue; }
                    result.Data = File.ReadAllBytes(cacheJson);
                    result.Sig = File.ReadAllBytes(cacheSig);
                    Log.Write("обновление: манифест не менялся (304)");
                }
                else
                {
                    result.Data = got.Body;
                    UpdHttp sigGot;
                    try { sigGot = UpdRetry("update.json.sig", delegate { return UpdGetSmall(root + "update.json.sig", null, UpdMaxSig); }); }
                    catch (NetFail fail) { throw new StopException("network", "Нет связи с сервером обновлений: " + fail.Message); }
                    result.Sig = sigGot.Body;
                    result.NewEtag = got.ETag;
                }
                byte[] sigBinary;
                try { sigBinary = Convert.FromBase64String(Encoding.ASCII.GetString(result.Sig).Trim()); }
                catch (FormatException) { sigBinary = null; }
                result.Signer = sigBinary == null ? null : UpdVerify(keys, result.Data, sigBinary);
                if (result.Signer != null)
                {
                    result.M = UpdParse(result.Data, result.Signer);
                    return result;
                }
                if (result.FromCache)
                {
                    try { File.Delete(cacheJson); File.Delete(cacheSig); } catch (IOException) { }
                    meta.Remove("etag"); UpdSaveMeta(ctx, meta);
                    etag = null;
                    continue;
                }
                if (freshTries++ == 0)
                {
                    int pause = UpdPauses()[1];
                    Log.Write("обновление: подпись не прошла проверку, повтор через " + (pause / 1000) + " с");
                    Thread.Sleep(pause);
                    continue;
                }
                throw new StopException("bad_signature", "Подпись обновления не прошла проверку. Обновление не применено.");
            }
        }

        // ---- основной ход ----

        static void UpdateRun(Context ctx, Dictionary<string, string> args, bool checkOnly, UpdateStatus status)
        {
            Log.Write("обновление: запуск" + (checkOnly ? " (только проверка)" : ""));
            UpdCleanOld();
            var meta = UpdLoadMeta(ctx);
            string updates = Path.Combine(ctx.Data, "updates");

            // 1. Что установлено. Без установленного перевода обновлять нечего.
            string installedVersion, installedStamp, gameRoot;
            bool installed = UpdReadState(ctx, out installedVersion, out installedStamp, out gameRoot);
            status.Installed = installedVersion;
            if (!installed)
            {
                // Прерванное обновление: откат снял state.json, а новая версия не встала. Возвращаем сохранённую.
                string inflightGame = Util.Str(meta, "inflight_game");
                string savedForRecovery = checkOnly || string.IsNullOrEmpty(Util.Str(meta, "inflight_version")) ? null : UpdSavedInstaller(ctx, meta, null);
                if (savedForRecovery != null && !string.IsNullOrEmpty(inflightGame) && Directory.Exists(inflightGame))
                {
                    Log.Write("обновление: прошлое обновление не завершилось, возвращаю сохранённую версию");
                    if (UpdRollback(ctx, status, meta, savedForRecovery, inflightGame, updates))
                    {
                        string back0, stamp0, game0;
                        UpdReadState(ctx, out back0, out stamp0, out game0);
                        status.Installed = back0;
                        status.Set("done", "reapplied", "Перевод возвращён после прерванного обновления.");
                        return;
                    }
                }
                status.Set("idle", "not_installed", "Перевод не установлен.");
                return;
            }

            // 2. Отпечаток игры (версии из <N>.info, SHA наших файлов из последнего receipt) - до сравнения версий.
            var probe = UpdProbeGame(ctx, gameRoot);
            status.GameVersion = probe.GameVersion;
            if (probe.Kind == "changed_not_ready")
            {
                // Игра обновилась, перевод остаётся в силе для старой версии, а новый кэш игра создаёт сама. Ничего не пишем и не качаем.
                UpdSaveMeta(ctx, meta);
                status.Set("deferred", "game_changed", "Игра обновилась. Запустите её один раз до меню и закройте — перевод вернётся сам.");
                Log.Write("обновление: игра обновилась, кэш ещё не пересоздан");
                return;
            }
            bool reapply = probe.Kind == "changed_ready" || probe.Kind == "pending";
            string savedExe = UpdSavedInstaller(ctx, meta, installedVersion);

            // 3. Адрес и ключи.
            string baseUrl;
            var keys = UpdLoadKeys(out baseUrl);
            string root = baseUrl;
#if TESTBUILD
            if (args.ContainsKey("test-base-url") && args["test-base-url"].Length > 0) root = args["test-base-url"];
#endif
            if (string.IsNullOrEmpty(root) || keys.Count == 0 || !UpdUrlOk(root))
                throw new StopException("not_configured", "В этой сборке помощника не заданы адрес обновлений или ключи подписи.");

            // 4. Манифест. Без связи при нужной пересадке хватает сохранённого установщика текущей версии.
            UpdFetched fetched = null;
            bool noRelease;
            try { fetched = UpdFetchManifest(ctx, root, keys, meta, out noRelease); }
            catch (StopException stop)
            {
                if (!(reapply && savedExe != null && stop.Code == "network")) throw;
                Log.Write("обновление: нет связи, пересадка пойдёт из сохранённого установщика");
                noRelease = false;
            }
            if (fetched == null && !noRelease && !(reapply && savedExe != null)) throw new StopException("network", "Нет связи с сервером обновлений.");
            if (fetched == null && noRelease && !(reapply && savedExe != null)) { status.Set("idle", "no_release", "Опубликованных обновлений пока нет."); return; }

            UpdateManifest m = fetched == null ? null : fetched.M;
            int cmp = 0;
            bool update = false;
            if (m != null)
            {
                status.Notes = m.Notes;
                Log.Write("обновление: подпись верна (ключ " + fetched.Signer.KeyId + "), в манифесте версия " + m.Version + ", установлена " + installedVersion);
                if (m.MinUpdater > UpdaterVersion)
                    throw new StopException("updater_too_old", "Для этого обновления нужна более новая версия русификатора. Скачайте установщик заново со страницы релизов.");
                cmp = UpdCmp(UpdVer(m.Version), UpdVer(installedVersion));
                if (cmp < 0 && !(reapply && savedExe != null))
                    throw new StopException("downgrade", "Версия в манифесте (" + m.Version + ") ниже установленной (" + installedVersion + "). Обновление отклонено.");
                DateTime lastIssued;
                string lastText = Util.Str(meta, "last_issued_utc");
                if (cmp > 0 && lastText != null && UpdParseTime(lastText, out lastIssued) && m.Issued < lastIssued)
                    throw new StopException("replay", "Манифест выпущен раньше уже принятого (" + lastText + "). Похоже на подмену старой версией; обновление отклонено.");
                // Принято (подпись верна, версия не ниже, время не старее): кэш, ETag и метка времени.
                if (!fetched.FromCache)
                {
                    UpdWriteAtomic(Path.Combine(ctx.Data, "update-cache.json"), fetched.Data);
                    UpdWriteAtomic(Path.Combine(ctx.Data, "update-cache.sig"), fetched.Sig);
                    if (!string.IsNullOrEmpty(fetched.NewEtag)) meta["etag"] = fetched.NewEtag; else meta.Remove("etag");
                }
                if (lastText == null || !UpdParseTime(lastText, out lastIssued) || m.Issued > lastIssued)
                    meta["last_issued_utc"] = m.Issued.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture);
                update = cmp > 0;
            }
            // Версия из манифеста уже установлена (например, поставили кнопкой «Обновить сейчас»): проверенный установщик из кэша становится
            // сохранённой копией текущей версии (для пересадки после обновления игры и отката), иначе она осталась бы от прежней версии.
            if (m != null && cmp == 0)
            {
                try
                {
                    string cached = Path.Combine(updates, UpdCacheBase(m) + ".exe");
                    if (File.Exists(cached) && !Util.IsReparse(updates) && !Util.IsReparse(cached) && UpdFileOk(cached, m))
                    {
                        UpdKeepInstalled(meta, cached, m.SetupSha, m.SetupBytes, m.Version);
                        UpdSaveMeta(ctx, meta);
                        savedExe = UpdSavedInstaller(ctx, meta, installedVersion);
                        Log.Write("обновление: установленная версия " + m.Version + ", проверенный установщик сохранён как копия текущей версии");
                    }
                }
                catch (Exception error) { Log.Write("обновление: копия установщика текущей версии не сохранена: " + error.Message); }
            }
            if (!update && !reapply)
            {
                UpdSaveMeta(ctx, meta);
                status.Set("idle", "up_to_date", "Установлена последняя версия перевода: " + installedVersion + ".");
                return;
            }

            // 5. Цель: новая версия из манифеста либо возврат текущей (пересадка после обновления игры, откат оборванной установки).
            string targetVersion = update ? m.Version : installedVersion;
            bool useSaved = !update && savedExe != null;
            if (!update && !useSaved && m == null)
                throw new StopException("no_installer", "Для возврата перевода нужен установщик текущей версии, а сохранённой копии нет. Скачайте установщик заново со страницы релизов.");
            status.Available = update ? m.Version : "";
            string failKey = update ? m.Version : "reapply:" + installedVersion;
            DateTime until;
            if (UpdFailureActive(meta, failKey, probe, out until))
            {
                UpdSaveMeta(ctx, meta);
                status.NextTry = until.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture);
                status.Set("failed", "retry_later", "Прошлая попытка " + (update ? "установить версию " + m.Version : "вернуть перевод после обновления игры") + " не удалась: " + (Util.Str(meta, "failed_message") ?? "см. журнал") + " Следующая попытка позже.");
                return;
            }
            // Новая версия собрана для более новой игры, чем стоит у пользователя: ждём обновления игры, а текущая версия продолжает работать.
            if (update && m.GameVersions.Count > 0)
            {
                int[] have = UpdGameVer(probe.GameVersion), lowest = null;
                foreach (string text in m.GameVersions)
                {
                    int[] parsed = UpdGameVer(text);
                    if (parsed != null && (lowest == null || UpdGameCmp(parsed, lowest) < 0)) lowest = parsed;
                }
                if (have != null && lowest != null && UpdGameCmp(have, lowest) < 0)
                {
                    UpdSaveMeta(ctx, meta);
                    status.NextTry = UpdIn(TimeSpan.FromHours(6));
                    status.Set("deferred", "game_old", "Версия " + m.Version + " ждёт обновления игры. Пока работает версия " + installedVersion + ".");
                    return;
                }
            }
            UpdSaveMeta(ctx, meta);

            // 6. Установщик: сохранённый или скачанный (с докачкой) и проверенный.
            if (Directory.Exists(updates) && Util.IsReparse(updates))
                throw new StopException("acl", "Папка загрузок обновления является ссылкой (" + updates + "), помощник её не использует.");
            Directory.CreateDirectory(updates);
            string exe;
            string setupSha = null;
            long setupBytes = 0;
            if (useSaved)
            {
                exe = savedExe;
                setupSha = Util.Str(meta, "installed_setup_sha");
                setupBytes = UpdMetaNumber(meta, "installed_setup_bytes");
            }
            else
            {
                string tag = UpdCacheBase(m);
                string part = Path.Combine(updates, tag + ".part");
                exe = Path.Combine(updates, tag + ".exe");
                setupSha = m.SetupSha; setupBytes = m.SetupBytes;
                UpdPrune(updates, tag);
                if (File.Exists(exe) && !UpdFileOk(exe, m)) { Log.Write("обновление: прежняя загрузка повреждена, удаляю"); File.Delete(exe); }
                if (!File.Exists(exe))
                {
                    var needs = new SpaceNeeds();
                    long have = File.Exists(part) ? new FileInfo(part).Length : 0;
                    needs.Add(updates, Math.Max(0, m.SetupBytes - have));
                    needs.Require();
                    status.BytesDone = have; status.BytesTotal = m.SetupBytes;
                    status.Set("downloading", "", "Скачивается версия " + m.Version + ".");
                    UpdDownload(m, part, exe, status);
                    Log.Write("обновление: " + m.SetupName + " скачан, SHA-256 совпал");
                }
#if TESTBUILD
                if (Environment.GetEnvironmentVariable("WXQ_RU_TEST_QUARANTINE") == "1") { try { File.Delete(exe); } catch (IOException) { } }
#endif
                if (!File.Exists(exe)) throw UpdAvBlocked(updates);
            }
            status.BytesDone = setupBytes; status.BytesTotal = setupBytes;

            // Только проверка (заход в русификатор): установщик скачан и проверен, но не запускается и ничего не закрывается.
            // Применяет обновление только кнопка «Обновить сейчас» в трее: она запускает этот файл и сверяет его SHA-256 с записанным здесь.
            if (checkOnly)
            {
                if (Util.IsReparse(updates) || Util.IsReparse(exe)) throw new StopException("acl", "Скачанный установщик или его папка стали ссылкой.");
                status.SetupPath = exe; status.SetupSha = setupSha; status.SetupBytes = setupBytes;
                status.Available = update ? m.Version : "";
                if (update) status.Set("ready", "", "Доступна версия " + m.Version + ".");
                else status.Set("ready", "reapply", "Перевод будет возвращён сохранённым установщиком.");
                Log.Write("обновление: только проверка, установщик готов (" + exe + ")");
                return;
            }

            // 7. Игра или лаунчер запущены, идёт другой установщик или операция — ничего не пишем, ждём.
            string gate = UpdBlocker(ctx, gameRoot);
            if (gate != null)
            {
                status.NextTry = UpdIn(TimeSpan.FromMinutes(30));
                string what = update ? "Обновление до " + m.Version : "Возврат перевода после обновления игры";
                status.Set("deferred", gate == "setup" || gate == "busy" ? gate + "_running" : "game_running",
                    gate == "game" ? what + " ждёт закрытия игры и лаунчера."
                                   : what + " подождёт: сейчас идёт другая операция русификатора.");
                Log.Write("обновление: отложено (" + gate + ")");
                return;
            }

            // 8. Запуск установщика. Перед запуском: папка и файл не ссылки, владельцы и права как у каталога данных, SHA-256 ещё раз.
            if (Util.IsReparse(updates) || Util.IsReparse(exe))
                throw new StopException("acl", "Скачанный установщик или его папка стали ссылкой; запуск отменён.");
            CheckDataTree(updates);
            if (!File.Exists(exe)) throw UpdAvBlocked(updates);
            bool exeOk;
            try { exeOk = new FileInfo(exe).Length == setupBytes && Util.Sha(exe) == setupSha; }
            catch (FileNotFoundException) { throw UpdAvBlocked(updates); }
            catch (IOException) { exeOk = false; }
            if (!exeOk)
                throw new StopException("sha_mismatch", "Скачанный установщик изменился после проверки; запуск отменён.");
            status.Set("applying", "", update ? "Устанавливается версия " + m.Version + "." : "Возвращается перевод после обновления игры.");
            meta["inflight_version"] = targetVersion; meta["inflight_game"] = gameRoot;
            UpdSaveMeta(ctx, meta);
            int exitCode = UpdRunSetup(exe, updates, gameRoot, UpdLauncherInstalled());

            // 9. Итог — по state.json: версия должна стать новой (при возврате - обновиться метка установки).
            string after, afterStamp, afterGame;
            bool afterInstalled = UpdReadState(ctx, out after, out afterStamp, out afterGame);
            bool success = afterInstalled && (update
                ? after.Length > 0 && UpdCmp(UpdVer(after), UpdVer(m.Version)) >= 0 && after != installedVersion
                : afterStamp.Length > 0 && afterStamp != installedStamp);
            if (success)
            {
                UpdClearFailure(meta);
                meta.Remove("inflight_version"); meta.Remove("inflight_game");
                UpdKeepInstalled(meta, exe, setupSha, setupBytes, targetVersion);
                UpdSaveMeta(ctx, meta);
                status.Installed = after; status.Available = ""; status.NextTry = "";
                status.GameVersion = UpdProbeGame(ctx, gameRoot).GameVersion;
                status.Set("done", update ? "" : "reapplied", update ? "Перевод обновлён до " + after + "." : "Перевод возвращён после обновления игры.");
                return;
            }
            string again = UpdBlocker(ctx, gameRoot);
            if (again == "game")
            {
                status.NextTry = UpdIn(TimeSpan.FromMinutes(30));
                status.Set("deferred", "game_running", (update ? "Обновление до " + m.Version : "Возврат перевода") + " ждёт закрытия игры и лаунчера.");
                return;
            }
            string why = UpdLastStop(ctx);
            string stopCode = UpdLastStopCode(ctx);
            string text2 = "Установщик завершился с кодом " + exitCode + (why.Length > 0 ? ": " + why : ".");
            UpdRecordFailure(ctx, meta, failKey, probe, stopCode, text2, status);
            if (update) { try { File.Delete(exe); } catch (IOException) { } }
            // Новая версия не встала, а прежний перевод снят откатом: возвращаем сохранённую копию текущей версии.
            if (!afterInstalled && savedExe != null && !string.Equals(savedExe, exe, StringComparison.OrdinalIgnoreCase))
            {
                if (UpdRollback(ctx, status, meta, savedExe, gameRoot, updates))
                {
                    string back, backStamp, backGame;
                    UpdReadState(ctx, out back, out backStamp, out backGame);
                    status.Installed = back;
                    throw new StopException("rolled_back", "Версия " + targetVersion + " не установилась, возвращена версия " + back + ". " + text2);
                }
            }
            if (!afterInstalled)
                throw new StopException("setup_failed", (update ? "Обновление до " + m.Version : "Возврат перевода") + " не удался, перевод сейчас снят, восстановлено состояние до его установки. " + text2);
            throw new StopException("setup_failed", (update ? "Обновление до " + m.Version : "Возврат перевода") + " не установился. " + text2);
        }

        // Возврат сохранённой версии: её Setup.exe с /UPDATE=1 сам снимет остатки и поставит перевод заново. true - state.json снова «установлен».
        static bool UpdRollback(Context ctx, UpdateStatus status, Dictionary<string, object> meta, string savedExe, string gameRoot, string updates)
        {
            status.Set("applying", "", "Возвращаю прежнюю версию перевода.");
            try
            {
                if (UpdBlocker(ctx, gameRoot) != null) { Log.Write("обновление: возврат версии отложен (игра запущена или идёт другая операция)"); return false; }
                UpdRunSetup(savedExe, updates, gameRoot, UpdLauncherInstalled());
            }
            catch (StopException stop) { Log.Write("обновление: возврат версии не удался: " + stop.Message); return false; }
            string version, stamp, game;
            bool ok = UpdReadState(ctx, out version, out stamp, out game);
            if (ok)
            {
                meta.Remove("inflight_version"); meta.Remove("inflight_game");
                UpdSaveMeta(ctx, meta);
            }
            Log.Write("обновление: возврат версии " + (ok ? "выполнен: " + version : "не удался"));
            return ok;
        }

        // Кто мешает запуску установщика: "game" (игра или лаунчер), "setup" (идёт обычный установщик), "busy" (помощник занят другой операцией) или null.
        static string UpdBlocker(Context ctx, string gameRoot)
        {
            ctx.Game = gameRoot;
            ctx.Launcher = Directory.GetParent(Util.NormPath(gameRoot)).FullName;
            // Фоновая задача ничего не закрывает: если игра или лаунчер запущены, обновление ждёт кнопки «Обновить сейчас».
            try { RequireClosed(ctx); }
            catch (StopException stop) { if (stop.Code == "running") { Log.Write("обновление: " + stop.Message); return "game"; } throw; }
            try
            {
                using (Mutex.OpenExisting(SetupMutexName)) { return "setup"; }
            }
            catch (WaitHandleCannotBeOpenedException) { }
            catch (UnauthorizedAccessException) { return "setup"; }
            // Мьютекс помощника: короткая проверка, что установка или откат не идут. Дальше Setup.exe возьмёт его сам.
            bool created;
            using (var tool = new Mutex(false, "Global\\Egoist.WXQRussian.Tool", out created))
            {
                bool free;
                try { free = tool.WaitOne(0); }
                catch (AbandonedMutexException) { free = true; }
                if (!free) return "busy";
                try { tool.ReleaseMutex(); } catch (ApplicationException) { }
            }
            return null;
        }

        static bool UpdLauncherInstalled()
        {
            try
            {
                using (var hive = Microsoft.Win32.RegistryKey.OpenBaseKey(
#if TESTBUILD
                    Microsoft.Win32.RegistryHive.CurrentUser,
#else
                    Microsoft.Win32.RegistryHive.LocalMachine,
#endif
                    Microsoft.Win32.RegistryView.Registry64))
                using (var key = hive.OpenSubKey(@"SOFTWARE\Egoist\WXQ-Russian"))
                {
                    object value = key == null ? null : key.GetValue("LauncherRU");
                    return value is int && (int)value == 1;
                }
            }
            catch (Exception) { return false; }
        }

        // Последняя остановка помощника в журнале (причина отказа Setup.exe), одной строкой.
        static string UpdLastStop(Context ctx)
        {
            try
            {
                string log = Path.Combine(ctx.Data, "install.log");
                if (!File.Exists(log)) return "";
                string last = "";
                foreach (string line in File.ReadAllLines(log, Encoding.UTF8).Reverse().Take(80))
                {
                    int at = line.IndexOf("остановка [", StringComparison.Ordinal);
                    if (at >= 0) { last = line.Substring(at); break; }
                }
                return last.Length > 300 ? last.Substring(0, 300) : last;
            }
            catch (Exception) { return ""; }
        }

        // ---- файлы рядом с помощником: переименованные при обновлении *.old ----

        // Установщик обновления не может перезаписать запущенные WXQTool.exe и WXQTray.exe, поэтому переименовывает их в *.old (PrepareToInstall).
        // Здесь (и в трее) *.old удаляются, когда рядом уже лежит новый файл; если нового нет (установка оборвалась), старый возвращается на место.
        static void UpdCleanOld()
        {
            try
            {
                string self = Process.GetCurrentProcess().MainModule.FileName;
                string dir = Path.GetDirectoryName(self);
                foreach (string name in new[] { "WXQTool.exe", "WXQTray.exe" })
                {
                    string main = Path.Combine(dir, name);
                    foreach (string old in Directory.GetFiles(dir, name + ".old*"))
                    {
                        if (string.Equals(old, self, StringComparison.OrdinalIgnoreCase)) continue;
                        try
                        {
                            if (File.Exists(main)) File.Delete(old);
                            else if (string.Equals(Path.GetFileName(old), name + ".old", StringComparison.OrdinalIgnoreCase)) File.Move(old, main);
                        }
                        catch (Exception) { }
                    }
                }
            }
            catch (Exception) { }
        }

        // ---- ключи и подпись ----

        static List<UpdateKey> UpdLoadKeys(out string baseUrl)
        {
            baseUrl = null;
            string text = null;
#if TESTBUILD
            string file = Environment.GetEnvironmentVariable("WXQ_RU_UPDATE_KEYS");
            if (!string.IsNullOrEmpty(file)) text = File.ReadAllText(file, Encoding.UTF8);
#endif
            if (text == null)
            {
                using (var stream = System.Reflection.Assembly.GetExecutingAssembly().GetManifestResourceStream("update-config.json"))
                {
                    if (stream != null)
                        using (var reader = new StreamReader(stream, Encoding.UTF8)) text = reader.ReadToEnd();
                }
            }
            var keys = new List<UpdateKey>();
            if (string.IsNullOrEmpty(text)) return keys;
            var root = new JavaScriptSerializer().DeserializeObject(text.TrimStart('﻿')) as Dictionary<string, object>;
            if (root == null) return keys;
            baseUrl = Util.Str(root, "base_url");
            object list;
            if (!root.TryGetValue("keys", out list)) return keys;
            foreach (object item in Util.Items(list))
            {
                var row = item as Dictionary<string, object>;
                if (row == null) continue;
                try
                {
                    byte[] der = UpdPemToDer(Util.Str(row, "public_key_pem") ?? "");
                    string id;
                    using (var hash = new SHA256CryptoServiceProvider()) id = Util.Hex(hash.ComputeHash(der)).Substring(0, 16);
                    string declared = Util.Str(row, "key_id");
                    if (declared != null && !string.Equals(declared, id, StringComparison.OrdinalIgnoreCase)) { Log.Write("обновление: ключ " + declared + " не совпал со своим отпечатком, пропущен"); continue; }
                    keys.Add(new UpdateKey { KeyId = id, Params = UpdParseSpki(der) });
                }
                catch (Exception error) { Log.Write("обновление: ключ не прочитан: " + error.Message); }
            }
            return keys;
        }

        static byte[] UpdPemToDer(string pem)
        {
            var match = Regex.Match(pem, "-----BEGIN PUBLIC KEY-----(.*?)-----END PUBLIC KEY-----", RegexOptions.Singleline);
            if (!match.Success) throw new FormatException("нет PEM-блока");
            return Convert.FromBase64String(Regex.Replace(match.Groups[1].Value, "\\s", ""));
        }

        static int DerLength(byte[] d, ref int p)
        {
            int b = d[p++];
            if (b < 0x80) return b;
            int count = b & 0x7F;
            if (count < 1 || count > 3) throw new FormatException("длина DER");
            int length = 0;
            for (int i = 0; i < count; i++) length = (length << 8) | d[p++];
            return length;
        }

        static void DerExpect(byte[] d, ref int p, byte tag)
        {
            if (d[p++] != tag) throw new FormatException("тег DER");
        }

        static byte[] DerInteger(byte[] d, ref int p)
        {
            DerExpect(d, ref p, 0x02);
            int length = DerLength(d, ref p);
            int end = p + length, start = p;
            if (length < 1 || end > d.Length) throw new FormatException("целое DER");
            while (start < end - 1 && d[start] == 0) start++;
            var value = new byte[end - start];
            Array.Copy(d, start, value, 0, value.Length);
            p = end;
            return value;
        }

        // SubjectPublicKeyInfo с rsaEncryption -> модуль и экспонента (в .NET Framework 4 нет ImportSubjectPublicKeyInfo).
        static RSAParameters UpdParseSpki(byte[] d)
        {
            int p = 0;
            DerExpect(d, ref p, 0x30); DerLength(d, ref p);
            DerExpect(d, ref p, 0x30);
            int algorithm = DerLength(d, ref p);
            byte[] oid = { 0x06, 0x09, 0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x01, 0x01 };   // rsaEncryption 1.2.840.113549.1.1.1
            for (int i = 0; i < oid.Length; i++) if (d[p + i] != oid[i]) throw new FormatException("ключ не RSA");
            p += algorithm;
            DerExpect(d, ref p, 0x03); DerLength(d, ref p);
            if (d[p++] != 0) throw new FormatException("битовая строка");
            DerExpect(d, ref p, 0x30); DerLength(d, ref p);
            byte[] modulus = DerInteger(d, ref p);
            byte[] exponent = DerInteger(d, ref p);
            if (modulus.Length < 256) throw new FormatException("ключ короче 2048 бит");
            return new RSAParameters { Modulus = modulus, Exponent = exponent };
        }

        // Подпись над точными байтами манифеста; успешный ключ или null. Разбор JSON — только после этого.
        static UpdateKey UpdVerify(List<UpdateKey> keys, byte[] data, byte[] signature)
        {
            foreach (var key in keys)
            {
                try
                {
                    using (var rsa = new RSACryptoServiceProvider())
                    using (var hash = new SHA256CryptoServiceProvider())
                    {
                        rsa.PersistKeyInCsp = false;
                        rsa.ImportParameters(key.Params);
                        if (rsa.VerifyData(data, hash, signature)) return key;
                    }
                }
                catch (CryptographicException) { }
            }
            return null;
        }

        // ---- манифест ----

        static StopException UpdBad(string what)
        {
            return new StopException("bad_manifest", "Манифест обновления подписан, но неправильно составлен (" + what + "). Обновление не применено.");
        }

        static Dictionary<string, object> UpdObject(Dictionary<string, object> parent, string key)
        {
            object value;
            var result = parent.TryGetValue(key, out value) ? value as Dictionary<string, object> : null;
            if (result == null) throw UpdBad("нет раздела " + key);
            return result;
        }

        static string UpdText(Dictionary<string, object> row, string key)
        {
            object value;
            string text = row.TryGetValue(key, out value) ? value as string : null;
            if (text == null) throw UpdBad("нет поля " + key);
            return text;
        }

        static long UpdNumber(Dictionary<string, object> row, string key)
        {
            object value;
            if (!row.TryGetValue(key, out value) || value == null || value is string || value is bool) throw UpdBad("нет числа " + key);
            try { return Convert.ToInt64(value, CultureInfo.InvariantCulture); }
            catch (Exception) { throw UpdBad("число " + key); }
        }

        static bool UpdParseTime(string text, out DateTime value)
        {
            value = DateTime.MinValue;
            if (string.IsNullOrEmpty(text)) return false;
            return DateTime.TryParseExact(text, "yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out value);
        }

        static UpdateManifest UpdParse(byte[] data, UpdateKey signer)
        {
            Dictionary<string, object> root;
            try
            {
                string text = new UTF8Encoding(false, true).GetString(data).TrimStart('﻿');
                root = new JavaScriptSerializer { MaxJsonLength = int.MaxValue }.DeserializeObject(text) as Dictionary<string, object>;
            }
            catch (Exception) { throw UpdBad("не JSON"); }
            if (root == null) throw UpdBad("не объект");
            if (UpdNumber(root, "schema") != 1) throw UpdBad("schema");
            if (UpdText(root, "product") != "wxq-russian") throw UpdBad("product");
            var m = new UpdateManifest();
            m.Version = UpdText(root, "version");
            if (!UpdVersionText.IsMatch(m.Version)) throw UpdBad("версия");
            if (!UpdParseTime(UpdText(root, "issued_utc"), out m.Issued)) throw UpdBad("issued_utc");
            m.KeyId = UpdText(root, "key_id");
            if (!string.Equals(m.KeyId, signer.KeyId, StringComparison.OrdinalIgnoreCase)) throw UpdBad("key_id не совпал с ключом подписи");
            object minimum;
            m.MinUpdater = root.TryGetValue("min_updater", out minimum) ? (int)Math.Min(UpdNumber(root, "min_updater"), 1000000) : 1;
            object notes;
            m.Notes = root.TryGetValue("notes", out notes) ? (notes as string ?? "") : "";
            object versions;
            if (root.TryGetValue("game_versions", out versions))
                foreach (object v in Util.Items(versions)) { string s = v as string; if (s != null) m.GameVersions.Add(s); }
            object bases;
            if (!root.TryGetValue("base_urls", out bases)) throw UpdBad("нет base_urls");
            foreach (object b in Util.Items(bases))
            {
                string url = b as string;
                if (url == null || !UpdUrlOk(url)) throw UpdBad("адрес в base_urls");
                m.Bases.Add(url);
            }
            if (m.Bases.Count == 0) throw UpdBad("пустой base_urls");
            var setup = UpdObject(root, "setup");
            m.SetupName = UpdText(setup, "name");
            if (!UpdAssetName.IsMatch(m.SetupName) || !m.SetupName.EndsWith(".exe", StringComparison.OrdinalIgnoreCase)) throw UpdBad("имя установщика");
            m.SetupBytes = UpdNumber(setup, "bytes");
            if (m.SetupBytes < 1 || m.SetupBytes > UpdMaxSetup) throw UpdBad("размер установщика");
            m.SetupSha = UpdText(setup, "sha256");
            if (!UpdSha.IsMatch(m.SetupSha)) throw UpdBad("sha256 установщика");
            return m;
        }

        // Допустимый адрес каталога загрузок: https, со слэшем в конце, без учётных данных. Тестовая сборка принимает ещё http на loopback.
        static bool UpdUrlOk(string url)
        {
            Uri uri;
            if (!Uri.TryCreate(url, UriKind.Absolute, out uri) || !url.EndsWith("/", StringComparison.Ordinal)) return false;
            if (uri.UserInfo.Length > 0) return false;
            return UpdSchemeOk(uri);
        }

        static bool UpdSchemeOk(Uri uri)
        {
            if (uri.Scheme == Uri.UriSchemeHttps) return true;
#if TESTBUILD
            if (uri.Scheme == Uri.UriSchemeHttp && uri.IsLoopback) return true;
#endif
            return false;
        }

        // Версия перевода: major.minor[.patch][-rcN] -> четыре числа. Четвёртое - номер кандидата: у версии без суффикса оно максимальное,
        // поэтому 0.6.0-rc2 < 0.6.0 и 0.6.0-rc2 < 0.6.0-rc3.
        static int[] UpdVer(string text)
        {
            var match = Regex.Match(text ?? "", @"^(\d+)\.(\d+)(?:\.(\d+))?(?:-rc(\d+))?");
            var parts = new int[4];
            if (!match.Success) { parts[3] = int.MaxValue; return parts; }
            for (int i = 0; i < 3; i++)
            {
                int n;
                parts[i] = match.Groups[i + 1].Success && int.TryParse(match.Groups[i + 1].Value, NumberStyles.None, CultureInfo.InvariantCulture, out n) ? n : 0;
            }
            int rc;
            parts[3] = match.Groups[4].Success && int.TryParse(match.Groups[4].Value, NumberStyles.None, CultureInfo.InvariantCulture, out rc) ? rc : int.MaxValue;
            return parts;
        }

        static int UpdCmp(int[] a, int[] b)
        {
            for (int i = 0; i < 4; i++) if (a[i] != b[i]) return a[i] < b[i] ? -1 : 1;
            return 0;
        }

        // Версия игры (1.5.0.1): произвольное число числовых полей; недостающие считаются нулями. null - версия не читается.
        static int[] UpdGameVer(string text)
        {
            if (string.IsNullOrEmpty(text) || !Regex.IsMatch(text, @"^\d+(\.\d+){0,5}$")) return null;
            var parts = new int[6];
            string[] pieces = text.Split('.');
            for (int i = 0; i < pieces.Length; i++)
            {
                int n;
                parts[i] = int.TryParse(pieces[i], NumberStyles.None, CultureInfo.InvariantCulture, out n) ? n : 0;
            }
            return parts;
        }

        static int UpdGameCmp(int[] a, int[] b)
        {
            for (int i = 0; i < 6; i++) if (a[i] != b[i]) return a[i] < b[i] ? -1 : 1;
            return 0;
        }

        // ---- служебные данные (update-meta.json): ETag, последняя принятая метка времени, срок повтора после сбоя ----

        static Dictionary<string, object> UpdLoadMeta(Context ctx)
        {
            try
            {
                string file = Path.Combine(ctx.Data, "update-meta.json");
                if (File.Exists(file)) return Util.ReadJson(file) ?? new Dictionary<string, object>();
            }
            catch (Exception error) { Log.Write("обновление: update-meta.json не прочитан: " + error.Message); }
            return new Dictionary<string, object>();
        }

        static void UpdSaveMeta(Context ctx, Dictionary<string, object> meta)
        {
            Util.WriteJson(Path.Combine(ctx.Data, "update-meta.json"), meta);
        }

        static void UpdWriteAtomic(string path, byte[] bytes)
        {
            string temporary = path + ".new";
            File.WriteAllBytes(temporary, bytes);
            Util.FlushToDisk(temporary);
            Util.ReplaceFile(temporary, path);
        }

        // ---- сеть ----

        static int[] UpdPauses()
        {
            if (SetupFastNetwork) return new[] { 150, 300 };
#if TESTBUILD
            int ms;
            if (int.TryParse(Environment.GetEnvironmentVariable("WXQ_RU_TEST_RETRY_MS"), NumberStyles.None, CultureInfo.InvariantCulture, out ms)) return new[] { ms, ms };
#endif
            return new[] { 10000, 60000 };   // паузы между тремя попытками: 10 с и 1 мин
        }

        // Три попытки; постоянные ошибки (4xx и т. п.) не повторяются.
        static T UpdRetry<T>(string what, Func<T> operation)
        {
            int[] pauses = UpdPauses();
            NetFail last = null;
            for (int attempt = 0; attempt < 3; attempt++)
            {
                if (SetupFastNetwork && DateTime.UtcNow >= SetupNetworkDeadline) throw new NetFail("network", "время быстрой проверки истекло", false, 0);
                if (attempt > 0) Thread.Sleep(pauses[Math.Min(attempt - 1, pauses.Length - 1)]);
                try { return operation(); }
                catch (NetFail fail)
                {
                    last = fail;
                    Log.Write("обновление: " + what + ", попытка " + (attempt + 1) + " не удалась: " + fail.Message);
                    if (fail.Permanent) throw;
                }
            }
            throw last;
        }

        static HttpWebRequest UpdRequest(string url)
        {
            ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072;   // только TLS 1.2
            ServicePointManager.Expect100Continue = false;
            var request = (HttpWebRequest)WebRequest.Create(url);
            int timeout = SetupFastNetwork ? Math.Max(100, Math.Min(2000, (int)(SetupNetworkDeadline - DateTime.UtcNow).TotalMilliseconds)) : 15000;
            request.Timeout = timeout;
            request.ReadWriteTimeout = SetupFastNetwork ? timeout : 60000;
            request.UserAgent = "WXQRussianUpdater/" + UpdaterVersion;
            request.AutomaticDecompression = DecompressionMethods.None;
            request.AllowAutoRedirect = true;
            request.MaximumAutomaticRedirections = 5;
            request.KeepAlive = false;
            return request;
        }

        static NetFail UpdWrap(WebException error)
        {
            var response = error.Response as HttpWebResponse;
            if (response == null) return new NetFail("network", error.Message, false, 0);
            int code = (int)response.StatusCode;
            bool permanent = code >= 400 && code < 500 && code != 408 && code != 429;
            return new NetFail("http", "HTTP " + code, permanent, code);
        }

        static void UpdCheckScheme(Uri uri)
        {
            if (!UpdSchemeOk(uri)) throw new NetFail("scheme", "перенаправление на небезопасный адрес", true, 0);
        }

        static UpdHttp UpdGetSmall(string url, string etag, long max)
        {
            var request = UpdRequest(url);
            if (!string.IsNullOrEmpty(etag)) request.Headers[HttpRequestHeader.IfNoneMatch] = etag;
            try
            {
                using (var response = (HttpWebResponse)request.GetResponse())
                {
                    UpdCheckScheme(response.ResponseUri);
                    if (response.StatusCode == HttpStatusCode.NotModified) return new UpdHttp { Status = 304 };
                    if (response.StatusCode != HttpStatusCode.OK) throw new NetFail("http", "HTTP " + (int)response.StatusCode, false, (int)response.StatusCode);
                    using (var stream = response.GetResponseStream())
                    using (var buffer = new MemoryStream())
                    {
                        var chunk = new byte[16384];
                        int read;
                        while ((read = stream.Read(chunk, 0, chunk.Length)) > 0)
                        {
                            buffer.Write(chunk, 0, read);
                            if (buffer.Length > max) throw new NetFail("size", "ответ больше допустимого", true, 0);
                        }
                        return new UpdHttp { Status = 200, Body = buffer.ToArray(), ETag = response.Headers[HttpResponseHeader.ETag] };
                    }
                }
            }
            catch (WebException error)
            {
                var response = error.Response as HttpWebResponse;
                if (response != null && response.StatusCode == HttpStatusCode.NotModified) { response.Close(); return new UpdHttp { Status = 304 }; }
                throw UpdWrap(error);
            }
            catch (IOException error) { throw new NetFail("network", error.Message, false, 0); }
        }

        // Докачка в WXQ-Russian-Setup-<версия>.part: если файл уже частично есть, просит Range; ответ 200 вместо 206 — начало заново.
        static void UpdFetchTo(string url, string part, long expected, UpdateStatus status)
        {
            long have = File.Exists(part) ? new FileInfo(part).Length : 0;
            if (have > expected) { File.Delete(part); have = 0; }
            if (have == expected) return;
            var request = UpdRequest(url);
            if (have > 0) request.AddRange(have);
            try
            {
                using (var response = (HttpWebResponse)request.GetResponse())
                {
                    UpdCheckScheme(response.ResponseUri);
                    bool append = false;
                    if (have > 0 && response.StatusCode == HttpStatusCode.PartialContent)
                    {
                        string range = response.Headers["Content-Range"] ?? "";
                        if (!range.StartsWith("bytes " + have.ToString(CultureInfo.InvariantCulture) + "-", StringComparison.Ordinal))
                            throw new NetFail("network", "сервер ответил не с того места", false, 0);
                        append = true;
                    }
                    else if (response.StatusCode != HttpStatusCode.OK)
                        throw new NetFail("http", "HTTP " + (int)response.StatusCode, false, (int)response.StatusCode);
                    long written = append ? have : 0;
                    using (var input = response.GetResponseStream())
                    using (var output = new FileStream(part, append ? FileMode.Append : FileMode.Create, FileAccess.Write, FileShare.None, 1 << 16))
                    {
                        var chunk = new byte[1 << 16];
                        while (true)
                        {
                            int read;
                            try { read = input.Read(chunk, 0, chunk.Length); }
                            catch (IOException error) { throw new NetFail("network", error.Message, false, 0); }
                            if (read <= 0) break;
                            written += read;
                            if (written > expected) throw new NetFail("size", "файл на сервере больше заявленного в манифесте", false, 0);
                            try { output.Write(chunk, 0, read); }
                            catch (IOException error)
                            {
                                if (Util.IsDiskFull(error)) throw new StopException("disk_space", "На диске закончилось место при скачивании обновления. Освободите место и повторите.");
                                throw;
                            }
                            status.BytesDone = written;
                            status.Tick();
                        }
                    }
                }
            }
            catch (WebException error)
            {
                var response = error.Response as HttpWebResponse;
                if (response != null && (int)response.StatusCode == 416) { try { File.Delete(part); } catch (IOException) { } }
                throw UpdWrap(error);
            }
        }

        static bool UpdFileOk(string path, UpdateManifest m)
        {
            try { return new FileInfo(path).Length == m.SetupBytes && Util.Sha(path) == m.SetupSha; }
            catch (IOException) { return false; }
        }

        // 3 попытки по всем адресам из манифеста по очереди; после полного скачивания — размер и SHA-256. Несовпадение: файл удаляется.
        static void UpdDownload(UpdateManifest m, string part, string exe, UpdateStatus status)
        {
            int[] pauses = UpdPauses();
            NetFail last = null;
            for (int attempt = 0; attempt < 3; attempt++)
            {
                if (attempt > 0) Thread.Sleep(pauses[Math.Min(attempt - 1, pauses.Length - 1)]);
                foreach (string baseUrl in m.Bases)
                {
                    try
                    {
                        UpdFetchTo(baseUrl + m.SetupName, part, m.SetupBytes, status);
                        if (new FileInfo(part).Length != m.SetupBytes) throw new NetFail("network", "соединение оборвалось до конца файла", false, 0);
                        if (Util.Sha(part) != m.SetupSha)
                        {
                            File.Delete(part);
                            throw new NetFail("sha", "SHA-256 скачанного файла не совпал с манифестом", false, 0);
                        }
                        if (File.Exists(exe)) File.Delete(exe);
                        File.Move(part, exe);
                        return;
                    }
                    catch (NetFail fail)
                    {
                        last = fail;
                        Log.Write("обновление: загрузка (попытка " + (attempt + 1) + ") не удалась: " + fail.Message);
                        if (fail.Kind == "size") { try { File.Delete(part); } catch (IOException) { } }
                    }
                }
            }
            if (last != null && last.Kind == "sha")
                throw new StopException("sha_mismatch", "SHA-256 скачанного установщика не совпал с подписанным манифестом. Файл удалён, обновление не применено.");
            throw new StopException("network", "Не удалось скачать обновление: " + (last == null ? "нет адресов" : last.Message) + ". Скачанная часть сохранена для докачки.");
        }

        // В updates\ остаются только файлы текущей загрузки (WXQ-Russian-Setup-<версия>.*) и журнал установщика.
        static void UpdPrune(string updates, string tag)
        {
            foreach (var item in new DirectoryInfo(updates).GetFileSystemInfos())
            {
                if (item.Name.StartsWith(tag + ".", StringComparison.OrdinalIgnoreCase) || item.Name.StartsWith("installed-", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(item.Name, "setup.log", StringComparison.OrdinalIgnoreCase)) continue;
                try
                {
                    if ((item.Attributes & FileAttributes.Directory) != 0 && (item.Attributes & FileAttributes.ReparsePoint) == 0) Directory.Delete(item.FullName, true);
                    else item.Delete();
                }
                catch (Exception error) { Log.Write("обновление: не удалось убрать " + item.Name + ": " + error.Message); }
            }
        }
    }

    // ---- задача планировщика автообновления --------------------------------------------------------------------------------
    // task-register / task-remove [--name <имя>] [--folder <папка>] [--exe <путь>] [--out <файл>]. Раньше это делал Pascal-скрипт установщика
    // и падал с пустым сообщением; здесь ошибка печатается с кодом HRESULT. Задача: от SYSTEM, БЕЗ триггеров (запускается только по запросу значка,
    // действие update --check-only), ниже обычного приоритета; Users - только чтение и запуск (GRGX), менять и удалять могут администраторы и SYSTEM.
    static partial class Program
    {
        const string TaskDefaultName = "WXQ-Russian Update";
        const string TaskDefaultFolder = "Egoist";
        const string TaskSddl = "D:(A;;FA;;;BA)(A;;FA;;;SY)(A;;GRGX;;;BU)";

        static string TaskErrorText(Exception error)
        {
            var inner = error;
            while (inner.InnerException != null && (inner is System.Reflection.TargetInvocationException)) inner = inner.InnerException;
            int hr = Marshal.GetHRForException(inner);
            return "0x" + ((uint)hr).ToString("X8", CultureInfo.InvariantCulture) + " " + inner.GetType().Name + ": " + inner.Message;
        }

        static bool TaskIsMissing(Exception error)
        {
            uint hr = (uint)Marshal.GetHRForException(error);
            return hr == 0x80070002u || hr == 0x80070003u || hr == 0x8004130Eu;   // файл/путь не найден
        }

        static void TaskRegister(Context ctx, Dictionary<string, string> args)
        {
            string name = args.ContainsKey("name") && args["name"].Length > 0 ? args["name"] : TaskDefaultName;
            string folderName = args.ContainsKey("folder") && args["folder"].Length > 0 ? args["folder"] : TaskDefaultFolder;
            string exe = args.ContainsKey("exe") && args["exe"].Length > 0 ? Util.NormPath(args["exe"]) : System.Reflection.Assembly.GetExecutingAssembly().Location;
            if (!File.Exists(exe)) throw new StopException("task-exe", "Для задачи автообновления нет исполняемого файла: " + exe);
            string workDir = Path.GetDirectoryName(exe);
            try
            {
                Type type = Type.GetTypeFromProgID("Schedule.Service");
                if (type == null) throw new StopException("task-com", "В системе нет службы планировщика заданий (Schedule.Service).");
                dynamic service = Activator.CreateInstance(type);
                service.Connect();
                dynamic root = service.GetFolder("\\");
                dynamic folder;
                try { folder = root.GetFolder(folderName); }
                catch (Exception) { folder = root.CreateFolder(folderName); }
                dynamic def = service.NewTask(0);
                def.RegistrationInfo.Description = "Проверка обновлений русификатора Honor of Kings ACE. Запускается только по запросу, когда вы заходите в русификатор; ничего не устанавливает и не закрывает.";
                def.Principal.UserId = "SYSTEM";
                def.Principal.LogonType = 5;    // TASK_LOGON_SERVICE_ACCOUNT
                def.Principal.RunLevel = 1;     // TASK_RUNLEVEL_HIGHEST
                dynamic settings = def.Settings;
                settings.Enabled = true;
                settings.Hidden = true;
                settings.Priority = 7;
                settings.StartWhenAvailable = true;
                settings.DisallowStartIfOnBatteries = false;
                settings.StopIfGoingOnBatteries = false;
                settings.ExecutionTimeLimit = "PT2H";
                settings.MultipleInstances = 2; // TASK_INSTANCES_IGNORE_NEW
                // Триггеров нет намеренно: проверка идёт только по запросу (schtasks /run из трея), когда человек зашёл в русификатор.
                // Определение задачи заменяется целиком (режим 6), поэтому триггеры задачи 0.6.0 (вход + каждые 6 часов) при обновлении исчезают.
                dynamic action = def.Actions.Create(0);     // TASK_ACTION_EXEC
                action.Path = exe;
                action.Arguments = "update --check-only";
                action.WorkingDirectory = workDir;
                dynamic task = folder.RegisterTaskDefinition(name, def, 6, "SYSTEM", null, 5);   // 6 = создать или обновить
                task.SetSecurityDescriptor(TaskSddl, 0);
                ctx.Result["task"] = folderName + "\\" + name;
                ctx.Result["exe"] = exe;
                Log.Write("задача автообновления зарегистрирована: " + folderName + "\\" + name + " -> " + exe);
            }
            catch (StopException) { throw; }
            catch (Exception error)
            {
                string text = TaskErrorText(error);
                Log.Write("задача автообновления не создана: " + text);
                throw new StopException("task-register", "Задача автообновления не создана: " + text);
            }
        }

        static void TaskRemove(Context ctx, Dictionary<string, string> args)
        {
            string name = args.ContainsKey("name") && args["name"].Length > 0 ? args["name"] : TaskDefaultName;
            string folderName = args.ContainsKey("folder") && args["folder"].Length > 0 ? args["folder"] : TaskDefaultFolder;
            try
            {
                Type type = Type.GetTypeFromProgID("Schedule.Service");
                if (type == null) throw new StopException("task-com", "В системе нет службы планировщика заданий (Schedule.Service).");
                dynamic service = Activator.CreateInstance(type);
                service.Connect();
                dynamic root = service.GetFolder("\\");
                dynamic folder = null;
                try { folder = root.GetFolder(folderName); }
                catch (Exception error) { if (!TaskIsMissing(error)) throw; }
                if (folder != null)
                {
                    try { folder.DeleteTask(name, 0); Log.Write("задача автообновления удалена: " + folderName + "\\" + name); }
                    catch (Exception error)
                    {
                        if (!TaskIsMissing(error)) throw;
                        Log.Write("задачи автообновления уже нет: " + folderName + "\\" + name);
                    }
                    try { root.DeleteFolder(folderName, 0); }   // удалится, только если пуста
                    catch (Exception) { }
                }
                ctx.Result["task"] = folderName + "\\" + name;
            }
            catch (StopException) { throw; }
            catch (Exception error)
            {
                string text = TaskErrorText(error);
                Log.Write("задача автообновления не удалена: " + text);
                throw new StopException("task-remove", "Задача автообновления не удалена: " + text);
            }
        }
    }
}
