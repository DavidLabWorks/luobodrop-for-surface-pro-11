"""Compare unsolicited WLAN device service notifications on the Surface WLAN."""
import ctypes as c
from ctypes import wintypes as w
from queue import SimpleQueue
from time import monotonic, sleep
from uuid import UUID
import sys

GUID = c.c_ubyte * 16
SOURCE = 0x800  # WLAN_NOTIFICATION_SOURCE_DEVICE_SERVICE


class Notification(c.Structure):
    _fields_ = [("source", w.DWORD), ("code", w.DWORD), ("interface", GUID),
                ("size", w.DWORD), ("data", c.c_void_p)]


def watch(interface):
    wlan = c.WinDLL("wlanapi")
    callback_type = c.WINFUNCTYPE(None, c.POINTER(Notification), c.c_void_p)
    handle = w.HANDLE()
    version = w.DWORD()
    services = c.c_void_p()
    events = SimpleQueue()

    def invoke(name, types, *args):
        fn = getattr(wlan, name)
        fn.argtypes = types
        fn.restype = w.DWORD
        result = fn(*args)
        if result:
            raise OSError(f"{name}: Win32 {result} ({c.FormatError(result).strip()})")

    @callback_type
    def receive(ptr, context):
        n = ptr.contents
        if n.source != SOURCE or bytes(n.interface) != interface.bytes_le:
            return
        if n.data and n.size >= 24:
            header = c.string_at(n.data, 24)
            service = UUID(bytes_le=header[:16])
            opcode = int.from_bytes(header[16:20], "little")
            length = int.from_bytes(header[20:24], "little")
            value = None
            if length == 1 and n.size >= 25:
                value = c.c_ubyte.from_address(n.data + 24).value
            events.put((monotonic(), service, opcode, length, value))

    invoke("WlanOpenHandle", [w.DWORD, c.c_void_p, c.POINTER(w.DWORD), c.POINTER(w.HANDLE)],
           2, None, c.byref(version), c.byref(handle))
    try:
        guid = GUID.from_buffer_copy(interface.bytes_le)
        invoke("WlanGetSupportedDeviceServices", [w.HANDLE, c.POINTER(GUID), c.POINTER(c.c_void_p)],
               handle, c.byref(guid), c.byref(services))
        count = w.DWORD.from_address(services.value).value
        if not count:
            print("No device services reported.")
            return
        invoke("WlanRegisterNotification", [w.HANDLE, w.DWORD, w.BOOL, callback_type,
               c.c_void_p, c.c_void_p, c.POINTER(w.DWORD)], handle, SOURCE, False, receive,
               None, None, None)
        invoke("WlanRegisterDeviceServiceNotification", [w.HANDLE, c.c_void_p], handle, services)
        print(f"已订阅 {count} 项服务。每段 30 秒，请留意屏幕提示。")
        input("让苹果设备停在主屏幕，然后在这台 Surface 按一次回车开始：")
        start = monotonic()
        for message in [
            "第一段：保持主屏幕，等待下一条提示。",
            "第二段：现在打开照片 → 分享 → 隔空投送，停在选择接收人的页面。",
            "第三段：现在退出隔空投送，回到主屏幕。",
        ]:
            print(f"+{monotonic() - start:.1f}s {message}", flush=True)
            sleep(30)
        end = monotonic()
    finally:
        if services.value:
            wlan.WlanFreeMemory.argtypes = [c.c_void_p]
            wlan.WlanFreeMemory.restype = None
            wlan.WlanFreeMemory(services)
        wlan.WlanCloseHandle.argtypes = [w.HANDLE, c.c_void_p]
        wlan.WlanCloseHandle(handle, None)

    print("通知明细（时间相对于开始时刻）")
    total = 0
    while not events.empty():
        stamp, service, opcode, length, value = events.get()
        if start <= stamp <= end:
            total += 1
            detail = "" if value is None else f" 值=0x{value:02x}"
            print(f"+{stamp-start:.2f}s {service} 操作码={opcode} 长度={length}{detail}")
    print(f"共 {total} 条通知。")


if __name__ == "__main__":
    watch(UUID(sys.argv[1]))
