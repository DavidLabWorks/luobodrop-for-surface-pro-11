$ErrorActionPreference = 'Stop'
$nic = Get-NetAdapter -Name WLAN
if ($nic.InterfaceDescription -notlike '*FastConnect*7800*') {
    throw "WLAN does not point to the expected FastConnect 7800."
}
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class WlanServiceQuery {
    [DllImport("wlanapi.dll")] public static extern uint WlanOpenHandle(uint version, IntPtr reserved, out uint negotiated, out IntPtr handle);
    [DllImport("wlanapi.dll")] public static extern uint WlanGetSupportedDeviceServices(IntPtr handle, ref Guid adapter, out IntPtr list);
    [DllImport("wlanapi.dll")] public static extern void WlanFreeMemory(IntPtr memory);
    [DllImport("wlanapi.dll")] public static extern uint WlanCloseHandle(IntPtr handle, IntPtr reserved);
}
"@
$handle = [IntPtr]::Zero
$list = [IntPtr]::Zero
$version = [uint32]0
$guid = [guid]$nic.InterfaceGuid
$result = [WlanServiceQuery]::WlanOpenHandle(2, [IntPtr]::Zero, [ref]$version, [ref]$handle)
if ($result) { throw [ComponentModel.Win32Exception]::new([int]$result) }
try {
    $result = [WlanServiceQuery]::WlanGetSupportedDeviceServices($handle, [ref]$guid, [ref]$list)
    if ($result -eq 5) { throw 'Access denied (5). Run this query in an administrator PowerShell.' }
    if ($result) { throw [ComponentModel.Win32Exception]::new([int]$result) }
    $count = [Runtime.InteropServices.Marshal]::ReadInt32($list)
    "Device services: $count"
    for ($i = 0; $i -lt $count; $i++) {
        $address = [IntPtr]::Add($list, 8 + 16 * $i)
        [Runtime.InteropServices.Marshal]::PtrToStructure($address, [type][guid])
    }
} finally {
    if ($list -ne [IntPtr]::Zero) { [WlanServiceQuery]::WlanFreeMemory($list) }
    [void][WlanServiceQuery]::WlanCloseHandle($handle, [IntPtr]::Zero)
}
