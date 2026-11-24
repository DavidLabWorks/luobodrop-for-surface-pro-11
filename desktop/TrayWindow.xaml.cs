using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using Microsoft.Win32;
using Forms = System.Windows.Forms;
using Drawing = System.Drawing;
using IOP = System.IO.Path;

namespace AirDropDesktop;

public partial class TrayWindow : Window
{
    private readonly string root;
    private readonly bool admin;
    private readonly ShellTrayIcon tray;
    private readonly Forms.ContextMenuStrip trayMenu;
    private readonly Drawing.Icon trayIcon;
    private readonly DispatcherTimer speedTimer;
    private readonly DeviceTile ipad = new("iPad");
    private TransferProcess? transfer;
    private string? pendingFile;
    private string? selectedFile;
    private string? sendAfterDriverCheck;
    private bool driverChecked;
    private string? previousFile;
    private string? offerId;
    private string? logPath;
    private string? failure;
    private bool listening;
    private bool ready;
    private bool closing;
    private bool showingDialog;
    private bool stopping;
    private bool accepting;
    private bool movingData;
    private bool completed;
    private bool expired;
    private DateTime lastProgress;
    private string? wlanSummary;

    public TrayWindow(string root, bool admin)
    {
        this.root = root;
        this.admin = admin;
        InitializeComponent();
        DeviceList.ItemsSource = new[] { ipad };
        ComputerLabel.Text = $"{Environment.MachineName}  ·  iPad";
        ElevateButton.Visibility = admin ? Visibility.Collapsed : Visibility.Visible;
        ApplyTheme();

        trayIcon = Drawing.Icon.ExtractAssociatedIcon(Environment.ProcessPath!) ??
                   (Drawing.Icon)Drawing.SystemIcons.Application.Clone();
        trayMenu = new Forms.ContextMenuStrip();
        trayMenu.Items.Add("打开隔空投送", null, (_, _) => ShowPanel());
        trayMenu.Items.Add("打开收件文件夹", null, (_, _) => OpenFolder(IOP.Combine(root, "received")));
        trayMenu.Items.Add("任务栏图标设置", null, (_, _) => OpenPath("ms-settings:taskbar"));
        trayMenu.Items.Add("退出", null, (_, _) => Quit());
        tray = new ShellTrayIcon(this, trayIcon);
        tray.Activated += TogglePanel;
        tray.MenuRequested += point =>
        {
            if (showingDialog || closing)
                return;
            Hide();
            trayMenu.Show(point);
        };
        Deactivated += (_, _) =>
        {
            // A click on our own icon closes through the tray callback.
            if (IsVisible && !showingDialog && !tray.PointerIsOnIcon())
                Hide();
        };

        speedTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(400) };
        speedTimer.Tick += (_, _) =>
        {
            if (movingData && (DateTime.UtcNow - lastProgress).TotalSeconds > 1)
                SpeedLabel.Text = "0.0 M/S";
        };
        speedTimer.Start();
        SystemEvents.UserPreferenceChanged += OnPreferenceChanged;
        Loaded += async (_, _) => await CheckDriver();
        RefreshControls();
    }

    internal void ShowPanel()
    {
        if (closing)
            return;
        if (!IsVisible)
            Show();
        UpdateLayout();
        PositionPanel();
        Activate();
    }

    protected override void OnRenderSizeChanged(SizeChangedInfo sizeInfo)
    {
        base.OnRenderSizeChanged(sizeInfo);
        if (IsVisible)
            PositionPanel();
    }

    private void TogglePanel()
    {
        if (showingDialog || closing)
            return;
        if (IsVisible)
            Hide();
        else
            ShowPanel();
    }

    private void PositionPanel()
    {
        var dpi = VisualTreeHelper.GetDpi(this);
        var size = new Drawing.Size((int)Math.Ceiling(ActualWidth * dpi.DpiScaleX),
                                    (int)Math.Ceiling(ActualHeight * dpi.DpiScaleY));
        if (tray.PlacePanel(size))
            return;
        Rect area = SystemParameters.WorkArea;
        Left = area.Right - ActualWidth - 14;
        Top = Math.Max(area.Top + 12, area.Bottom - ActualHeight - 14);
    }

    private void OnPreferenceChanged(object sender, UserPreferenceChangedEventArgs e)
        => Dispatcher.BeginInvoke(new Action(ApplyTheme));

    private void ApplyTheme()
    {
        using var skin = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");
        bool dark = skin?.GetValue("AppsUseLightTheme") is 0;
        SetBrush("Panel", dark ? "#202225" : "#FFFFFF");
        SetBrush("Card", dark ? "#2A2D31" : "#F3F5F7");
        SetBrush("Control", dark ? "#34383E" : "#EDF0F4");
        SetBrush("Ink", dark ? "#F3F5F7" : "#202630");
        SetBrush("Muted", dark ? "#BAC2CC" : "#4C5664");
        SetBrush("Line", dark ? "#454C55" : "#DFE5ED");
        SetBrush("AccentText", dark ? "#75BCFF" : "#0060B9");
    }

    private void SetBrush(string name, string color)
    {
        var brush = (SolidColorBrush)new BrushConverter().ConvertFromString(color)!;
        brush.Freeze();
        Resources[name] = brush;
    }

    private async Task CheckDriver()
    {
        try
        {
            var start = TransferProcess.PowerShell(root, "test-wificx.ps1");
            start.ArgumentList.Add("-Status");
            using var process = Process.Start(start)!;
            var read = process.StandardOutput.ReadToEndAsync();
            var errors = process.StandardError.ReadToEndAsync();
            await Task.WhenAll(read, errors, process.WaitForExitAsync());
            string report = read.Result;
            ready = process.ExitCode == 0 &&
                report.Contains("Driver binding: Coexistence", StringComparison.Ordinal) &&
                report.Contains("PnP problem code: 0", StringComparison.Ordinal);
            StatusLabel.Text = ready
                ? (report.Contains("Media connection state: 1", StringComparison.Ordinal)
                    ? "MT7922 共存驱动已就绪 · Wi-Fi 已连接"
                    : "MT7922 共存驱动已就绪 · 请先连接 Wi-Fi")
                : "当前未检测到可用的 MT7922 共存驱动";
        }
        catch (Exception)
        {
            StatusLabel.Text = "无法读取 MT7922 驱动状态";
        }
        driverChecked = true;
        if (sendAfterDriverCheck is string file)
        {
            sendAfterDriverCheck = null;
            QueueSend(file);
        }
        RefreshControls();
    }

    private bool CanQueueSend => !closing && !stopping && pendingFile is null &&
                                 sendAfterDriverCheck is null && transfer?.Sending != true &&
                                 !accepting && offerId is null;

    private bool SendInProgress => pendingFile is not null || transfer?.Sending == true;

    private void RefreshControls()
    {
        ReceiveToggle.IsChecked = listening;
        ReceiveToggle.IsEnabled = admin && ready && !closing;
        SendButton.IsEnabled = admin && ready && CanQueueSend;
        ipad.CanSend = selectedFile is not null && ready && CanQueueSend;
        PickerCloseButton.Content = selectedFile is not null ? "取消"
            : SendInProgress ? "隐藏" : "完成";
    }

    private void ToggleReceive(object sender, RoutedEventArgs e)
    {
        listening = ReceiveToggle.IsChecked == true;
        if (!listening && transfer is { Sending: false } && !accepting)
            StopListener();
        else if (listening && transfer is null && pendingFile is null)
            StartTransfer(null);
        RefreshControls();
    }

    private void SendClick(object sender, RoutedEventArgs e)
    {
        var picker = new Microsoft.Win32.OpenFileDialog
        {
            Title = "选择要发送的文件",
            CheckFileExists = true,
            Multiselect = false,
            InitialDirectory = previousFile is null ? IOP.Combine(root, "received") : IOP.GetDirectoryName(previousFile)
        };
        showingDialog = true;
        bool? chosen;
        try { chosen = picker.ShowDialog(this); }
        finally { showingDialog = false; }
        if (chosen != true || !SendButton.IsEnabled)
            return;

        OpenShare(new App.LaunchFile(picker.FileName, SendConfirmed: false));
    }

    internal void OpenShare(App.LaunchFile request)
    {
        if (closing)
            return;
        if (string.IsNullOrWhiteSpace(request.Path) || !IOP.IsPathFullyQualified(request.Path) || !File.Exists(request.Path))
        {
            ShowPanel();
            StatusLabel.Text = "找不到这次选中的文件。";
            return;
        }

        ShowPanel();
        if (!CanQueueSend)
        {
            StatusLabel.Text = "请先处理当前投送，再发送这个文件。";
            return;
        }

        if (request.SendConfirmed && admin)
        {
            if (!driverChecked)
                sendAfterDriverCheck = request.Path;
            else
                QueueSend(request.Path);
        }
        else
        {
            selectedFile = request.Path;
            ShowDevicePicker(request.Path, "选择接收设备", "已配置设备");
        }
        RefreshControls();
    }

    private void ShowDevicePicker(string file, string title, string deviceStatus)
    {
        PickerTitle.Text = title;
        SelectedFileLabel.Text = IOP.GetFileName(file);
        SelectedFileLabel.ToolTip = file;
        ipad.Status = deviceStatus;
        ipad.SetProgress(null);
        SpeedLabel.Text = "";
        Activity.Visibility = Visibility.Collapsed;
        TransferControls.Visibility = Visibility.Collapsed;
        DevicePicker.Visibility = Visibility.Visible;
        UpdateLayout();
        PositionPanel();
    }

    private void SendToDevice(object sender, RoutedEventArgs e)
    {
        if (selectedFile is not string file || !ipad.CanSend)
            return;
        if (admin)
            QueueSend(file);
        else
            RelaunchElevated(file);
    }

    private void CloseDevicePicker(object sender, RoutedEventArgs e)
    {
        if (SendInProgress)
            Hide();
        else
            EndDevicePicker();
    }

    private void EndDevicePicker()
    {
        selectedFile = null;
        DevicePicker.Visibility = Visibility.Collapsed;
        TransferControls.Visibility = Visibility.Visible;
    }

    private void QueueSend(string file)
    {
        if (!admin || !ready || !CanQueueSend)
        {
            StatusLabel.Text = ready ? "请先处理当前投送。" : "共存驱动尚未就绪，无法发送。";
            return;
        }
        if (!File.Exists(file))
        {
            StatusLabel.Text = "找不到这次选中的文件。";
            return;
        }
        selectedFile = null;
        ShowDevicePicker(file, "投送进度", "正在连接");
        previousFile = file;
        if (transfer is null)
            StartTransfer(file);
        else
        {
            pendingFile = file;
            StopListener();
        }
        RefreshControls();
    }

    private void StopListener()
    {
        if (transfer is not { Sending: false } worker || stopping)
            return;
        stopping = true;
        OfferActions.Visibility = Visibility.Collapsed;
        offerId = null;
        worker.Command(new { command = "stop" });
        StatusLabel.Text = "正在结束接收会话…";
    }

    private async void StartTransfer(string? file)
    {
        if (transfer is not null || closing)
            return;

        TransferProcess? worker = null;
        int? exitCode = null;
        try
        {
            completed = expired = stopping = accepting = movingData = false;
            failure = offerId = wlanSummary = null;
            OfferActions.Visibility = Visibility.Collapsed;
            worker = new TransferProcess(root, file);
            transfer = worker;
            logPath = worker.LogPath;
            LogsButton.IsEnabled = true;
            StatusLabel.Text = file is null ? "正在启动接收…" : "正在检查 Wi-Fi…";
            worker.Start();
            RefreshControls();
            exitCode = await Task.Run(() => worker.ReadAsync(
                data => Dispatcher.BeginInvoke(new Action(() => OnEvent(worker, data))),
                line => Dispatcher.BeginInvoke(new Action(() => OnLog(worker, line)))));
            // Drain notifications queued by the pipe reader before processing the exit.
            await Dispatcher.Yield(DispatcherPriority.Background);
        }
        catch (Exception ex)
        {
            failure = ex.Message;
        }
        finally
        {
            worker?.Dispose();
            transfer = null;
        }

        if (closing)
        {
            Shutdown();
            return;
        }

        if (offerId is not null)
            OfferActions.Visibility = Visibility.Collapsed;
        offerId = null;

        if (stopping)
        {
            if (!completed)
                Activity.Visibility = Visibility.Collapsed;
            StatusLabel.Text = "接收已关闭";
        }
        else if (completed)
        {
            StatusLabel.Text = wlanSummary ?? "本次投送已完成";
        }
        else if (!(expired || (file is null && (exitCode == 0 || exitCode == 7))))
        {
            StatusLabel.Text = DescribeFailure(file is not null);
            if (file is null)
                listening = false;
            if (file is not null || accepting)
            {
                if (file is not null)
                    ipad.Status = "未完成";
                else
                    ActivityTitle.Text = "传输未完成";
                SpeedLabel.Text = "";
            }
        }

        movingData = accepting = stopping = false;
        if (pendingFile is string next)
        {
            pendingFile = null;
            StartTransfer(next);
        }
        else if (listening && (worker is not null && exitCode.HasValue))
        {
            StartTransfer(null);
        }

        RefreshControls();
    }

    private string DescribeFailure(bool sending)
    {
        if (failure?.Contains("connectivity baseline", StringComparison.Ordinal) == true)
            return "Wi-Fi 联网检查未通过。请检查网络后重试。";
        if (failure?.Contains("did not accept", StringComparison.Ordinal) == true)
            return "iPad 拒绝了这次投送。";
        if (failure?.Contains("timed out", StringComparison.OrdinalIgnoreCase) == true
            || failure?.Contains("TimeoutError", StringComparison.Ordinal) == true)
            return movingData ? "传输超时。详情见日志。" : "等待设备或接收确认超时。详情见日志。";
        if (sending)
            return "无法完成发送。详情见日志。";
        return "接收会话已断开。可重新打开接收开关。";
    }

    private void OnLog(TransferProcess worker, string line)
    {
        if (worker != transfer)
            return;
        if (line.StartsWith("AirDrop operation failed:", StringComparison.Ordinal)
            || line.Contains("The WLAN connectivity baseline failed", StringComparison.Ordinal))
            failure = line;
        if (line.StartsWith("WLAN after AirDrop:", StringComparison.Ordinal))
            wlanSummary = line.Contains("gateway=True HTTPS=True", StringComparison.Ordinal)
                ? "投送结束 · Wi-Fi 联网检查通过"
                : "投送结束 · 部分 Wi-Fi 探测未通过";
    }

    private void OnEvent(TransferProcess worker, JsonElement data)
    {
        if (worker != transfer || closing || stopping)
            return;
        switch (GetString(data, "event"))
        {
            case "ready":
                StatusLabel.Text = "等待 iPad 投送";
                break;
            case "offer":
                offerId = GetString(data, "id");
                EndDevicePicker();
                var sender = GetString(data, "sender");
                var files = FileNames(data);
                var count = Number(data, "count");
                string extra = count > 2 ? $" 等 {(int)count} 个文件" : "";
                ShowActivity(string.IsNullOrWhiteSpace(sender) ? "iPad 请求投送" : $"{sender} 请求投送",
                    $"{string.Join("、", files.Take(2))}{extra}", "↓");
                SpeedLabel.Text = SizeText(Number(data, "total"));
                OfferActions.Visibility = Visibility.Visible;
                ShowPanel();
                break;
            case "waiting":
                ipad.Status = "等待接受";
                StatusLabel.Text = "如未见弹窗，请查看 iPad 通知中心。";
                break;
            case "answer":
                bool accepted = data.GetProperty("accepted").GetBoolean();
                offerId = null;
                OfferActions.Visibility = Visibility.Collapsed;
                accepting = accepted;
                ActivityTitle.Text = accepted ? "已接受，等待文件…" : "已拒绝这次投送";
                break;
            case "expired":
                offerId = null;
                OfferActions.Visibility = Visibility.Collapsed;
                ActivityTitle.Text = "邀请已过期，请对方重发";
                break;
            case "idle":
                expired = true;
                break;
            case "transfer":
                movingData = accepting = true;
                if (!worker.Sending)
                    ActivityTitle.Text = "正在接收";
                SpeedLabel.Text = "0.0 M/S";
                lastProgress = DateTime.UtcNow;
                SetTransferProgress(worker.Sending, 0);
                break;
            case "progress":
                double total = Number(data, "total");
                double done = Number(data, "done");
                lastProgress = DateTime.UtcNow;
                if (total > 0)
                    SetTransferProgress(worker.Sending, Math.Clamp(done / total, 0, 0.99));
                SpeedLabel.Text = $"{Number(data, "rate"):0.0} M/S";
                break;
            case "complete":
                completed = true;
                movingData = false;
                if (!worker.Sending)
                {
                    ActivityTitle.Text = "已保存到收件文件夹";
                    FileLabel.Text = string.Join("、", FileNames(data));
                }
                SetTransferProgress(worker.Sending, 1);
                double rate = Number(data, "rate");
                double peak = Number(data, "peak");
                SpeedLabel.Text = rate > 0 ? $"正文平均 {rate:0.0} M/S" : "已完成";
                if (peak > 0)
                    SpeedLabel.Text += $"　峰值 {peak:0.0} M/S";
                break;
        }
        RefreshControls();
    }

    private static string GetString(JsonElement data, string key)
        => data.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString()! : "";

    private static double Number(JsonElement data, string key)
        => data.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.Number
            && value.TryGetDouble(out double number) ? number : 0;

    private static string[] FileNames(JsonElement data)
        => data.TryGetProperty("files", out var files) && files.ValueKind == JsonValueKind.Array
            ? files.EnumerateArray().Take(12).Select(file => file.GetString() ?? "").ToArray()
            : Array.Empty<string>();

    private static string SizeText(double bytes)
        => bytes >= 1_000_000 ? $"{bytes / 1_000_000:0.0} MB"
            : bytes >= 1000 ? $"{bytes / 1000:0.0} KB"
            : bytes > 0 ? $"{bytes:0} B" : "";

    private void ShowActivity(string title, string detail, string glyph)
    {
        ActivityTitle.Text = title;
        FileLabel.Text = detail;
        SpeedLabel.Text = "";
        OfferActions.Visibility = Visibility.Collapsed;
        Ring.Data = Geometry.Empty;
        ProgressLabel.Text = glyph;
        ProgressLabel.FontSize = 15;
        Activity.Visibility = Visibility.Visible;
    }

    private void SetTransferProgress(bool sending, double value)
    {
        string percent = $"{Math.Floor(value * 100)}%";
        if (sending)
        {
            ipad.Status = value >= 1 ? "已发送" : $"发送中 · {percent}";
            ipad.SetProgress(value);
        }
        else
        {
            ProgressLabel.FontSize = value >= 1 ? 17 : 10;
            ProgressLabel.Text = value >= 1 ? "✓" : percent;
            Ring.Data = DeviceTile.CreateRing(value, 22, 20);
        }
    }

    private void Answer(bool allow)
    {
        if (offerId is not string id || transfer is null)
            return;
        if (transfer.Command(new { command = "answer", id, accept = allow }))
        {
            accepting = allow;
            OfferActions.Visibility = Visibility.Collapsed;
        }
        else
        {
            offerId = null;
            OfferActions.Visibility = Visibility.Collapsed;
            ActivityTitle.Text = "邀请已过期，请对方重发";
            RefreshControls();
        }
    }

    private void AcceptClick(object sender, RoutedEventArgs e) => Answer(true);
    private void DeclineClick(object sender, RoutedEventArgs e) => Answer(false);
    private void OpenInbox(object sender, RoutedEventArgs e) => OpenFolder(IOP.Combine(root, "received"));
    private void OpenLog(object sender, RoutedEventArgs e) { if (logPath is not null) OpenPath(logPath); }
    private void HideClick(object sender, RoutedEventArgs e) => Hide();
    private void HandleKeyDown(object sender, KeyEventArgs e) { if (e.Key == Key.Escape) Hide(); }

    private void HideOnClose(object? sender, CancelEventArgs e)
    {
        if (!closing)
        {
            e.Cancel = true;
            Hide();
        }
    }

    private void OpenFolder(string path)
    {
        if (Directory.Exists(path))
            OpenPath(path);
        else
            StatusLabel.Text = "收件文件夹尚未创建";
    }

    private void OpenPath(string path)
    {
        try { Process.Start(new ProcessStartInfo(path) { UseShellExecute = true }); }
        catch (Win32Exception) { StatusLabel.Text = "无法打开该文件或文件夹"; }
    }

    private void ElevateClick(object sender, RoutedEventArgs e) => RelaunchElevated();

    private void RelaunchElevated(string? confirmedFile = null)
    {
        showingDialog = true;
        try
        {
            var executable = Environment.ProcessPath!;
            var start = new ProcessStartInfo(executable)
            {
                UseShellExecute = true,
                WorkingDirectory = root,
                Verb = "runas"
            };
            if (confirmedFile is null)
                start.ArgumentList.Add(root);
            else
            {
                start.ArgumentList.Add("--send-confirmed");
                start.ArgumentList.Add(confirmedFile);
            }
            if (Process.Start(start) is not null)
                Shutdown();
        }
        catch (Win32Exception)
        {
            StatusLabel.Text = "未获得管理员权限";
        }
        finally { showingDialog = false; }
    }

    private void Quit()
    {
        if (accepting || transfer?.Sending == true)
        {
            ShowPanel();
            StatusLabel.Text = "请在本次投送结束后退出";
            return;
        }
        closing = true;
        listening = false;
        pendingFile = null;
        if (transfer is null)
            Shutdown();
        else
            StopListener();
        RefreshControls();
    }

    private void Shutdown()
    {
        closing = true;
        SystemEvents.UserPreferenceChanged -= OnPreferenceChanged;
        speedTimer.Stop();
        tray.Dispose();
        trayMenu.Dispose();
        trayIcon.Dispose();
        Application.Current.Shutdown();
    }
}
