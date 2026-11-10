using System.Data;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Data;
using System.Windows.Media;
using Microsoft.Win32;

namespace ForecastViewer;

public partial class MainWindow : Window
{
    private string? _dailyDir;
    private List<ForecastRow> _rows = new();

    public MainWindow()
    {
        InitializeComponent();
        // Sized for a 2560x1440 monitor, but derived from the work area so it also
        // fits when Windows display scaling is above 100%.
        var work = SystemParameters.WorkArea;
        Width = Math.Min(2000, work.Width * 0.85);
        Height = Math.Min(1200, work.Height * 0.88);
        _dailyDir = FindDailyDir();
        Reload();
    }

    /// <summary>Walks up from the exe folder looking for artifacts/daily.</summary>
    private static string? FindDailyDir()
    {
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir != null; dir = dir.Parent)
        {
            var candidate = Path.Combine(dir.FullName, "artifacts", "daily");
            if (Directory.Exists(candidate)) return candidate;
        }
        return null;
    }

    private void Refresh_Click(object sender, RoutedEventArgs e) => Reload();

    private void Browse_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new OpenFolderDialog { Title = "Select the artifacts/daily folder" };
        if (dialog.ShowDialog() == true)
        {
            _dailyDir = dialog.FolderName;
            Reload();
        }
    }

    private void Reload()
    {
        SourceText.Text = _dailyDir ?? "No artifacts/daily folder found - click Browse...";
        WeeklyPanel.Children.Clear();
        MonthlyPanel.Children.Clear();
        ScorecardPanel.Children.Clear();
        if (_dailyDir == null || !Directory.Exists(_dailyDir))
        {
            StatusText.Text = "No data folder.";
            return;
        }

        var rows = new List<ForecastRow>();
        var errors = 0;
        foreach (var file in Directory.EnumerateFiles(_dailyDir, "forecast_*.csv"))
        {
            try { rows.AddRange(ForecastRow.Load(file)); }
            catch (Exception) { errors++; }
        }

        _rows = rows;
        BuildPanel(WeeklyPanel, rows, "weekly");
        BuildPanel(MonthlyPanel, rows, "monthly");
        BuildScorecard();
        BuildCondor();
        StatusText.Text = $"Loaded {rows.Count} rows at {DateTime.Now:HH:mm:ss}" +
                          (errors > 0 ? $" ({errors} file(s) failed to parse)" : "");
    }

    private void Recalculate_Click(object sender, RoutedEventArgs e) => BuildCondor();

    private static bool TryParse(string text, out double value) =>
        double.TryParse(text, System.Globalization.NumberStyles.Float,
            System.Globalization.CultureInfo.InvariantCulture, out value);

    private void BuildCondor()
    {
        CondorPanel.Children.Clear();
        AddCondorSection(CondorPanel, "weekly", "VIX9D_History.csv", "VIX9D");
        AddCondorSection(CondorPanel, "monthly", "VIX_History.csv", "VIX");
    }

    /// <summary>Appends the range vs. delta-neutral condor for the latest issued forecast of a horizon.</summary>
    private void AddCondorSection(DockPanel panel, string horizon, string volFile, string volName)
    {
        void Message(string text)
        {
            var t = new TextBlock { Text = text, Foreground = Brushes.Gray, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 8) };
            DockPanel.SetDock(t, Dock.Top);
            panel.Children.Add(t);
        }

        if (!TryParse(WingBox.Text, out var wing) || wing <= 0 ||
            !TryParse(RateBox.Text, out var rate) || !TryParse(DivBox.Text, out var div))
        {
            Message("Enter valid numbers for wing width, rate and dividend yield (Iron condor tab).");
            return;
        }

        var artifacts = _dailyDir == null ? null : Directory.GetParent(_dailyDir)?.FullName;
        var imports = artifacts == null ? null : Path.Combine(Directory.GetParent(artifacts)!.FullName, "data", "imports");

        {
            var f = _rows.Where(r => r.Horizon == horizon && r.IsIssued)
                         .OrderByDescending(r => r.AsOfDate).FirstOrDefault();
            if (f == null || f.P10 is null || f.P90 is null || f.ReferenceClose is null ||
                !DateTime.TryParse(f.Expiration, out var expiry))
            {
                Message($"{char.ToUpper(horizon[0]) + horizon[1..]}: no issued forecast yet.");
                return;
            }

            var vol = imports == null ? null : Condor.LatestClose(Path.Combine(imports, volFile));
            if (vol == null)
            {
                Message($"{horizon}: {volFile} not found under data/imports.");
                return;
            }

            var days = (expiry.Date - f.AsOfDate.Date).TotalDays;
            if (days <= 0)
            {
                Message($"{horizon}: forecast is already past expiration.");
                return;
            }

            var inputs = new Condor.Inputs(f.ReferenceClose.Value, days / 365.0, vol.Value.Value,
                rate / 100, div / 100, wing, f.P10.Value, f.P90.Value);
            var (range, neutral) = Condor.Solve(inputs);

            var header = new TextBlock
            {
                Text = $"{char.ToUpper(horizon[0]) + horizon[1..]}: spot {inputs.Spot:N2}, expires {f.Expiration} ({days:0} days), " +
                       $"{volName} {vol.Value.Value:P1} (as of {vol.Value.Date:yyyy-MM-dd}), forecast range {inputs.P10:N0} - {inputs.P90:N0}",
                FontWeight = FontWeights.SemiBold,
                Margin = new Thickness(0, 4, 0, 4),
            };
            DockPanel.SetDock(header, Dock.Top);
            panel.Children.Add(header);

            var grid = new DataGrid
            {
                AutoGenerateColumns = false,
                IsReadOnly = true,
                CanUserAddRows = false,
                HeadersVisibility = DataGridHeadersVisibility.Column,
                GridLinesVisibility = DataGridGridLinesVisibility.Horizontal,
                ItemsSource = new[] { range, neutral }
                    .Concat(new[] { 0.05, 0.10, 0.15, 0.20 }.Select(d => Condor.SolveByShortDelta(inputs, d)))
                    .ToList(),
                Margin = new Thickness(0, 0, 0, 10),
            };
            grid.Columns.Add(Col("Structure", nameof(Condor.Structure.Name)));
            grid.Columns.Add(Col("Long put", nameof(Condor.Structure.LongPut), "N0"));
            grid.Columns.Add(Col("Short put", nameof(Condor.Structure.ShortPut), "N0"));
            grid.Columns.Add(Col("Short call", nameof(Condor.Structure.ShortCall), "N0"));
            grid.Columns.Add(Col("Long call", nameof(Condor.Structure.LongCall), "N0"));
            grid.Columns.Add(Col("Net delta", nameof(Condor.Structure.NetDelta), "+0.0;-0.0;0.0"));
            grid.Columns.Add(Col("Credit $", nameof(Condor.Structure.Credit), "N0"));
            grid.Columns.Add(Col("Max loss $", nameof(Condor.Structure.MaxLoss), "N0"));
            grid.Columns.Add(Col("Lower B/E", nameof(Condor.Structure.LowerBreakeven), "N0"));
            grid.Columns.Add(Col("Upper B/E", nameof(Condor.Structure.UpperBreakeven), "N0"));
            grid.Columns.Add(Col("P(between shorts)", nameof(Condor.Structure.ProbBetweenShorts), "P0"));
            grid.Columns.Add(Col("P(profit)", nameof(Condor.Structure.ProbProfit), "P0"));
            DockPanel.SetDock(grid, Dock.Top);
            panel.Children.Add(grid);
        }
    }

    private void BuildScorecard()
    {
        // live_scorecard*.csv sit in artifacts/, one level above artifacts/daily.
        var artifacts = Directory.GetParent(_dailyDir!)?.FullName;
        AddCsvSection("Summary", artifacts, "live_scorecard_summary.csv", 120);
        AddCsvSection("Issued forecasts vs. settlements", artifacts, "live_scorecard.csv", double.NaN);
    }

    private void AddCsvSection(string title, string? folder, string fileName, double height)
    {
        var header = new TextBlock
        {
            Text = title, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 4, 0, 4),
        };
        DockPanel.SetDock(header, Dock.Top);
        ScorecardPanel.Children.Add(header);

        var path = folder == null ? null : Path.Combine(folder, fileName);
        if (path == null || !File.Exists(path))
        {
            var missing = new TextBlock { Text = $"{fileName} not found.", Foreground = Brushes.Gray };
            DockPanel.SetDock(missing, Dock.Top);
            ScorecardPanel.Children.Add(missing);
            return;
        }

        var rows = Csv.Read(path).ToList();
        var table = new DataTable();
        if (rows.Count > 0)
        {
            foreach (var name in rows[0]) table.Columns.Add(name);
            foreach (var r in rows.Skip(1))
            {
                var values = new object[table.Columns.Count];
                for (var i = 0; i < values.Length; i++) values[i] = i < r.Count ? r[i] : "";
                table.Rows.Add(values);
            }
        }

        var grid = new DataGrid
        {
            IsReadOnly = true,
            AutoGenerateColumns = true,
            ItemsSource = table.DefaultView,
            CanUserAddRows = false,
            HeadersVisibility = DataGridHeadersVisibility.Column,
            GridLinesVisibility = DataGridGridLinesVisibility.Horizontal,
            AlternatingRowBackground = new SolidColorBrush(Color.FromRgb(0xF6, 0xF6, 0xF6)),
            Margin = new Thickness(0, 0, 0, 8),
        };
        grid.AutoGeneratingColumn += (_, e) =>
            e.Column.Header = e.PropertyName.Replace('_', ' ');
        if (!double.IsNaN(height))
        {
            grid.MaxHeight = height;
            DockPanel.SetDock(grid, Dock.Top);
        }
        ScorecardPanel.Children.Add(grid);
    }

    private void BuildPanel(DockPanel panel, List<ForecastRow> allRows, string horizon)
    {
        var rows = allRows.Where(r => r.Horizon == horizon)
                          .OrderByDescending(r => r.AsOfDate).ToList();
        var latest = rows.FirstOrDefault();
        var latestIssued = rows.FirstOrDefault(r => r.IsIssued);

        var card = BuildCard(latest, latestIssued);
        DockPanel.SetDock(card, Dock.Top);
        panel.Children.Add(card);

        var grid = new DataGrid
        {
            AutoGenerateColumns = false,
            IsReadOnly = true,
            ItemsSource = rows,
            HeadersVisibility = DataGridHeadersVisibility.Column,
            GridLinesVisibility = DataGridGridLinesVisibility.Horizontal,
            AlternatingRowBackground = new SolidColorBrush(Color.FromRgb(0xF6, 0xF6, 0xF6)),
            CanUserAddRows = false,
        };
        grid.Columns.Add(Col("As of", nameof(ForecastRow.AsOfDate), "yyyy-MM-dd"));
        grid.Columns.Add(Col("Status", nameof(ForecastRow.Status)));
        grid.Columns.Add(Col("Expiration", nameof(ForecastRow.Expiration)));
        grid.Columns.Add(Col("Type", nameof(ForecastRow.SettlementType)));
        grid.Columns.Add(Col("Ref close", nameof(ForecastRow.ReferenceClose), "N2"));
        grid.Columns.Add(Col("P10", nameof(ForecastRow.P10), "N2"));
        grid.Columns.Add(Col("Median", nameof(ForecastRow.Median), "N2"));
        grid.Columns.Add(Col("P90", nameof(ForecastRow.P90), "N2"));
        grid.Columns.Add(Col("P10 bps", nameof(ForecastRow.P10Bps), "N1"));
        grid.Columns.Add(Col("Median bps", nameof(ForecastRow.MedianBps), "N1"));
        grid.Columns.Add(Col("P90 bps", nameof(ForecastRow.P90Bps), "N1"));
        grid.Columns.Add(new DataGridTextColumn
        {
            Header = "Note",
            Binding = new Binding(nameof(ForecastRow.Note)),
            Width = new DataGridLength(1, DataGridLengthUnitType.Star),
        });
        panel.Children.Add(grid);
    }

    private static DataGridTextColumn Col(string header, string path, string? format = null) =>
        new()
        {
            Header = header,
            Binding = new Binding(path) { StringFormat = format },
            Width = DataGridLength.Auto,
        };

    private UIElement BuildCard(ForecastRow? latest, ForecastRow? issued)
    {
        var border = new Border
        {
            BorderBrush = new SolidColorBrush(Color.FromRgb(0xCC, 0xCC, 0xCC)),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(4),
            Padding = new Thickness(12),
            Margin = new Thickness(0, 0, 0, 10),
        };

        if (issued == null)
        {
            border.Child = new TextBlock
            {
                Text = latest == null ? "No forecasts found." : $"No issued forecast yet. {latest.Note}",
                TextWrapping = TextWrapping.Wrap,
            };
            return border;
        }

        var stack = new StackPanel();
        var newerRun = latest != null && latest != issued;
        stack.Children.Add(new TextBlock
        {
            Text = $"Latest issued: {issued.AsOfDate:yyyy-MM-dd}, expires {issued.Expiration} ({issued.SettlementType})" +
                   (newerRun ? $"   |   Newer run {latest!.AsOfDate:yyyy-MM-dd}: {latest.Status}" : ""),
            FontWeight = FontWeights.SemiBold,
            Margin = new Thickness(0, 0, 0, 8),
        });

        var values = new UniformGrid { Columns = 4 };
        values.Children.Add(Stat("Reference close", F(issued.ReferenceClose), null));
        values.Children.Add(Stat("P10", F(issued.P10), Bps(issued.P10Bps)));
        values.Children.Add(Stat("Median", F(issued.Median), Bps(issued.MedianBps)));
        values.Children.Add(Stat("P90", F(issued.P90), Bps(issued.P90Bps)));
        stack.Children.Add(values);
        border.Child = stack;
        return border;
    }

    private static string F(double? v) => v?.ToString("N2") ?? "-";
    private static string Bps(double? v) => v == null ? "" : $"{v:+0.0;-0.0} bps";

    private UIElement Stat(string label, string value, string? sub)
    {
        var sp = new StackPanel();
        sp.Children.Add(new TextBlock { Text = label, Style = (Style)FindResource("CardLabel") });
        sp.Children.Add(new TextBlock { Text = value, Style = (Style)FindResource("CardValue") });
        if (sub != null) sp.Children.Add(new TextBlock { Text = sub, Foreground = Brushes.Gray });
        return sp;
    }
}
