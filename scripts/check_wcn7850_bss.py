"""Compare BSS cache timestamps for the connected AP across one WLAN scan."""
import ctypes as c
from ctypes import wintypes as w
from uuid import UUID
import sys
import time

from scan_wcn7850_once import GUID, scan


class Ssid(c.Structure):
    _fields_ = [("length", w.ULONG), ("bytes", c.c_ubyte * 32)]


class Rates(c.Structure):
    _fields_ = [("length", w.ULONG), ("rates", w.USHORT * 126)]


class Entry(c.Structure):
    _fields_ = [
        ("ssid", Ssid), ("phy_id", w.ULONG), ("bssid", c.c_ubyte * 6),
        ("bss_type", c.c_int), ("phy_type", c.c_int), ("rssi", w.LONG),
        ("quality", w.ULONG), ("in_region", c.c_ubyte), ("period", w.USHORT),
        ("ap_time", c.c_uint64), ("host_time", c.c_uint64),
        ("capabilities", w.USHORT), ("frequency", w.ULONG), ("rates", Rates),
        ("ie_offset", w.ULONG), ("ie_size", w.ULONG),
    ]


def measure(interface, bssid):
    api = c.WinDLL("wlanapi")
    handle = w.HANDLE()
    version = w.DWORD()
    adapter = GUID.from_buffer_copy(interface.bytes_le)
    api.WlanOpenHandle.argtypes = [w.DWORD, c.c_void_p, c.POINTER(w.DWORD), c.POINTER(w.HANDLE)]
    api.WlanGetNetworkBssList.argtypes = [w.HANDLE, c.POINTER(GUID), c.c_void_p,
        c.c_int, w.BOOL, c.c_void_p, c.POINTER(c.c_void_p)]
    api.WlanFreeMemory.argtypes = [c.c_void_p]
    api.WlanFreeMemory.restype = None
    api.WlanCloseHandle.argtypes = [w.HANDLE, c.c_void_p]

    def snapshot():
        data = c.c_void_p()
        code = api.WlanGetNetworkBssList(handle, c.byref(adapter), None, 3, False, None, c.byref(data))
        if code:
            raise c.WinError(code)
        try:
            size, count = (w.DWORD * 2).from_address(data.value)
            if 8 + count * c.sizeof(Entry) > size:
                raise ValueError("Invalid WLAN_BSS_LIST length")
            entries = (Entry * count).from_address(data.value + 8)
            times = [row.host_time for row in entries if bytes(row.bssid) == bssid]
            return max(times, default=None)
        finally:
            api.WlanFreeMemory(data)

    code = api.WlanOpenHandle(2, None, c.byref(version), c.byref(handle))
    if code:
        raise c.WinError(code)
    try:
        before = snapshot()
        start = time.time_ns() // 100 + 116444736000000000
        scan(interface)
        after = snapshot()
        end = time.time_ns() // 100 + 116444736000000000
        print("AP present before/after:", before is not None, after is not None)
        if before is not None and after is not None:
            print("AP host timestamp advance (seconds):", (after - before) / 10000000)
        if after is not None:
            print("Latest AP observation within measurement window:", start <= after <= end)
    finally:
        api.WlanCloseHandle(handle, None)


if __name__ == "__main__":
    measure(UUID(sys.argv[1]), bytes.fromhex(sys.argv[2].replace(":", "")))
