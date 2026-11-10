using System.Globalization;
using System.IO;

namespace ForecastViewer;

/// <summary>Theoretical (Black-Scholes) iron condor sizing from a forecast range. No skew, no live quotes.</summary>
public static class Condor
{
    public const double Multiplier = 100;

    public sealed record Inputs(
        double Spot, double YearsToExpiry, double Volatility,
        double Rate, double DividendYield, double WingWidth,
        double P10, double P90);

    public sealed record Structure(
        string Name, double LongPut, double ShortPut, double ShortCall, double LongCall,
        double NetDelta, double Credit, double MaxLoss, double ProbBetweenShorts);

    // ---- Black-Scholes -------------------------------------------------

    private static double Cdf(double x)
    {
        // Abramowitz & Stegun 7.1.26 erf approximation (abs error < 1.5e-7).
        var z = Math.Abs(x) / Math.Sqrt(2.0);
        var t = 1.0 / (1.0 + 0.3275911 * z);
        var poly = ((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592;
        var erf = 1.0 - poly * t * Math.Exp(-z * z);
        return x >= 0 ? 0.5 * (1 + erf) : 0.5 * (1 - erf);
    }

    private static (double d1, double d2) D(Inputs i, double k)
    {
        var sd = i.Volatility * Math.Sqrt(i.YearsToExpiry);
        var d1 = (Math.Log(i.Spot / k) + (i.Rate - i.DividendYield + 0.5 * i.Volatility * i.Volatility) * i.YearsToExpiry) / sd;
        return (d1, d1 - sd);
    }

    private static double CallPrice(Inputs i, double k)
    {
        var (d1, d2) = D(i, k);
        return i.Spot * Math.Exp(-i.DividendYield * i.YearsToExpiry) * Cdf(d1)
             - k * Math.Exp(-i.Rate * i.YearsToExpiry) * Cdf(d2);
    }

    private static double PutPrice(Inputs i, double k)
    {
        var (d1, d2) = D(i, k);
        return k * Math.Exp(-i.Rate * i.YearsToExpiry) * Cdf(-d2)
             - i.Spot * Math.Exp(-i.DividendYield * i.YearsToExpiry) * Cdf(-d1);
    }

    private static double CallDelta(Inputs i, double k) =>
        Math.Exp(-i.DividendYield * i.YearsToExpiry) * Cdf(D(i, k).d1);

    private static double PutDelta(Inputs i, double k) =>
        Math.Exp(-i.DividendYield * i.YearsToExpiry) * (Cdf(D(i, k).d1) - 1);

    // ---- Structures ----------------------------------------------------

    /// <summary>Net delta in share-equivalents per 1-lot (short put and long call add delta, short call and long put remove it).</summary>
    private static double NetDelta(Inputs i, double shortPut, double shortCall) =>
        Multiplier * (-PutDelta(i, shortPut) + PutDelta(i, shortPut - i.WingWidth)
                      - CallDelta(i, shortCall) + CallDelta(i, shortCall + i.WingWidth));

    private static Structure Build(Inputs i, string name, double shortPut, double shortCall)
    {
        var longPut = shortPut - i.WingWidth;
        var longCall = shortCall + i.WingWidth;
        var credit = Multiplier * (PutPrice(i, shortPut) - PutPrice(i, longPut)
                                   + CallPrice(i, shortCall) - CallPrice(i, longCall));
        var maxLoss = Multiplier * i.WingWidth - credit;
        // Risk-neutral P(shortPut < S_T < shortCall) = N(d2 at put) - N(d2 at call).
        var prob = Cdf(D(i, shortPut).d2) - Cdf(D(i, shortCall).d2);
        return new Structure(name, longPut, shortPut, shortCall, longCall,
            NetDelta(i, shortPut, shortCall), credit, maxLoss, prob);
    }

    /// <summary>
    /// Returns (1) the plain range condor with shorts at P10/P90 rounded outward to 5 points, and
    /// (2) the nearest delta-neutral condor: of all short-strike pairs within +/-searchRange points
    /// of those, the one with |net delta| under the tolerance that moves the shorts least
    /// (or the smallest |net delta| if none qualifies).
    /// </summary>
    public static (Structure Range, Structure Neutral) Solve(Inputs i, double searchRange = 150, double tolerance = 1.0)
    {
        var putStart = Math.Floor(i.P10 / 5) * 5;
        var callStart = Math.Ceiling(i.P90 / 5) * 5;
        var range = Build(i, "Range (P10/P90)", putStart, callStart);

        double bestPut = putStart, bestCall = callStart;
        double bestDelta = double.MaxValue, bestCost = double.MaxValue;
        var foundNeutral = false;

        for (var p = putStart - searchRange; p <= putStart + searchRange; p += 5)
        {
            if (p - i.WingWidth <= 0) continue;
            for (var c = callStart - searchRange; c <= callStart + searchRange; c += 5)
            {
                if (c <= p) continue;
                var delta = Math.Abs(NetDelta(i, p, c));
                var cost = Math.Abs(p - putStart) + Math.Abs(c - callStart);
                var qualifies = delta <= tolerance;
                var better = qualifies
                    ? (!foundNeutral || cost < bestCost || (cost == bestCost && delta < bestDelta))
                    : (!foundNeutral && delta < bestDelta);
                if (!better) continue;
                if (qualifies) foundNeutral = true;
                bestPut = p; bestCall = c; bestDelta = delta; bestCost = cost;
            }
        }
        return (range, Build(i, "Delta-neutral", bestPut, bestCall));
    }

    // ---- Volatility lookup --------------------------------------------

    /// <summary>Latest close from a Cboe-style history CSV (DATE as MM/dd/yyyy, CLOSE column), as a decimal (e.g. 0.27).</summary>
    public static (double Value, DateTime Date)? LatestClose(string path)
    {
        if (!File.Exists(path)) return null;
        var rows = Csv.Read(path).ToList();
        if (rows.Count < 2) return null;
        var close = rows[0].IndexOf("CLOSE");
        var date = rows[0].IndexOf("DATE");
        if (close < 0 || date < 0) return null;

        (double, DateTime)? best = null;
        foreach (var r in rows.Skip(1))
        {
            if (r.Count <= Math.Max(close, date)) continue;
            if (!DateTime.TryParseExact(r[date], "MM/dd/yyyy", CultureInfo.InvariantCulture, DateTimeStyles.None, out var d)) continue;
            if (!double.TryParse(r[close], NumberStyles.Float, CultureInfo.InvariantCulture, out var v)) continue;
            if (best == null || d > best.Value.Item2) best = (v / 100.0, d);
        }
        return best;
    }
}
