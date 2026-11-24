"""Compare unsolicited WLAN device service notifications on the Surface WLAN."""
import ctypes as c
from collections import Counter
from ctypes import wintypes as w
from queue import Empty, SimpleQueue
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
            # Copy only the service header while the callback buffer is valid.
            header = c.string_at(n.data, 24)
            service = UUID(bytes_le=header[:16])
            opcode = int.from_bytes(header[16:20], "little")
            length = int.from_bytes(header[20:24], "little")
            events.put((monotonic(), service, opcode, length))

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
        print(f"Subscribed to {count} services.")
        for phase, instruction in [
            ("baseline", "Close the Apple device's AirDrop sharing panel"),
            ("airdrop", "Open the AirDrop sharing panel and keep it open"),
            ("recovery", "Close the AirDrop sharing panel again"),
        ]:
            input(f"{instruction}, then press Enter: ")
            start = monotonic()
            sleep(30)
            end = monotonic()
            counts = Counter()
            while True:
                try:
                    stamp, service, opcode, length = events.get_nowait()
                except Empty:
                    break
                if start <= stamp <= end:
                    counts[service, opcode, length] += 1
            print(f"{phase}: {sum(counts.values())} notifications")
            for key, total in counts.items():
                print(" ", *key, "count", total)
    finally:
        if services.value:
            wlan.WlanFreeMemory.argtypes = [c.c_void_p]
            wlan.WlanFreeMemory.restype = None
            wlan.WlanFreeMemory(services)
        wlan.WlanCloseHandle.argtypes = [w.HANDLE, c.c_void_p]
        wlan.WlanCloseHandle(handle, None)


if __name__ == "__main__":
    watch(UUID(sys.argv[1]))
