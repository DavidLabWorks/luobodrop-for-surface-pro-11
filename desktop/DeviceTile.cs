using System;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows;
using System.Windows.Media;

namespace AirDropDesktop;

internal sealed class DeviceTile : INotifyPropertyChanged
{
    private bool canSend;
    private string status = "";
    private Geometry? ring;

    internal DeviceTile(string name) => Name = name;

    public string Name { get; }
    public bool CanSend
    {
        get => canSend;
        set => Set(ref canSend, value);
    }
    public string Status
    {
        get => status;
        set => Set(ref status, value);
    }
    public Geometry? Ring
    {
        get => ring;
        private set => Set(ref ring, value);
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    internal void SetProgress(double? progress)
        => Ring = progress is double value ? CreateRing(value, 34, 30.25) : null;

    internal static Geometry CreateRing(double value, double center, double radius)
    {
        if (value <= 0)
            return Geometry.Empty;
        if (value >= 1)
            return new EllipseGeometry(new Point(center, center), radius, radius);

        double angle = value * 2 * Math.PI;
        var end = new Point(center + radius * Math.Sin(angle), center - radius * Math.Cos(angle));
        var arc = new ArcSegment(end, new Size(radius, radius), 0, value > 0.5, SweepDirection.Clockwise, true);
        return new PathGeometry(new[] { new PathFigure(new Point(center, center - radius), new[] { arc }, false) });
    }

    private void Set<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (Equals(field, value))
            return;
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}