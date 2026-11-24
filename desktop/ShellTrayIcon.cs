using System;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using Drawing = System.Drawing;
using Forms = System.Windows.Forms;

namespace AirDropDesktop;

internal sealed class ShellTrayIcon : IDisposable
{
    private const uint Add = 0;
    private const uint Delete = 2;
    private const uint SetVersion = 4;
    private const uint Version = 4;
    private const int CallbackMessage = 0x8001;
    private const int Select = 0x0400;
    private const int KeySelect = 0x0401;
    private const int ContextMenu = 0x007b;
    private const uint IconFlags = 0x1 | 0x2 | 0x4 | 0x80; // Message, icon, tooltip, show tooltip.
    private const uint PopupFlags = 0x4 | 0x20 | 0x10000; // Center, bottom, stay in work area.
    private const uint MoveOnly = 0x1 | 0x4 | 0x10; // No resize, z-order change, or activation.

    private readonly HwndSource source;
    private readonly int taskbarCreated = RegisterWindowMessage("TaskbarCreated");
    private IconData data;
    private bool registered;

    internal ShellTrayIcon(Window window, Drawing.Icon icon)
    {
        var handle = new WindowInteropHelper(window).EnsureHandle();
        source = HwndSource.FromHwnd(handle);
        data = new IconData
        {
            Size = (uint)Marshal.SizeOf<IconData>(),
            Window = handle,
            Id = 1,
            Flags = IconFlags,
            Callback = CallbackMessage,
            Icon = icon.Handle,
            Tip = "隔空投送"
        };
        source.AddHook(OnMessage);
        RegisterIcon();
    }

    internal event Action? Activated;
    internal event Action<Drawing.Point>? MenuRequested;

    private void RegisterIcon()
    {
        registered = Shell_NotifyIcon(Add, ref data);
        if (registered)
        {
            data.Version = Version;
            Shell_NotifyIcon(SetVersion, ref data);
        }
    }

    private bool TryGetBounds(out NativeRect bounds)
    {
        var id = new IconIdentifier
        {
            Size = (uint)Marshal.SizeOf<IconIdentifier>(),
            Window = data.Window,
            Id = data.Id
        };
        return Shell_NotifyIconGetRect(ref id, out bounds) == 0 &&
               bounds.Right > bounds.Left && bounds.Bottom > bounds.Top;
    }

    internal bool PointerIsOnIcon() => TryGetBounds(out var bounds) &&
        Drawing.Rectangle.FromLTRB(bounds.Left, bounds.Top, bounds.Right, bounds.Bottom)
                         .Contains(Forms.Cursor.Position);

    internal bool PlacePanel(Drawing.Size size)
    {
        if (size.IsEmpty || !TryGetBounds(out var icon))
            return false;
        var anchor = new Drawing.Point(icon.Left + (icon.Right - icon.Left) / 2, icon.Top - 8);
        if (!CalculatePopupWindowPosition(ref anchor, ref size, PopupFlags, IntPtr.Zero, out var panel))
            return false;
        return SetWindowPos(data.Window, IntPtr.Zero, panel.Left, panel.Top, 0, 0, MoveOnly);
    }

    private IntPtr OnMessage(IntPtr hwnd, int message, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (message == taskbarCreated)
            RegisterIcon();
        else if (message == CallbackMessage)
        {
            switch (lParam.ToInt64() & 0xffff)
            {
                case Select:
                case KeySelect:
                    Activated?.Invoke();
                    break;
                case ContextMenu:
                    var point = TryGetBounds(out var icon)
                        ? new Drawing.Point(icon.Left + (icon.Right - icon.Left) / 2, icon.Top)
                        : Forms.Cursor.Position;
                    SetForegroundWindow(hwnd);
                    MenuRequested?.Invoke(point);
                    break;
            }
            handled = true;
        }
        return IntPtr.Zero;
    }

    public void Dispose()
    {
        source.RemoveHook(OnMessage);
        if (registered)
            Shell_NotifyIcon(Delete, ref data);
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct IconData
    {
        public uint Size;
        public IntPtr Window;
        public uint Id;
        public uint Flags;
        public uint Callback;
        public IntPtr Icon;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string Tip;
        public uint State;
        public uint StateMask;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)] public string Info;
        public uint Version;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)] public string InfoTitle;
        public uint InfoFlags;
        public Guid Guid;
        public IntPtr BalloonIcon;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IconIdentifier
    {
        public uint Size;
        public IntPtr Window;
        public uint Id;
        public Guid Guid;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct NativeRect
    {
        public int Left, Top, Right, Bottom;
    }

    [DllImport("shell32.dll", EntryPoint = "Shell_NotifyIconW", CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool Shell_NotifyIcon(uint message, ref IconData data);

    [DllImport("shell32.dll")]
    private static extern int Shell_NotifyIconGetRect(ref IconIdentifier identifier, out NativeRect bounds);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int RegisterWindowMessage(string name);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CalculatePopupWindowPosition(ref Drawing.Point anchor, ref Drawing.Size size,
                                                            uint flags, IntPtr exclude, out NativeRect position);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetForegroundWindow(IntPtr window);
}
