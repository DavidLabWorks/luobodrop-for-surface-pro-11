using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;

namespace AirDropDesktop;

internal sealed class TransferProcess : IDisposable
{
    private const string Prefix = "AIRDROP_UI ";
    private readonly Process process;
    private StreamWriter? log;

    internal TransferProcess(string root, string? file)
    {
        var peer = Environment.GetEnvironmentVariable("LUOBODROP_PEER");
        if (string.IsNullOrWhiteSpace(peer) ||
            !System.Text.RegularExpressions.Regex.IsMatch(peer, @"\A(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\z"))
            throw new InvalidOperationException("请先设置 LUOBODROP_PEER 为对端的 AWDL 地址。");

        Sending = file is not null;
        var logs = Path.Combine(root, "build", "desktop", "logs");
        Directory.CreateDirectory(logs);
        LogPath = Path.Combine(logs, $"{(Sending ? "send" : "receive")}-{DateTime.Now:yyyyMMdd}.log");
        var args = PowerShell(root, "check-shared-wifi.ps1");
        args.RedirectStandardInput = true;
        Add(args, "-SharedAdvertPeer", peer, "-DesktopEvents");
        if (file is not null)
        {
            Add(args, "-SendFile", file, "-SendFastProfile", "-SendHe2Stream", "-PrepareUploadEarly",
                "-BatchRadioSend", "-RadioSendBatchSize", "16", "-WintunRingKiB", "4096");
        }
        else
        {
            Add(args, "-ReceiveTo", Path.Combine(root, "received"), "-WinAwdlProfile", "-Mtu1500",
                "-AdvertiseHe", "-UseRxAmsdu", "-PeerWcidAck", "-DecodeDuringReceive",
                "-FastUploadTail", "-ReceiveBufferMiB", "4", "-MemoryArchive");
        }

        process = new Process { StartInfo = args };
    }

    internal bool Sending { get; }
    internal string LogPath { get; }

    internal static ProcessStartInfo PowerShell(string root, string script)
    {
        var start = new ProcessStartInfo
        {
            FileName = Path.Combine(Environment.SystemDirectory, "WindowsPowerShell", "v1.0", "powershell.exe"),
            WorkingDirectory = root,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        start.Environment["PSModulePath"] = Path.Combine(Environment.SystemDirectory,
            "WindowsPowerShell", "v1.0", "Modules");
        Add(start, "-NoProfile", "-ExecutionPolicy", "RemoteSigned", "-File", Path.Combine(root, "scripts", script));
        return start;
    }

    private static void Add(ProcessStartInfo start, params string[] args)
    {
        foreach (string arg in args)
            start.ArgumentList.Add(arg);
    }

    internal void Start()
    {
        log = new StreamWriter(new FileStream(LogPath, FileMode.Append, FileAccess.Write,
                                              FileShare.ReadWrite), new UTF8Encoding(false));
        process.Start();
    }

    internal async Task<int> ReadAsync(Action<JsonElement> onEvent, Action<string> onLog)
    {
        object logLock = new();
        void Write(string line)
        {
            lock (logLock)
            {
                try
                {
                    log?.WriteLine($"{DateTime.Now:HH:mm:ss.fff} {line}");
                    log?.Flush();
                }
                catch (IOException)
                {
                    // Keep draining the subprocess if its log volume runs out of space.
                }
            }
        }

        async Task Read(StreamReader reader, bool events)
        {
            while (await reader.ReadLineAsync().ConfigureAwait(false) is string line)
            {
                if (events && line.StartsWith(Prefix, StringComparison.Ordinal))
                {
                    try
                    {
                        using var message = JsonDocument.Parse(line.AsMemory(Prefix.Length));
                        var data = message.RootElement;
                        if (data.ValueKind == JsonValueKind.Object)
                        {
                            if (!data.TryGetProperty("event", out var kind) || kind.ValueKind != JsonValueKind.String)
                                continue;
                            if (kind.GetString() != "progress")
                                Write(line);
                            onEvent(data.Clone());
                        }
                    }
                    catch (JsonException)
                    {
                        Write(line);
                    }
                }
                else
                {
                    Write(line);
                    onLog(line);
                }
            }
        }

        Write(Sending ? "Send" : "Receive");
        await Task.WhenAll(Read(process.StandardOutput, true), Read(process.StandardError, false),
                           process.WaitForExitAsync()).ConfigureAwait(false);
        return process.ExitCode;
    }

    internal bool Command(object command)
    {
        try
        {
            process.StandardInput.WriteLine(JsonSerializer.Serialize(command));
            process.StandardInput.Flush();
            return true;
        }
        catch (IOException) { return false; }
        catch (InvalidOperationException) { return false; }
    }

    public void Dispose()
    {
        try { log?.Dispose(); }
        catch (IOException) { }
        process.Dispose();
    }
}
