using System.Globalization;
using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace ForecastViewer;

/// <summary>A condor the user picked for paper trading. Stored as picked; results are computed from settlements at display time.</summary>
public sealed class PaperTrade
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N");
    public DateTime AsOfDate { get; set; }
    public string Horizon { get; set; } = "";
    public string Expiration { get; set; } = "";
    public string Structure { get; set; } = "";
    public double LongPut { get; set; }
    public double ShortPut { get; set; }
    public double ShortCall { get; set; }
    public double LongCall { get; set; }
    public double WingWidth { get; set; }
    /// <summary>Model credit in dollars per 1-lot at the time of the pick.</summary>
    public double Credit { get; set; }
    public double MaxLoss { get; set; }
    public double NetDelta { get; set; }
    public double ProbProfit { get; set; }
    public double Spot { get; set; }
    public double Volatility { get; set; }
    /// <summary>Forecast P10/P90 at the time of the pick, kept so the forecast itself can be graded later.</summary>
    public double P10 { get; set; }
    public double P90 { get; set; }
    public string Notes { get; set; } = "";

    [JsonIgnore]
    public string Key => MakeKey(Horizon, AsOfDate, Structure, LongPut, ShortPut, ShortCall, LongCall);

    public static string MakeKey(string horizon, DateTime asOf, string structure,
        double longPut, double shortPut, double shortCall, double longCall) =>
        string.Create(CultureInfo.InvariantCulture,
            $"{horizon}|{asOf:yyyy-MM-dd}|{structure}|{longPut}|{shortPut}|{shortCall}|{longCall}");

    /// <summary>Expiry P&amp;L in dollars per 1-lot: the credit minus any loss on the put and call spreads.</summary>
    public double PnlAt(double settlement)
    {
        var putLoss = Math.Clamp(ShortPut - settlement, 0, ShortPut - LongPut);
        var callLoss = Math.Clamp(settlement - ShortCall, 0, LongCall - ShortCall);
        return Credit - Condor.Multiplier * (putLoss + callLoss);
    }
}

public static class PaperTradeStore
{
    private static readonly JsonSerializerOptions Options = new() { WriteIndented = true };

    public static List<PaperTrade> Load(string? path)
    {
        if (path == null || !File.Exists(path)) return new();
        try
        {
            return JsonSerializer.Deserialize<List<PaperTrade>>(File.ReadAllText(path)) ?? new();
        }
        catch (JsonException)
        {
            // Keep the unreadable file rather than silently overwriting it on the next save.
            File.Copy(path, path + ".corrupt", overwrite: true);
            return new();
        }
    }

    public static void Save(string path, List<PaperTrade> trades)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, JsonSerializer.Serialize(trades, Options));
    }
}

/// <summary>A settlement taken from artifacts/live_scorecard.csv.</summary>
public sealed record Settlement(double Level, string Source);

/// <summary>Journal row: a trade plus its outcome once a settlement exists.</summary>
public sealed class PaperTradeRow
{
    public PaperTradeRow(PaperTrade trade, Settlement? settlement)
    {
        Trade = trade;
        Settlement = settlement;
    }

    public PaperTrade Trade { get; }
    public Settlement? Settlement { get; }

    public DateTime AsOfDate => Trade.AsOfDate;
    public string Horizon => Trade.Horizon;
    public string Expiration => Trade.Expiration;
    public string Structure => Trade.Structure;
    public string Strikes => $"{Trade.LongPut:0}/{Trade.ShortPut:0} - {Trade.ShortCall:0}/{Trade.LongCall:0}";
    public double Credit => Trade.Credit;
    public double MaxLoss => Trade.MaxLoss;
    public double ProbProfit => Trade.ProbProfit;

    public bool IsSettled => Settlement != null;
    public double? SettleLevel => Settlement?.Level;
    public string SettleSource => Settlement?.Source ?? "";
    public double? Pnl => Settlement == null ? null : Trade.PnlAt(Settlement.Level);
    public string Result => Pnl is null ? "Open" : Pnl > 0 ? "Win" : "Loss";

    public string InsideShorts => Settlement == null ? "" :
        Settlement.Level >= Trade.ShortPut && Settlement.Level <= Trade.ShortCall ? "Yes" : "No";

    /// <summary>Did the settlement land inside the forecast's own P10-P90 range?</summary>
    public string InsideForecast => Settlement == null ? "" :
        Settlement.Level >= Trade.P10 && Settlement.Level <= Trade.P90 ? "Yes" : "No";

    public string Notes
    {
        get => Trade.Notes;
        set => Trade.Notes = value ?? "";
    }
}

public sealed record StructureStat(
    string Structure, int Trades, int Settled, int Wins, double? WinRate, double TotalPnl, double? AveragePnl);
