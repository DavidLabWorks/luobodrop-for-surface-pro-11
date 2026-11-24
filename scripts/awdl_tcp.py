"""Batch contiguous IPv6/TCP receive segments for the AWDL Wintun relay."""

import csv
from dataclasses import dataclass
import socket
import struct

TCP_FIN = 0x01
TCP_SYN = 0x02
TCP_RST = 0x04
TCP_PSH = 0x08
TCP_ACK = 0x10
IPV6_HEADER_BYTES = 40
BATCH_BYTE_LIMITS = {4: 6144, 8: 10240, 12: 15360, 16: 20480}
DEFAULT_BATCH_SEGMENTS = 4
MAX_HOLD_SECONDS = 0.00075
SEQUENCE_MASK = 0xffffffff


def tcp_flags(packet):
    if len(packet) >= 60 and packet[6] == socket.IPPROTO_TCP and 20 <= (packet[52] >> 4) * 4 <= len(packet) - 40:
        return packet[53]
    return None


def tcp_options(packet):
    end = IPV6_HEADER_BYTES + (packet[52] >> 4) * 4
    cursor = IPV6_HEADER_BYTES + 20
    while cursor < end:
        kind = packet[cursor]
        if kind == 0:
            return
        if kind == 1:
            cursor += 1
            continue
        if end - cursor < 2:
            return
        length = packet[cursor + 1]
        if not 2 <= length <= end - cursor:
            return
        yield kind, packet[cursor + 2:cursor + length]
        cursor += length


def with_tcp_checksum(packet):
    tcp = packet[40:]
    pseudo = packet[8:40] + struct.pack("!I3xB", len(tcp), socket.IPPROTO_TCP)
    data = pseudo + tcp[:16] + b"\0\0" + tcp[18:]
    if len(data) % 2:
        data += b"\0"
    checksum = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while checksum > 0xffff:
        checksum = (checksum & 0xffff) + (checksum >> 16)
    expected = struct.pack("!H", checksum ^ 0xffff)
    return packet if tcp[16:18] == expected else packet[:56] + expected + packet[58:]


def _payload_offset(packet, port):
    flags = tcp_flags(packet)
    if flags not in (TCP_ACK, TCP_ACK | TCP_PSH) or packet[42:44] != port:
        return None
    if packet[0] >> 4 != 6 or int.from_bytes(packet[4:6], "big") != len(packet) - IPV6_HEADER_BYTES:
        return None
    if packet[52] not in (0x50, 0x80) or packet[58:60] != b"\0\0":
        return None
    offset = IPV6_HEADER_BYTES + (packet[52] >> 4) * 4
    if len(packet) <= offset:
        return None
    if offset == 72 and packet[60:64] != b"\x01\x01\x08\x0a":
        return None
    if with_tcp_checksum(packet) != packet:
        return None
    return offset


def _is_contiguous(previous, packet, offset):
    if (previous[:4] != packet[:4] or previous[6:44] != packet[6:44]
            or previous[48:53] != packet[48:53] or previous[54:56] != packet[54:56]):
        return False
    expected = (int.from_bytes(previous[44:48], "big") + len(previous) - offset) & SEQUENCE_MASK
    if int.from_bytes(packet[44:48], "big") != expected:
        return False
    if offset == 72:
        change = (int.from_bytes(packet[64:68], "big") - int.from_bytes(previous[64:68], "big")) & SEQUENCE_MASK
        return packet[68:72] == previous[68:72] and change < 0x80000000
    return True


def coalesced_packet(segments):
    """Build one packet from segments already checked by TcpReceiveBatch."""
    if len(segments) == 1:
        return segments[0]
    last = segments[-1]
    offset = IPV6_HEADER_BYTES + (last[52] >> 4) * 4
    header = bytearray(last[:offset])
    header[44:48] = segments[0][44:48]
    payload = b"".join(packet[offset:] for packet in segments)
    struct.pack_into("!H", header, 4, offset - IPV6_HEADER_BYTES + len(payload))
    return with_tcp_checksum(bytes(header) + payload)


class TcpHandshakeTrace:
    """Track packet headers on one receiver connection before upload."""

    def __init__(self):
        self.syn = 0
        self.syn_ack = 0
        self.syn_ack_end = None
        self.windows_window_scale = None
        self.peer_window_scale = None
        self.peer_ack = None
        self.reply_end = None
        self.peer_data = 0
        self.local_data = 0
        self.peer_reset = 0
        self.local_reset = 0

    @staticmethod
    def covers(ack, end):
        return ack is not None and end is not None and ((ack - end) & SEQUENCE_MASK) < 0x80000000

    def observe(self, packet, flags, *, from_peer):
        payload = len(packet) - IPV6_HEADER_BYTES - (packet[52] >> 4) * 4
        if from_peer:
            self.syn += (flags & (TCP_SYN | TCP_ACK)) == TCP_SYN
            self.peer_reset += bool(flags & TCP_RST)
            self.peer_data += payload
            if (flags & (TCP_SYN | TCP_ACK)) == (TCP_SYN | TCP_ACK):
                self.peer_window_scale = self.window_scale(packet)
            if flags & TCP_ACK and not flags & TCP_RST:
                ack = int.from_bytes(packet[48:52], "big")
                if self.peer_ack is None or self.covers(ack, self.peer_ack):
                    self.peer_ack = ack
        else:
            self.local_reset += bool(flags & TCP_RST)
            self.local_data += payload
            sequence = int.from_bytes(packet[44:48], "big")
            if (flags & (TCP_SYN | TCP_ACK)) == (TCP_SYN | TCP_ACK):
                self.syn_ack += 1
                self.syn_ack_end = (sequence + 1) & SEQUENCE_MASK
                self.windows_window_scale = self.window_scale(packet)
            if payload:
                end = (sequence + payload + bool(flags & TCP_SYN)) & SEQUENCE_MASK
                if self.reply_end is None or self.covers(end, self.reply_end):
                    self.reply_end = end

    @staticmethod
    def window_scale(packet):
        return next((min(data[0], 14) for kind, data in tcp_options(packet) if kind == 3 and len(data) == 1), 0)

    def summary(self):
        handshake_ack = self.covers(self.peer_ack, self.syn_ack_end)
        return (f"SYN={self.syn} SYN-ACK={self.syn_ack} SYN-ACK-confirmed={int(handshake_ack)} "
                f"{self.data_summary()}")

    def data_summary(self):
        reply_ack = self.covers(self.peer_ack, self.reply_end)
        return (f"peer-data-bytes={self.peer_data} Windows-data-bytes={self.local_data} "
                f"Windows-data-confirmed={int(reply_ack)} peer-RST={self.peer_reset} Windows-RST={self.local_reset}")


class UploadTcpClose:
    """Track the final TCP close on the upload's existing connection."""

    def __init__(self):
        self.reply_segments = 0
        self.fin_ack = None
        self.fin_acked = False
        self.peer_fin = False
        self.peer_reset = False

    def sent(self, packet, flags):
        payload_bytes = len(packet) - IPV6_HEADER_BYTES - (packet[52] >> 4) * 4
        self.reply_segments += payload_bytes > 0
        if flags & TCP_FIN:
            sequence = int.from_bytes(packet[44:48], "big")
            self.fin_ack = (sequence + payload_bytes + 1) & SEQUENCE_MASK

    def received(self, packet, flags):
        self.peer_fin |= bool(flags & TCP_FIN)
        self.peer_reset |= bool(flags & TCP_RST)
        if self.fin_ack is not None and flags & TCP_ACK and not flags & TCP_RST:
            self.fin_acked |= int.from_bytes(packet[48:52], "big") == self.fin_ack


@dataclass
class UploadFlowInterval:
    arrived_bytes: int = 0
    acked_bytes: int = 0
    acks: int = 0
    sacks: int = 0
    zero_windows: int = 0
    min_window: int | None = None
    max_window: int = 0
    longest_ack_gap: float = 0.0


class UploadTcpFlow:
    """Summarize upload headers at the Wintun/driver boundary in five-second intervals."""

    def __init__(self):
        self.intervals = {}
        self.last_ack = None
        self.last_ack_progress = None
        self.acked_bytes = 0

    def interval(self, seconds):
        index = int(max(seconds, 0) // 5)
        if index not in self.intervals:
            self.intervals[index] = UploadFlowInterval()
        return self.intervals[index]

    def received(self, payload, seconds):
        self.interval(seconds).arrived_bytes += payload

    def sent(self, packet, seconds, window_scale):
        interval = self.interval(seconds)
        interval.acks += 1
        has_sack = any(kind == 5 and len(data) >= 8 for kind, data in tcp_options(packet))
        interval.sacks += has_sack
        window = int.from_bytes(packet[54:56], "big")
        interval.zero_windows += window == 0
        if window_scale is not None:
            window <<= window_scale
            interval.min_window = window if interval.min_window is None else min(interval.min_window, window)
            interval.max_window = max(interval.max_window, window)
        ack = int.from_bytes(packet[48:52], "big")
        if self.last_ack is None:
            self.last_ack = ack
            self.last_ack_progress = seconds
            return has_sack
        advance = (ack - self.last_ack) & SEQUENCE_MASK
        if 0 < advance < 0x80000000:
            interval.acked_bytes += advance
            self.acked_bytes += advance
            interval.longest_ack_gap = max(interval.longest_ack_gap, seconds - self.last_ack_progress)
            self.last_ack = ack
            self.last_ack_progress = seconds
        return has_sack

    def summaries(self, payload_label="RX-bytes"):
        for index, interval in sorted(self.intervals.items()):
            window = ("unknown" if interval.min_window is None else
                      f"{interval.min_window}..{interval.max_window}")
            yield (f"{index * 5}-{(index + 1) * 5}s {payload_label}={interval.arrived_bytes} "
                   f"ACK-advance-bytes={interval.acked_bytes} ACKs={interval.acks} SACKs={interval.sacks} "
                   f"window-bytes={window} zero-window={interval.zero_windows} "
                   f"ACK-progress-gap={interval.longest_ack_gap:.3f}s")


class UploadAckTiming:
    """Keep a bounded header-only trace, written after the upload."""

    MAX_EVENTS = 60000
    COLUMNS = ("event", "peer_port", "tcp_seq", "tcp_ack", "tcp_payload_bytes",
               "segments", "wintun_ns", "driver_call_ns", "driver_return_ns", "tcp_window_raw")

    def __init__(self):
        self.rows = []
        self.omitted = 0

    def add(self, event, packet, peer_port, segments, wintun_ns, driver_call_ns=None, driver_return_ns=None):
        if len(self.rows) >= self.MAX_EVENTS:
            self.omitted += 1
            return
        seq, ack, window = struct.unpack_from("!II2xH", packet, IPV6_HEADER_BYTES + 4)
        payload = len(packet) - IPV6_HEADER_BYTES - (packet[52] >> 4) * 4
        self.rows.append((event, peer_port, seq, ack, payload, segments,
                          wintun_ns, driver_call_ns, driver_return_ns, window))

    def write(self, file, port_column="peer_port"):
        output = csv.writer(file)
        output.writerow((self.COLUMNS[0], port_column, *self.COLUMNS[2:]))
        output.writerows(self.rows)


class TcpReceiveBatch:
    """Hold only a short burst for one connection and preserve delivery order."""

    def __init__(self, port, max_segments=DEFAULT_BATCH_SEGMENTS):
        self.port = struct.pack("!H", port)
        self.max_segments = max_segments
        self.max_bytes = BATCH_BYTE_LIMITS[max_segments]
        self.pending = []
        self.size = 0
        self.deadline = 0.0

    def flush(self):
        if not self.pending:
            return ()
        batch, self.pending = tuple(self.pending), []
        self.size = 0
        return (batch,)

    def flush_due(self, now):
        if self.pending and now >= self.deadline:
            return self.flush()
        return ()

    def offer(self, packet, now):
        ready = self.flush_due(now)
        offset = _payload_offset(packet, self.port)
        if offset is None:
            return ready + self.flush() + ((packet,),)

        if self.pending and (self.size + len(packet) - offset > self.max_bytes
                             or not _is_contiguous(self.pending[-1], packet, offset)):
            ready += self.flush()
        if not self.pending:
            self.deadline = now + MAX_HOLD_SECONDS
            self.size = offset
        self.pending.append(packet)
        self.size += len(packet) - offset
        if len(self.pending) == self.max_segments or packet[53] & TCP_PSH:
            ready += self.flush()
        return ready
