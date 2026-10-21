// Совместимый перенос строк .lang/Global: только точный ключ и SHA исходного текста.
// Здесь нет файлов, процессов и загрузки кода; все остальные wire-байты сохраняются.
using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

namespace Egoist.WXQRussian
{
    class CompatTextRow
    {
        public string Key = null, SourceSha = null, Target = null;
    }

    class CompatTextResult
    {
        public byte[] Payload;
        public int Applied, SourceChanged, Missing, CurrentRows;
    }

    static class CompatTextMap
    {
        // Метаданные наших переводов остаются строго ограничены. Новые официальные
        // строки могут быть длиннее: сохраняем их без перевода рядом с известными.
        const int MaxRows = 32768, MaxCurrentRows = 65536, MaxFields = 300000;
        const int MaxValueBytes = 16384, MaxCurrentValueBytes = 256 * 1024, MaxKeyBytes = 512;
        static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
        static readonly Regex LangKey = new Regex("\\A#[0-9a-fA-F]{16}#\\z", RegexOptions.CultureInvariant);
        static readonly Regex Digest = new Regex("\\A[0-9a-fA-F]{64}\\z", RegexOptions.CultureInvariant);

        sealed class Frame
        {
            public int Number, Wire, Start, TagEnd, PayloadStart, End;
            public int PayloadBytes { get { return End - PayloadStart; } }
            public int RawBytes { get { return End - Start; } }
        }

        sealed class Entry
        {
            public int OuterIndex, KeyIndex, TextIndex;
            public List<Frame> Fields;
            public string Key, Text;
        }

        sealed class Document
        {
            public byte[] Bytes;
            public List<Frame> Fields;
            public readonly List<Entry> Rows = new List<Entry>();
            public readonly Dictionary<string, Entry> ByKey = new Dictionary<string, Entry>(StringComparer.Ordinal);
            public readonly Dictionary<int, Entry> ByOuter = new Dictionary<int, Entry>();
        }

        static FormatException Bad(string code) { return new FormatException("compat-text-" + code); }

        static int Limit(string codec)
        {
            if (codec == "lang-map-v1") return 128 * 1024 * 1024;
            if (codec == "global-map-v1") return 8 * 1024 * 1024;
            throw Bad("codec");
        }

        static int EncodedLength(string value, int maximum, string role)
        {
            if (value == null || value.Length > maximum) throw Bad(role + "-budget");
            try
            {
                int size = Utf8.GetByteCount(value);
                if (size > maximum) throw Bad(role + "-budget");
                return size;
            }
            catch (EncoderFallbackException) { throw Bad(role + "-utf8"); }
        }

        static byte[] Encode(string value, int maximum, string role)
        {
            EncodedLength(value, maximum, role);
            return Utf8.GetBytes(value);
        }

        static void CheckKey(string key, string codec)
        {
            int size = EncodedLength(key, MaxKeyBytes, "key");
            if (size == 0 || (codec == "lang-map-v1" && !LangKey.IsMatch(key))) throw Bad("key");
        }

        public static void ValidateRows(string codec, IList<CompatTextRow> rows)
        {
            Limit(codec);
            if (rows == null || rows.Count > MaxRows) throw Bad("metadata-budget");
            var keys = new HashSet<string>(StringComparer.Ordinal);
            foreach (var row in rows)
            {
                if (row == null) throw Bad("metadata-row");
                CheckKey(row.Key, codec);
                if (!keys.Add(row.Key)) throw Bad("metadata-duplicate-key");
                if (row.SourceSha == null || !Digest.IsMatch(row.SourceSha)) throw Bad("source-sha");
                EncodedLength(row.Target, MaxValueBytes, "target");
            }
        }

        static ulong ReadVarint(byte[] data, ref int position, int end)
        {
            ulong value = 0;
            for (int index = 0; index < 10; index++)
            {
                if (position >= end) throw Bad("truncated-varint");
                byte part = data[position++];
                if (index == 9 && part > 1) throw Bad("wide-varint");
                value |= (ulong)(part & 127) << (7 * index);
                if (part < 128) return value;
            }
            throw Bad("wide-varint");
        }

        static List<Frame> ReadFields(byte[] data, int position, int end, ref int used)
        {
            var result = new List<Frame>();
            while (position < end)
            {
                if (used >= MaxFields) throw Bad("field-budget");
                used++;
                var field = new Frame { Start = position };
                ulong tag = ReadVarint(data, ref position, end), number = tag >> 3;
                if (number == 0 || number > 536870911) throw Bad("field-number");
                field.Number = (int)number;
                field.Wire = (int)(tag & 7);
                field.TagEnd = position;
                field.PayloadStart = position;
                if (field.Wire == 0) ReadVarint(data, ref position, end);
                else if (field.Wire == 1 || field.Wire == 5)
                {
                    int count = field.Wire == 1 ? 8 : 4;
                    if (end - position < count) throw Bad("truncated-fixed");
                    position += count;
                }
                else if (field.Wire == 2)
                {
                    ulong count = ReadVarint(data, ref position, end);
                    if (count > (ulong)(end - position)) throw Bad("truncated-field");
                    field.PayloadStart = position;
                    position += (int)count;
                }
                else
                {
                    // Groups не используются этими строковыми схемами. Без знания схемы
                    // их безопаснее отклонить целиком, чем разбирать числовую таблицу.
                    throw Bad("unsupported-wire");
                }
                field.End = position;
                result.Add(field);
            }
            return result;
        }

        static string Decode(byte[] data, Frame field, int maximum, string role)
        {
            if (field.PayloadBytes > maximum) throw Bad(role + "-budget");
            try { return Utf8.GetString(data, field.PayloadStart, field.PayloadBytes); }
            catch (DecoderFallbackException) { throw Bad(role + "-utf8"); }
        }

        static Document Parse(byte[] data, string codec)
        {
            if (data == null || data.Length > Limit(codec)) throw Bad("input-budget");
            int used = 0;
            var doc = new Document { Bytes = data, Fields = ReadFields(data, 0, data.Length, ref used) };
            for (int outerIndex = 0; outerIndex < doc.Fields.Count; outerIndex++)
            {
                var outer = doc.Fields[outerIndex];
                if (outer.Number != 1) continue;
                if (outer.Wire != 2) throw Bad("root-row-wire");
                if (doc.Rows.Count >= MaxCurrentRows) throw Bad("row-budget");
                var row = new Entry { OuterIndex = outerIndex, KeyIndex = -1, TextIndex = -1,
                    Fields = ReadFields(data, outer.PayloadStart, outer.End, ref used) };
                for (int innerIndex = 0; innerIndex < row.Fields.Count; innerIndex++)
                {
                    var field = row.Fields[innerIndex];
                    if (field.Number != 1 && field.Number != 2) continue;
                    if (field.Wire != 2) throw Bad("row-known-wire");
                    if (field.Number == 1)
                    {
                        if (row.KeyIndex >= 0) throw Bad("duplicate-key-field");
                        row.KeyIndex = innerIndex;
                    }
                    else
                    {
                        if (row.TextIndex >= 0) throw Bad("duplicate-text-field");
                        row.TextIndex = innerIndex;
                    }
                }
                if (row.KeyIndex < 0 || row.TextIndex < 0) throw Bad("row-shape");
                row.Key = Decode(data, row.Fields[row.KeyIndex], MaxKeyBytes, "key");
                row.Text = Decode(data, row.Fields[row.TextIndex], MaxCurrentValueBytes, "text");
                CheckKey(row.Key, codec);
                if (doc.ByKey.ContainsKey(row.Key)) throw Bad("duplicate-key");
                doc.Rows.Add(row);
                doc.ByKey.Add(row.Key, row);
                doc.ByOuter.Add(outerIndex, row);
            }
            if (doc.Rows.Count == 0) throw Bad("no-text-rows");
            return doc;
        }

        static byte[] Varint(int value)
        {
            var bytes = new List<byte>(5);
            uint rest = (uint)value;
            while (rest >= 128) { bytes.Add((byte)((rest & 127) | 128)); rest >>= 7; }
            bytes.Add((byte)rest);
            return bytes.ToArray();
        }

        static long FrameSize(Frame old, int payloadBytes)
        {
            return (long)(old.TagEnd - old.Start) + (payloadBytes == old.PayloadBytes
                ? old.PayloadStart - old.TagEnd : Varint(payloadBytes).Length) + payloadBytes;
        }

        static void WriteRange(Stream stream, byte[] data, int start, int count) { stream.Write(data, start, count); }

        static void WritePrefix(Stream stream, byte[] data, Frame old, int payloadBytes)
        {
            WriteRange(stream, data, old.Start, old.TagEnd - old.Start);
            if (payloadBytes == old.PayloadBytes) WriteRange(stream, data, old.TagEnd, old.PayloadStart - old.TagEnd);
            else
            {
                byte[] size = Varint(payloadBytes);
                WriteRange(stream, size, 0, size.Length);
            }
        }

        static byte[] Rewrite(Document doc, Dictionary<int, byte[]> selected, int maximum)
        {
            long size = doc.Bytes.Length;
            foreach (var pair in selected)
            {
                Entry row = doc.ByOuter[pair.Key];
                Frame outer = doc.Fields[pair.Key], text = row.Fields[row.TextIndex];
                long rowBytes = (long)outer.PayloadBytes - text.RawBytes + FrameSize(text, pair.Value.Length);
                if (rowBytes > maximum) throw Bad("output-budget");
                size += FrameSize(outer, (int)rowBytes) - outer.RawBytes;
            }
            if (size < 0 || size > maximum) throw Bad("output-budget");
            using (var output = new MemoryStream((int)size))
            {
                for (int index = 0; index < doc.Fields.Count; index++)
                {
                    Frame outer = doc.Fields[index];
                    byte[] target;
                    if (!selected.TryGetValue(index, out target))
                    {
                        WriteRange(output, doc.Bytes, outer.Start, outer.RawBytes);
                        continue;
                    }
                    Entry row = doc.ByOuter[index];
                    Frame text = row.Fields[row.TextIndex];
                    int rowBytes = (int)((long)outer.PayloadBytes - text.RawBytes + FrameSize(text, target.Length));
                    WritePrefix(output, doc.Bytes, outer, rowBytes);
                    for (int inner = 0; inner < row.Fields.Count; inner++)
                    {
                        Frame field = row.Fields[inner];
                        if (inner != row.TextIndex) WriteRange(output, doc.Bytes, field.Start, field.RawBytes);
                        else
                        {
                            WritePrefix(output, doc.Bytes, field, target.Length);
                            WriteRange(output, target, 0, target.Length);
                        }
                    }
                }
                if (output.Length != size) throw Bad("output-size");
                return output.ToArray();
            }
        }

        static bool Equal(byte[] first, int start, byte[] second, int otherStart, int count)
        {
            for (int index = 0; index < count; index++)
                if (first[start + index] != second[otherStart + index]) return false;
            return true;
        }

        static void SameRaw(Document before, Frame old, Document after, Frame current)
        {
            if (old.RawBytes != current.RawBytes || !Equal(before.Bytes, old.Start, after.Bytes, current.Start, old.RawBytes)) throw Bad("unknown-field-drift");
        }

        static void SamePrefix(Document before, Frame old, Document after, Frame current)
        {
            int tagBytes = old.TagEnd - old.Start;
            if (old.Number != current.Number || old.Wire != current.Wire || tagBytes != current.TagEnd - current.Start ||
                !Equal(before.Bytes, old.Start, after.Bytes, current.Start, tagBytes)) throw Bad("tag-drift");
            if (old.PayloadBytes == current.PayloadBytes)
            {
                int lengthBytes = old.PayloadStart - old.TagEnd;
                if (lengthBytes != current.PayloadStart - current.TagEnd ||
                    !Equal(before.Bytes, old.TagEnd, after.Bytes, current.TagEnd, lengthBytes)) throw Bad("length-drift");
            }
            else
            {
                byte[] expected = Varint(current.PayloadBytes);
                if (expected.Length != current.PayloadStart - current.TagEnd ||
                    !Equal(expected, 0, after.Bytes, current.TagEnd, expected.Length)) throw Bad("length-drift");
            }
        }

        static void InversePart(byte[] expected, ref int position, byte[] bytes, int start, int count)
        {
            if (count > expected.Length - position || !Equal(expected, position, bytes, start, count)) throw Bad("inverse-drift");
            position += count;
        }

        static void Prove(Document before, Document after, Dictionary<int, byte[]> selected)
        {
            if (before.Fields.Count != after.Fields.Count || before.Rows.Count != after.Rows.Count) throw Bad("shape-drift");
            int inversePosition = 0;
            for (int index = 0; index < before.Fields.Count; index++)
            {
                Frame oldOuter = before.Fields[index], newOuter = after.Fields[index];
                Entry oldRow, newRow;
                if (!before.ByOuter.TryGetValue(index, out oldRow))
                {
                    SameRaw(before, oldOuter, after, newOuter);
                    InversePart(before.Bytes, ref inversePosition, after.Bytes, newOuter.Start, newOuter.RawBytes);
                    continue;
                }
                if (!after.ByOuter.TryGetValue(index, out newRow) || oldRow.Key != newRow.Key ||
                    oldRow.KeyIndex != newRow.KeyIndex || oldRow.TextIndex != newRow.TextIndex || oldRow.Fields.Count != newRow.Fields.Count) throw Bad("row-identity-drift");
                SamePrefix(before, oldOuter, after, newOuter);
                byte[] target;
                bool changed = selected.TryGetValue(index, out target);
                if (!changed) SameRaw(before, oldOuter, after, newOuter);
                InversePart(before.Bytes, ref inversePosition, before.Bytes, oldOuter.Start, oldOuter.PayloadStart - oldOuter.Start);
                for (int inner = 0; inner < oldRow.Fields.Count; inner++)
                {
                    Frame old = oldRow.Fields[inner], current = newRow.Fields[inner];
                    if (inner != oldRow.TextIndex)
                    {
                        SameRaw(before, old, after, current);
                        InversePart(before.Bytes, ref inversePosition, after.Bytes, current.Start, current.RawBytes);
                    }
                    else
                    {
                        SamePrefix(before, old, after, current);
                        byte[] expected = changed ? target : Encode(oldRow.Text, MaxCurrentValueBytes, "text");
                        if (current.PayloadBytes != expected.Length || !Equal(expected, 0, after.Bytes, current.PayloadStart, expected.Length)) throw Bad("text-drift");
                        InversePart(before.Bytes, ref inversePosition, before.Bytes, old.Start, old.RawBytes);
                    }
                }
            }
            if (inversePosition != before.Bytes.Length) throw Bad("inverse-size");
        }

        static string TextSha(HashAlgorithm hash, byte[] data, Frame text)
        {
            byte[] digest = hash.ComputeHash(data, text.PayloadStart, text.PayloadBytes);
            var value = new StringBuilder(64);
            foreach (byte part in digest) value.Append(part.ToString("x2", System.Globalization.CultureInfo.InvariantCulture));
            return value.ToString();
        }

        public static CompatTextResult Reuse(byte[] current, string codec, IList<CompatTextRow> translations)
        {
            ValidateRows(codec, translations);
            Document source = Parse(current, codec);
            var result = new CompatTextResult { Payload = current, CurrentRows = source.Rows.Count };
            var selected = new Dictionary<int, byte[]>();
            // Один hash-контекст на ресурс: сотни тысяч строк не создают по
            // отдельному CryptoAPI-дескриптору и target-буферу при валидации.
            using (var hash = new SHA256CryptoServiceProvider())
            {
                foreach (var item in translations)
                {
                    Entry row;
                    if (!source.ByKey.TryGetValue(item.Key, out row)) { result.Missing++; continue; }
                    if (!string.Equals(TextSha(hash, current, row.Fields[row.TextIndex]), item.SourceSha, StringComparison.OrdinalIgnoreCase))
                    {
                        result.SourceChanged++;
                        continue;
                    }
                    byte[] target = Encode(item.Target, MaxValueBytes, "target");
                    Frame text = row.Fields[row.TextIndex];
                    if (text.PayloadBytes == target.Length && Equal(current, text.PayloadStart, target, 0, target.Length)) continue;
                    selected.Add(row.OuterIndex, target);
                    result.Applied++;
                }
            }
            if (selected.Count == 0) return result;
            result.Payload = Rewrite(source, selected, Limit(codec));
            Document after = Parse(result.Payload, codec);
            Prove(source, after, selected);
            return result;
        }
    }
}
