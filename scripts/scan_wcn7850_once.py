"""Request one scan on the specified WLAN interface and report its completion."""
import ctypes as c
from ctypes import wintypes as w
from queue import Empty, Queue
import sys
from uuid import UUID

from watch_wcn7850_services import GUID, Notification


def scan(interface):
    wlan = c.WinDLL("wlanapi")
    handle = w.HANDLE()
    version = w.DWORD()
    callback_type = c.WINFUNCTYPE(None, c.POINTER(Notification), c.c_void_p)
    results = Queue()
    acm = 8

    @callback_type
    def receive(ptr, context):
        event = ptr.contents
        if (event.source == acm and bytes(event.interface) == interface.bytes_le
                and event.code in (7, 8)):
            results.put(event.code)

    def call(name, types, *args):
        fn = getattr(wlan, name)
        fn.argtypes = types
        fn.restype = w.DWORD
        code = fn(*args)
        if code:
            raise OSError(f"{name}: Win32 {code}")

    call("WlanOpenHandle", [w.DWORD, c.c_void_p, c.POINTER(w.DWORD), c.POINTER(w.HANDLE)],
         2, None, c.byref(version), c.byref(handle))
    try:
        call("WlanRegisterNotification", [w.HANDLE, w.DWORD, w.BOOL, callback_type,
             c.c_void_p, c.c_void_p, c.POINTER(w.DWORD)], handle, acm, False, receive,
             None, None, None)
        adapter = GUID.from_buffer_copy(interface.bytes_le)
        call("WlanScan", [w.HANDLE, c.POINTER(GUID), c.c_void_p, c.c_void_p, c.c_void_p],
             handle, c.byref(adapter), None, None, None)
        try:
            result = results.get(timeout=15)
        except Empty:
            raise RuntimeError("15 秒内未收到扫描完成通知") from None
        if result == 8:
            raise RuntimeError("Windows 报告扫描失败")
        print("已收到 WLAN 扫描完成通知。")
    finally:
        wlan.WlanCloseHandle.argtypes = [w.HANDLE, c.c_void_p]
        wlan.WlanCloseHandle(handle, None)


if __name__ == "__main__":
    scan(UUID(sys.argv[1]))
