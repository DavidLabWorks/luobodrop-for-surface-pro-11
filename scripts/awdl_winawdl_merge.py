"""Connect WinAWDL's native TCP receive merger to the MT7922 relay."""

import ctypes
from pathlib import Path
import struct

MAX_SEGMENTS = 64
MAX_PACKET = 65535
IDLE_SECONDS = 0.0004


class _MergeState(ctypes.Structure):
    _fields_ = [
        ("packet", ctypes.c_uint8 * MAX_PACKET),
        ("length", ctypes.c_int),
        ("segments", ctypes.c_int),
        ("closed", ctypes.c_int),
    ]


class WinAwdlReceiveBatch:
    def __init__(self, port):
        self.port = struct.pack("!H", port)
        dll_path = Path(__file__).resolve().parent.parent / "build" / "x64" / "winawdl-tcp-merge.dll"
        self.dll = ctypes.CDLL(str(dll_path))
        self.append = self.dll.tcp_merge_offer
        self.append.argtypes = (ctypes.POINTER(_MergeState), ctypes.c_char_p, ctypes.c_int)
        self.append.restype = ctypes.c_int
        self.take = self.dll.tcp_merge_take
        self.take.argtypes = (ctypes.POINTER(_MergeState), ctypes.POINTER(ctypes.c_char))
        self.take.restype = ctypes.c_int
        self.state = _MergeState()
        self.output = ctypes.create_string_buffer(MAX_PACKET)
        self.pending = []
        self.deadline = 0.0

    def flush(self):
        if not self.pending:
            return ()
        packets, self.pending = tuple(self.pending), []
        length = self.take(self.state, self.output)
        merged = self.output[:length] if len(packets) > 1 else None
        return ((packets, merged),)

    def flush_due(self, now):
        if self.pending and now >= self.deadline:
            return self.flush()
        return ()

    def offer(self, packet, now):
        ready = self.flush_due(now)
        if not 60 <= len(packet) <= MAX_PACKET or packet[42:44] != self.port:
            return ready + self.flush() + (((packet,), None),)
        if not self.append(self.state, packet, len(packet)):
            if not self.pending:
                return ready + (((packet,), None),)
            ready += self.flush()
            if not self.append(self.state, packet, len(packet)):
                return ready + (((packet,), None),)
        self.pending.append(packet)
        self.deadline = now + IDLE_SECONDS
        if self.state.closed or self.state.segments == MAX_SEGMENTS:
            ready += self.flush()
        return ready

