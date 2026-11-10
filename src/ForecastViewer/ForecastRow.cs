using System.Globalization;

namespace ForecastViewer;

/// <summary>One row of artifacts/daily/forecast_YYYY-MM-DD.csv.</summary>
public sealed class ForecastRow
{
    public DateTime AsOfDate { get; init; }
    public string Horizon { get; init; } = "";
    public string Status { get; init; } = "";
    public string Note { get; init; } = "";
    public string ReferenceDate { get; init; } = "";
    public string Expiration { get; init; } = "";
    public string SettlementType { get; init; } = "";
    public double? ReferenceClose { get; init; }
    public double? P10 { get; init; }
    public double? Median { get; init; }
    public double? P90 { get; init; }
    public double? P10Bps { get; init; }
    public double? MedianBps { get; init; }
    public double? P90Bps { get; init; }

    public bool IsIssued => Status == "issued";

    private static double? Num(string s) =>
        double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out var v) ? v : null;

    public static IEnumerable<ForecastRow> Load(string path)
    {
        var lines = Csv.Read(path).ToList();
        if (lines.Count < 2) yield break;
        var col = lines[0].Select((name, i) => (name, i)).ToDictionary(x => x.name, x => x.i);
        string Get(List<string> r, string name) =>
            col.TryGetValue(name, out var i) && i < r.Count ? r[i] : "";

        foreach (var r in lines.Skip(1))
        {
            if (!DateTime.TryParse(Get(r, "as_of_date"), CultureInfo.InvariantCulture,
                    DateTimeStyles.None, out var asOf)) continue;
            yield return new ForecastRow
            {
                AsOfDate = asOf,
                Horizon = Get(r, "horizon"),
                Status = Get(r, "forecast_status"),
                Note = Get(r, "note"),
                ReferenceDate = Get(r, "reference_date"),
                Expiration = Get(r, "expiration"),
                SettlementType = Get(r, "settlement_type"),
                ReferenceClose = Num(Get(r, "reference_close")),
                P10 = Num(Get(r, "p10_level")),
                Median = Num(Get(r, "median_level")),
                P90 = Num(Get(r, "p90_level")),
                P10Bps = Num(Get(r, "p10_bps")),
                MedianBps = Num(Get(r, "median_bps")),
                P90Bps = Num(Get(r, "p90_bps")),
            };
        }
    }
}
