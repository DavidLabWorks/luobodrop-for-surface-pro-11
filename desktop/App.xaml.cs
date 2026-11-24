using System;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;

namespace AirDropDesktop;

public partial class App : Application
{
    internal record LaunchFile(string Path, bool SendConfirmed);

    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        var root = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", ".."));
        LaunchFile? requested = null;
        if (e.Args.Length == 2 && e.Args[0] is "--share" or "--send-confirmed")
            requested = new LaunchFile(e.Args[1], e.Args[0] == "--send-confirmed");
        else if (e.Args.Length == 1 && Path.IsPathFullyQualified(e.Args[0]))
            root = Path.GetFullPath(e.Args[0]);
        else if (e.Args.Length != 0)
        {
            MessageBox.Show("请选择一个要发送的文件。", "隔空投送");
            Shutdown(1);
            return;
        }

        if (!File.Exists(Path.Combine(root, "scripts", "check-shared-wifi.ps1")))
        {
            MessageBox.Show("找不到项目文件。请从项目目录运行 airdrop.cmd。", "隔空投送");
            Shutdown(1);
            return;
        }

        using var identity = System.Security.Principal.WindowsIdentity.GetCurrent();
        bool admin = new System.Security.Principal.WindowsPrincipal(identity).IsInRole(
            System.Security.Principal.WindowsBuiltInRole.Administrator);
        using var current = Process.GetCurrentProcess();
        string pipeName = $"Mt7922AirDropDesktop-{current.SessionId}-{admin}";
        var single = new Mutex(true, $@"Local\Mt7922AirDropDesktop-{admin}", out bool first);
        if (!first)
        {
            single.Dispose();
            if (requested is not null && !await ForwardFile(pipeName, requested))
                MessageBox.Show("请从托盘退出旧版隔空投送，然后重试。", "隔空投送");
            Shutdown();
            return;
        }

        var requests = new CancellationTokenSource();
        Exit += (_, _) =>
        {
            requests.Cancel();
            single.Dispose();
        };
        var window = new TrayWindow(root, admin);
        MainWindow = window;
        _ = ReceiveFiles(pipeName, window, requests.Token);
        window.ShowPanel();
        if (requested is not null)
            window.OpenShare(requested);
    }

    private static async Task<bool> ForwardFile(string name, LaunchFile requested)
    {
        try
        {
            using var pipe = new NamedPipeClientStream(".", name, PipeDirection.Out,
                PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
            await pipe.ConnectAsync(1500);
            using var writer = new StreamWriter(pipe);
            await writer.WriteLineAsync(JsonSerializer.Serialize(requested));
            await writer.FlushAsync();
            return true;
        }
        catch (TimeoutException) { return false; }
        catch (IOException) { return false; }
        catch (UnauthorizedAccessException) { return false; }
    }

    private static async Task ReceiveFiles(string name, TrayWindow window, CancellationToken exit)
    {
        while (!exit.IsCancellationRequested)
        {
            try
            {
                using var pipe = new NamedPipeServerStream(name, PipeDirection.In, 1, PipeTransmissionMode.Byte,
                    PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
                await pipe.WaitForConnectionAsync(exit);
                using var reader = new StreamReader(pipe);
                using var deadline = CancellationTokenSource.CreateLinkedTokenSource(exit);
                deadline.CancelAfter(TimeSpan.FromSeconds(2));
                string? payload = await reader.ReadLineAsync().WaitAsync(deadline.Token);
                if (payload is not null && payload.Length < 128_000 &&
                    JsonSerializer.Deserialize<LaunchFile>(payload) is LaunchFile requested)
                    window.OpenShare(requested);
            }
            catch (OperationCanceledException) when (exit.IsCancellationRequested) { return; }
            catch (OperationCanceledException) { }
            catch (JsonException) { }
            catch (IOException) { await Task.Delay(100); }
            catch (UnauthorizedAccessException) { return; }
        }
    }
}
