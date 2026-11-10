using System.ComponentModel;

namespace ForecastViewer;

/// <summary>One row of the condor table, with the paper-trade checkbox.</summary>
public sealed class CondorRow : INotifyPropertyChanged
{
    private bool _paper;

    public CondorRow(Condor.Structure structure, Condor.Inputs inputs, string horizon, string expiration, DateTime asOf)
    {
        Structure = structure;
        Inputs = inputs;
        Horizon = horizon;
        Expiration = expiration;
        AsOfDate = asOf;
    }

    public Condor.Structure Structure { get; }
    public Condor.Inputs Inputs { get; }
    public string Horizon { get; }
    public string Expiration { get; }
    public DateTime AsOfDate { get; }

    public string Key => PaperTrade.MakeKey(Horizon, AsOfDate, Structure.Name,
        Structure.LongPut, Structure.ShortPut, Structure.ShortCall, Structure.LongCall);

    // Pass-throughs so the grid columns bind by the same names as Condor.Structure.
    public string Name => Structure.Name;
    public double LongPut => Structure.LongPut;
    public double ShortPut => Structure.ShortPut;
    public double ShortCall => Structure.ShortCall;
    public double LongCall => Structure.LongCall;
    public double NetDelta => Structure.NetDelta;
    public double Credit => Structure.Credit;
    public double MaxLoss => Structure.MaxLoss;
    public double LowerBreakeven => Structure.LowerBreakeven;
    public double UpperBreakeven => Structure.UpperBreakeven;
    public double ProbBetweenShorts => Structure.ProbBetweenShorts;
    public double ProbProfit => Structure.ProbProfit;

    /// <summary>Raised when the user toggles the checkbox.</summary>
    public event Action<CondorRow, bool>? PaperToggled;
    public event PropertyChangedEventHandler? PropertyChanged;

    public bool Paper
    {
        get => _paper;
        set
        {
            if (_paper == value) return;
            _paper = value;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Paper)));
            PaperToggled?.Invoke(this, value);
        }
    }

    /// <summary>Sets the checkbox without raising PaperToggled (initial state, or reverting a refused change).</summary>
    public void SetPaperSilently(bool value)
    {
        _paper = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Paper)));
    }

    public PaperTrade ToTrade() => new()
    {
        AsOfDate = AsOfDate,
        Horizon = Horizon,
        Expiration = Expiration,
        Structure = Structure.Name,
        LongPut = Structure.LongPut,
        ShortPut = Structure.ShortPut,
        ShortCall = Structure.ShortCall,
        LongCall = Structure.LongCall,
        WingWidth = Inputs.WingWidth,
        Credit = Structure.Credit,
        MaxLoss = Structure.MaxLoss,
        NetDelta = Structure.NetDelta,
        ProbProfit = Structure.ProbProfit,
        Spot = Inputs.Spot,
        Volatility = Inputs.Volatility,
        P10 = Inputs.P10,
        P90 = Inputs.P90,
    };
}
