using System.IO;
using System.Text;

namespace ForecastViewer;

/// <summary>Minimal RFC 4180 CSV reader (handles quoted fields containing commas).</summary>
public static class Csv
{
    public static IEnumerable<List<string>> Read(string path)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
        using var reader = new StreamReader(stream, Encoding.UTF8);
        var fields = new List<string>();
        var sb = new StringBuilder();
        bool quoted = false;
        int c;
        while ((c = reader.Read()) != -1)
        {
            char ch = (char)c;
            if (quoted)
            {
                if (ch == '"')
                {
                    if (reader.Peek() == '"') { sb.Append('"'); reader.Read(); }
                    else quoted = false;
                }
                else sb.Append(ch);
            }
            else if (ch == '"') quoted = true;
            else if (ch == ',') { fields.Add(sb.ToString()); sb.Clear(); }
            else if (ch == '\n' || ch == '\r')
            {
                if (ch == '\r' && reader.Peek() == '\n') reader.Read();
                fields.Add(sb.ToString());
                sb.Clear();
                if (fields.Count > 1 || fields[0].Length > 0) yield return fields;
                fields = new List<string>();
            }
            else sb.Append(ch);
        }
        if (sb.Length > 0 || fields.Count > 0)
        {
            fields.Add(sb.ToString());
            yield return fields;
        }
    }
}
