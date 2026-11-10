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

    public MainWindow()
    {
        InitializeComponent();
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

        BuildPanel(WeeklyPanel, rows, "weekly");
        BuildPanel(MonthlyPanel, rows, "monthly");
        BuildScorecard();
        StatusText.Text = $"Loaded {rows.Count} rows at {DateTime.Now:HH:mm:ss}" +
                          (errors > 0 ? $" ({errors} file(s) failed to parse)" : "");
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
