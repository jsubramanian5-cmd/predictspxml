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
    private List<PaperTrade> _trades = new();
    private Dictionary<string, Settlement> _settlements = new();

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
        _trades = PaperTradeStore.Load(TradesPath());
        _settlements = LoadSettlements();
        BuildPanel(WeeklyPanel, rows, "weekly");
        BuildPanel(MonthlyPanel, rows, "monthly");
        BuildScorecard();
        BuildCondor();
        BuildJournal();
        StatusText.Text = $"Loaded {rows.Count} rows at {DateTime.Now:HH:mm:ss}" +
                          (errors > 0 ? $" ({errors} file(s) failed to parse)" : "");
    }

    // Reload (not just BuildCondor) so the condor shown on the Monthly tab picks up the new inputs too.
    private void Recalculate_Click(object sender, RoutedEventArgs e) => Reload();

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

            var structures = new[] { range, neutral }
                .Concat(new[] { 0.05, 0.10, 0.15, 0.20 }.Select(d => Condor.SolveByShortDelta(inputs, d)));
            var condorRows = structures
                .Select(s => new CondorRow(s, inputs, horizon, f.Expiration, f.AsOfDate))
                .ToList();
            foreach (var row in condorRows)
            {
                row.SetPaperSilently(_trades.Any(t => t.Key == row.Key));
                row.PaperToggled += OnPaperToggled;
            }

            // Not read-only overall so the Paper checkbox can be ticked; every other column is read-only.
            var grid = new DataGrid
            {
                AutoGenerateColumns = false,
                IsReadOnly = false,
                CanUserAddRows = false,
                CanUserDeleteRows = false,
                HeadersVisibility = DataGridHeadersVisibility.Column,
                GridLinesVisibility = DataGridGridLinesVisibility.Horizontal,
                ItemsSource = condorRows,
                Margin = new Thickness(0, 0, 0, 10),
            };
            var checkbox = new FrameworkElementFactory(typeof(CheckBox));
            checkbox.SetBinding(CheckBox.IsCheckedProperty, new Binding(nameof(CondorRow.Paper))
            {
                Mode = BindingMode.TwoWay,
                UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged,
            });
            checkbox.SetValue(HorizontalAlignmentProperty, HorizontalAlignment.Center);
            checkbox.SetValue(VerticalAlignmentProperty, VerticalAlignment.Center);
            grid.Columns.Add(new DataGridTemplateColumn
            {
                Header = "Paper",
                CellTemplate = new DataTemplate { VisualTree = checkbox },
            });
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

    // ---- Paper trading -------------------------------------------------

    /// <summary>data/paper_trades.json, next to data/imports (the repo root is the parent of artifacts/).</summary>
    private string? TradesPath()
    {
        var artifacts = _dailyDir == null ? null : Directory.GetParent(_dailyDir)?.FullName;
        var root = artifacts == null ? null : Directory.GetParent(artifacts)?.FullName;
        return root == null ? null : Path.Combine(root, "data", "paper_trades.json");
    }

    private static string SettlementKey(string horizon, string expiration) => $"{horizon}|{expiration}";

    /// <summary>Settled levels from artifacts/live_scorecard.csv, keyed by horizon and expiration.</summary>
    private Dictionary<string, Settlement> LoadSettlements()
    {
        var result = new Dictionary<string, Settlement>();
        var artifacts = _dailyDir == null ? null : Directory.GetParent(_dailyDir)?.FullName;
        var path = artifacts == null ? null : Path.Combine(artifacts, "live_scorecard.csv");
        if (path == null || !File.Exists(path)) return result;

        var rows = Csv.Read(path).ToList();
        if (rows.Count < 2) return result;
        var header = rows[0];
        int horizon = header.IndexOf("horizon"), expiration = header.IndexOf("expiration"),
            actual = header.IndexOf("actual_level"), source = header.IndexOf("actual_source");
        if (horizon < 0 || expiration < 0 || actual < 0) return result;

        foreach (var r in rows.Skip(1))
        {
            if (r.Count <= Math.Max(Math.Max(horizon, expiration), actual)) continue;
            if (!double.TryParse(r[actual], System.Globalization.NumberStyles.Float,
                    System.Globalization.CultureInfo.InvariantCulture, out var level)) continue;
            result[SettlementKey(r[horizon], r[expiration])] =
                new Settlement(level, source >= 0 && source < r.Count ? r[source] : "");
        }
        return result;
    }

    private void SaveTrades()
    {
        var path = TradesPath();
        if (path == null) { StatusText.Text = "Cannot save paper trades: no data folder."; return; }
        try { PaperTradeStore.Save(path, _trades); }
        catch (Exception ex) { StatusText.Text = $"Could not save paper trades: {ex.Message}"; }
    }

    private void OnPaperToggled(CondorRow row, bool isChecked)
    {
        if (isChecked)
        {
            if (!_trades.Any(t => t.Key == row.Key)) _trades.Add(row.ToTrade());
        }
        else
        {
            var existing = _trades.FirstOrDefault(t => t.Key == row.Key);
            if (existing != null)
            {
                if (_settlements.ContainsKey(SettlementKey(existing.Horizon, existing.Expiration)))
                {
                    MessageBox.Show(this, "This trade has already settled. Use Delete selected on the Paper trades tab if you really want to remove it.",
                        "Paper trade", MessageBoxButton.OK, MessageBoxImage.Information);
                    Dispatcher.BeginInvoke(new Action(() => row.SetPaperSilently(true)));
                    return;
                }
                _trades.Remove(existing);
            }
        }
        SaveTrades();
        // The same condor can appear on more than one tab, so rebuild everything once this click has finished.
        Dispatcher.BeginInvoke(new Action(Reload));
    }

    private void BuildJournal()
    {
        PaperPanel.Children.Clear();
        void Add(UIElement element, Dock dock)
        {
            DockPanel.SetDock(element, dock);
            PaperPanel.Children.Add(element);
        }

        var rows = _trades.OrderByDescending(t => t.AsOfDate).ThenBy(t => t.Horizon).ThenBy(t => t.Structure)
            .Select(t => new PaperTradeRow(t, _settlements.GetValueOrDefault(SettlementKey(t.Horizon, t.Expiration))))
            .ToList();
        var settled = rows.Where(r => r.IsSettled).ToList();

        if (rows.Count == 0)
        {
            Add(new TextBlock
            {
                Text = "No paper trades yet. Tick the Paper box on a condor in the Iron condor tab to start your journal.",
                Foreground = Brushes.Gray, TextWrapping = TextWrapping.Wrap,
            }, Dock.Top);
            return;
        }

        // Overall summary.
        var wins = settled.Count(r => r.Pnl > 0);
        var totalPnl = settled.Sum(r => r.Pnl ?? 0);
        var forecasts = settled.GroupBy(r => (r.Horizon, r.Expiration, r.AsOfDate)).ToList();
        var forecastHits = forecasts.Count(g => g.First().InsideForecast == "Yes");
        var withFill = rows.Where(r => r.Slippage != null).ToList();
        var fillNote = withFill.Count == 0
            ? "Using model credits (enter a Fill credit to use real quotes).   "
            : $"{withFill.Count} trade(s) use your fill credit (avg slippage {withFill.Average(r => r.Slippage!.Value):+$#,##0;-$#,##0;$0}).   ";
        var summary = $"{rows.Count} trades: {settled.Count} settled, {rows.Count - settled.Count} open.   " + fillNote +
                      (settled.Count == 0
                          ? "Results appear here once the live scorecard records a settlement."
                          : $"Wins {wins}/{settled.Count} ({(double)wins / settled.Count:P0}),  total P&L {totalPnl:+$#,##0;-$#,##0;$0},  " +
                            $"average {totalPnl / settled.Count:+$#,##0;-$#,##0;$0}.   " +
                            $"Forecast range held (P10-P90) in {forecastHits}/{forecasts.Count} settled forecasts.");
        Add(new TextBlock
        {
            Text = summary, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap,
            Margin = new Thickness(0, 0, 0, 8),
        }, Dock.Top);

        // Per-structure results: shows which way of choosing a condor is working.
        var stats = rows.GroupBy(r => r.Structure).Select(g =>
        {
            var s = g.Where(r => r.IsSettled).ToList();
            var w = s.Count(r => r.Pnl > 0);
            var pnl = s.Sum(r => r.Pnl ?? 0);
            var slips = g.Where(r => r.Slippage != null).Select(r => r.Slippage!.Value).ToList();
            return new StructureStat(g.Key, g.Count(), s.Count, w,
                s.Count == 0 ? null : (double)w / s.Count, pnl, s.Count == 0 ? null : pnl / s.Count,
                slips.Count == 0 ? null : slips.Average());
        }).OrderBy(x => x.Structure).ToList();

        Add(new TextBlock { Text = "By structure", FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 4) }, Dock.Top);
        var statGrid = NewReadOnlyGrid(stats);
        statGrid.MaxHeight = 220;
        statGrid.Columns.Add(Col("Structure", nameof(StructureStat.Structure)));
        statGrid.Columns.Add(Col("Trades", nameof(StructureStat.Trades)));
        statGrid.Columns.Add(Col("Settled", nameof(StructureStat.Settled)));
        statGrid.Columns.Add(Col("Wins", nameof(StructureStat.Wins)));
        statGrid.Columns.Add(Col("Win rate", nameof(StructureStat.WinRate), "P0"));
        statGrid.Columns.Add(Col("Total P&L $", nameof(StructureStat.TotalPnl), "+#,##0;-#,##0;0"));
        statGrid.Columns.Add(Col("Avg P&L $", nameof(StructureStat.AveragePnl), "+#,##0;-#,##0;0"));
        statGrid.Columns.Add(Col("Avg slippage $", nameof(StructureStat.AverageSlippage), "+#,##0;-#,##0;0"));
        statGrid.Margin = new Thickness(0, 0, 0, 10);
        Add(statGrid, Dock.Top);

        // Buttons + journal.
        var delete = new Button { Content = "Delete selected", Padding = new Thickness(10, 3, 10, 3), HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 0, 0, 6) };
        Add(new TextBlock { Text = "Journal (edit the Notes column to record why you picked a trade)", FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 4) }, Dock.Top);
        Add(delete, Dock.Top);

        var journal = NewReadOnlyGrid(rows);
        journal.IsReadOnly = false;
        journal.SelectionMode = DataGridSelectionMode.Extended;
        journal.Columns.Add(Col("Picked", nameof(PaperTradeRow.AsOfDate), "yyyy-MM-dd"));
        journal.Columns.Add(Col("Horizon", nameof(PaperTradeRow.Horizon)));
        journal.Columns.Add(Col("Expiration", nameof(PaperTradeRow.Expiration)));
        journal.Columns.Add(Col("Structure", nameof(PaperTradeRow.Structure)));
        journal.Columns.Add(Col("Strikes", nameof(PaperTradeRow.Strikes)));
        journal.Columns.Add(Col("Model credit $", nameof(PaperTradeRow.Credit), "N0"));
        journal.Columns.Add(new DataGridTextColumn
        {
            Header = "Fill credit $ (edit)",
            Binding = new Binding(nameof(PaperTradeRow.FillCredit))
            {
                Mode = BindingMode.TwoWay,
                StringFormat = "N0",
                UpdateSourceTrigger = UpdateSourceTrigger.LostFocus,
                ValidatesOnExceptions = true,
            },
            Width = DataGridLength.Auto,
            IsReadOnly = false,
        });
        journal.Columns.Add(Col("Slippage $", nameof(PaperTradeRow.Slippage), "+#,##0;-#,##0;0"));
        journal.Columns.Add(Col("Max loss $", nameof(PaperTradeRow.MaxLoss), "N0"));
        journal.Columns.Add(Col("P(profit)", nameof(PaperTradeRow.ProbProfit), "P0"));
        journal.Columns.Add(Col("Settled at", nameof(PaperTradeRow.SettleLevel), "N2"));
        journal.Columns.Add(Col("Source", nameof(PaperTradeRow.SettleSource)));
        journal.Columns.Add(Col("Model P&L $", nameof(PaperTradeRow.ModelPnl), "+#,##0;-#,##0;0"));
        journal.Columns.Add(Col("P&L $", nameof(PaperTradeRow.Pnl), "+#,##0;-#,##0;0"));
        journal.Columns.Add(Col("Result", nameof(PaperTradeRow.Result)));
        journal.Columns.Add(Col("In shorts", nameof(PaperTradeRow.InsideShorts)));
        journal.Columns.Add(Col("In forecast", nameof(PaperTradeRow.InsideForecast)));
        journal.Columns.Add(new DataGridTextColumn
        {
            Header = "Notes",
            Binding = new Binding(nameof(PaperTradeRow.Notes)) { Mode = BindingMode.TwoWay, UpdateSourceTrigger = UpdateSourceTrigger.LostFocus },
            Width = new DataGridLength(1, DataGridLengthUnitType.Star),
            IsReadOnly = false,
        });
        // The Notes setter runs when the edit commits, which is after this event, so save once it has.
        // A fill-credit edit also changes P&L, results and the summary, so rebuild the journal after it.
        journal.CellEditEnding += (_, e) =>
        {
            var fillEdited = e.EditAction == DataGridEditAction.Commit &&
                             Equals(e.Column.Header, "Fill credit $ (edit)");
            Dispatcher.BeginInvoke(new Action(() =>
            {
                SaveTrades();
                if (fillEdited) BuildJournal();
            }), System.Windows.Threading.DispatcherPriority.Background);
        };

        delete.Click += (_, _) =>
        {
            var selected = journal.SelectedItems.OfType<PaperTradeRow>().ToList();
            if (selected.Count == 0) return;
            if (MessageBox.Show(this, $"Delete {selected.Count} paper trade(s)? This cannot be undone.",
                    "Paper trade", MessageBoxButton.YesNo, MessageBoxImage.Warning) != MessageBoxResult.Yes) return;
            foreach (var s in selected) _trades.Remove(s.Trade);
            SaveTrades();
            Reload();
        };
        PaperPanel.Children.Add(journal);
    }

    private static DataGrid NewReadOnlyGrid(System.Collections.IEnumerable items) => new()
    {
        AutoGenerateColumns = false,
        IsReadOnly = true,
        CanUserAddRows = false,
        HeadersVisibility = DataGridHeadersVisibility.Column,
        GridLinesVisibility = DataGridGridLinesVisibility.Horizontal,
        AlternatingRowBackground = new SolidColorBrush(Color.FromRgb(0xF6, 0xF6, 0xF6)),
        ItemsSource = items,
    };

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

        if (horizon == "monthly")
            AddCondorSection(panel, "monthly", "VIX_History.csv", "VIX");

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
            Binding = new Binding(path) { StringFormat = format, Mode = BindingMode.OneWay },
            Width = DataGridLength.Auto,
            IsReadOnly = true,
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
