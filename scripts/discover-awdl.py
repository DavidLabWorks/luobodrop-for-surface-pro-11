"""Discover, send or receive AirDrop files over the MT7922 link."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
import csv
import ctypes as c
from ctypes import wintypes as w
from datetime import datetime
import ipaddress
import os
from pathlib import Path
import plistlib
import socket
import struct
import subprocess
import sys
import tempfile
from threading import Event, Lock
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "winawdl" / "airdrop"))
import awdl_receiver
from awdl_desktop import DesktopSession, Progress as DesktopProgress, Stopped
from awdl_winawdl_merge import MAX_SEGMENTS as WINAWDL_MAX_SEGMENTS, WinAwdlReceiveBatch
from awdl_tcp import (
    BATCH_BYTE_LIMITS, DEFAULT_BATCH_SEGMENTS, TCP_ACK, TCP_RST, TCP_SYN, TcpHandshakeTrace, TcpReceiveBatch,
    UploadAckTiming, UploadTcpClose, UploadTcpFlow, coalesced_packet, tcp_flags, with_tcp_checksum,
)
import sender

POWERSHELL = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
MTU = 1280
WINAWDL_MTU = 1450
APPLE_PROFILE_MTU = 1500
WINTUN_RING_DEFAULT = 256 * 1024
WINTUN_RING_LARGE = 4 * 1024 * 1024
STARTUP_OBSERVATION_SECONDS = 20
RADIO_BATCH_SIZES = (8, 16, 32, 64)
DEFAULT_RADIO_BATCH_PACKETS = 8
DEFAULT_UPLOAD_BATCH_PACKETS = 16
MAX_UPLOAD_BATCH_PACKETS = 64
UPLOAD_BATCH_COMPARISON = (
    (0, "warmup", 16),
    (16 << 20, "16a", 16),
    (28 << 20, "32", 32),
    (40 << 20, "16b", 16),
    (52 << 20, "tail", 16),
)
PROBE_GUID = uuid.UUID("bf1a2054-dc33-4538-842c-5f228907de89")
SHARED_GUID = uuid.UUID("36446677-94db-41f4-ba29-98c372e7e0b6")
START, STATUS, EXCHANGE, STOP = (0x228000 | (n << 2) for n in range(0x808, 0x80c))
EXCHANGE_BATCH = 0x228000 | (0x80d << 2)
UPLOAD_DISCOVERED = 0x228000 | (0x810 << 2)
BA_RESET = 0x228000 | (0x811 << 2)
BA_STATUS = 0x224000 | (0x812 << 2)
BA_RESET_BA_ONLY = 1
BA_RESET_PEER = 2
BA_STATE_NAMES = {0: "idle", 1: "operational", 2: "disabling", 3: "disabled",
                  4: "rebuilding", 5: "failed"}
BA_STATUS_STRUCT = struct.Struct("<20I")
EXCHANGE_UPLOAD_ACK = {24: 0x228000 | (0x80e << 2), 54: 0x228000 | (0x80f << 2)}
ERROR_NOT_READY = 21
ERROR_INVALID_PARAMETER = 87
ERROR_BUFFER_OVERFLOW = 111
ERROR_BUSY = 170
ERROR_NO_MORE_ITEMS = 259
ERROR_IO_PENDING = 997
ERROR_NOT_FOUND = 1168
ERROR_OBJECT_ALREADY_EXISTS = 5010
DAD_TENTATIVE = 1
DAD_PREFERRED = 4
CREATE_WAITABLE_TIMER_HIGH_RESOLUTION = 0x2
TIMER_MODIFY_STATE = 0x2
SYNCHRONIZE = 0x100000
WAIT_INFINITE = 0xffffffff
WAIT_OBJECT_0 = 0
INVALID_HANDLE = c.c_void_p(-1).value
DWORD_PTR = c.POINTER(w.DWORD)
LINK_REPORT = struct.Struct("<67I")
SHARED_TX_STATUS_MISSING = struct.Struct("<I")
SHARED_TX_REPORT = struct.Struct("<5I")
SHARED_BA_REPORT = struct.Struct("<5I")
SHARED_ACK_REPORT = struct.Struct("<4I")
UPLOAD_TX_REPORT = struct.Struct("<4I")
UPLOAD_TX_REPORT_MAGIC = 0x58545055
UPLOAD_BA_REPORT_V1 = struct.Struct("<9I")
UPLOAD_BA_REPORT = struct.Struct("<11I")
UPLOAD_BA_REPORT_MAGIC = 0x41425055
UPLOAD_WTBL_HEADER_V1 = struct.Struct("<2I")
UPLOAD_WTBL_HEADER = struct.Struct("<4I")
UPLOAD_WTBL_SNAPSHOT = struct.Struct("<7I")
UPLOAD_WTBL_REPORT_BYTES = UPLOAD_WTBL_HEADER.size + 4 * UPLOAD_WTBL_SNAPSHOT.size
UPLOAD_WTBL_REPORT_MAGIC = 0x4C425457
UPLOAD_ADDBA_TIMING = struct.Struct("<5I")
UPLOAD_ADDBA_TIMING_MAGIC = 0x54414241
UPLOAD_BW_MAGIC = 0x57425055
UPLOAD_BW_MAX_SAMPLES = 41
UPLOAD_BW_HEADER = struct.Struct("<2I")
UPLOAD_BW_SAMPLE = struct.Struct("<Q12I")
UPLOAD_BW_REPORT_BYTES = UPLOAD_BW_HEADER.size + UPLOAD_BW_MAX_SAMPLES * UPLOAD_BW_SAMPLE.size
SHARED_AMSDU_REPORT = struct.Struct("<6I")
SHARED_HE_REPORT = struct.Struct("<8I")
ACK_TRACE_HEADER = struct.Struct("<4I")
ACK_TRACE_ENTRY_V1 = struct.Struct("<4Q2I4H3B5x")
ACK_TRACE_ENTRY = struct.Struct("<4Q2I4H6BHQ")
ACK_TRACE_MAGIC = 0x544b4341
ACK_TRACE_MAX = 4096
ACK_WAIT_REASONS = {
    0x01: "WLAN-TX", 0x02: "AWDL-TX", 0x04: "channel-window",
    0x08: "DMA", 0x10: "control-frame", 0x20: "ACK-slots",
}
SYN_ACK_TIMING = struct.Struct("<4I")
SYN_ACK_QUEUE_TIMING = struct.Struct("<4I")
SYN_ACK_TIMING_MAX = 32
MDNS_TIMING = struct.Struct("<9I")
MDNS_TIMING_MAGIC = 0x534e444d
RECEIVER_MODE = 1
RECEIVER_WARMUP_MODE = 2
RECEIVER_WINAWDL_MODE = 3
RECEIVER_HE = 0x08
RECEIVER_AMSDU = 0x10
RECEIVER_REORDER = 0x20
RECEIVER_SHORT_POLL = 0x40
SHARED_RECEIVER_MTU1500 = 0x40
SHARED_UPLOAD_EARLY = 0x01
SHARED_UPLOAD_BATCH64 = 0x08
SHARED_UPLOAD_BATCH = 0x10
SHARED_UPLOAD_HE_2_STREAM = 0x20
SHARED_UPLOAD_FAST = 0x40
SHARED_PEER_WCID_ACK = 0x80
RECEIVER_VHT = 0x80
RECEIVER_RX256 = 0x04


def report_ack_trace(entries, omitted, has_dma_observation, receiving):
    label_prefix = "ACK" if receiving else "upload TX"
    port_label = "peer-port" if receiving else "local-port"
    if not entries:
        print(f"MT7922 {label_prefix} timing: samples=0 omitted={omitted}", flush=True)
        return

    peer_port, count = Counter(entry[6] for entry in entries).most_common(1)[0]
    samples = [entry for entry in entries if entry[6] == peer_port]
    missing_status = sum(entry[2] == 0 for entry in samples)
    missing_release = sum(entry[3] == 0 for entry in samples)
    wcid_summary = " ".join(f"{wcid}={count}" for wcid, count in sorted(Counter(e[13] for e in samples).items()))
    missing_dma = sum(entry[14] == 0 for entry in samples)
    dma_summary = f" missing-device-fetch={missing_dma}" if has_dma_observation else ""
    print(f"MT7922 {label_prefix} timing: {port_label}={peer_port} samples={count} total={len(entries)} "
          f"omitted={omitted} missing-tx-status={missing_status} missing-tx-free={missing_release}"
          f"{dma_summary} wcid-counts=[{wcid_summary}]", flush=True)

    stages = [("enqueue-to-DMA", 0, 1), ("DMA-to-TX-status", 1, 2), ("DMA-to-TX-free", 1, 3)]
    if has_dma_observation:
        stages += [("DMA-submit-to-device-fetch", 1, 14), ("device-fetch-to-TX-status", 14, 2)]
    for label, start, end in stages:
        times = sorted((entry[end] - entry[start]) / 10000 for entry in samples
                       if 0 < entry[start] <= entry[end])
        if times:
            p50 = times[(len(times) - 1) // 2]
            p95 = times[int((len(times) - 1) * 0.95)]
            print(f"MT7922 {label_prefix} timing {label}: n={len(times)} p50={p50:.3f} ms "
                  f"p95={p95:.3f} ms max={times[-1]:.3f} ms", flush=True)
    if any(entry[15] for entry in samples):
        slowest = sorted(samples, key=lambda entry: entry[1] - entry[0], reverse=True)[:3]
        for entry in slowest:
            queued_ms = (entry[1] - entry[0]) / 10000
            primary = ACK_WAIT_REASONS.get(entry[16], "unobserved")
            seen = ",".join(reason for bit, reason in ACK_WAIT_REASONS.items() if entry[15] & bit) or "none"
            print(f"MT7922 ACK wait: wlan-seq={entry[7]} queue-ms={queued_ms:.3f} "
                  f"primary={primary} observed-ms={entry[17] / 10:.1f} gates={seen}", flush=True)
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", newline="", delete=False,
                                         dir=ROOT / "build", prefix="awdl-ack-driver-" if receiving else "awdl-upload-tx-driver-",
                                         suffix=".csv") as output:
            writer = csv.writer(output)
            writer.writerow(("enqueued_100ns", "dma_submitted_100ns", "tx_status_100ns", "tx_free_100ns",
                             "tcp_seq", "tcp_ack", port_label.replace("-", "_"), "wlan_seq", "rate_code", "attempts",
                             "tx_queue", "ack_errors", "release_status", "wcid", "dma_observed_100ns",
                             "wait_flags", "wait_primary", "wait_observed_100us"))
            writer.writerows(entries)
        print(f"MT7922 {label_prefix} driver timing file: {output.name}", flush=True)
    except OSError as error:
        print(f"MT7922 {label_prefix} driver timing file unavailable: {error}", flush=True)


class Overlapped(c.Structure):
    _fields_ = [
        ("internal", c.c_size_t), ("high", c.c_size_t),
        ("offset", w.DWORD), ("offset_high", w.DWORD), ("event", w.HANDLE),
    ]


def api(dll, name, result, *arguments):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = arguments
    return function


class Driver:
    def __init__(self, cleanup, *, shared_wifi=False):
        self.shared_wifi = shared_wifi
        kernel = c.WinDLL("kernel32", use_last_error=True)
        create_file = api(kernel, "CreateFileW", w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD,
                          c.c_void_p, w.DWORD, w.DWORD, w.HANDLE)
        self.create_event = api(kernel, "CreateEventW", w.HANDLE, c.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR)
        self.close = api(kernel, "CloseHandle", w.BOOL, w.HANDLE)
        self.control = api(kernel, "DeviceIoControl", w.BOOL, w.HANDLE, w.DWORD,
                           c.c_void_p, w.DWORD, c.c_void_p, w.DWORD, DWORD_PTR, c.POINTER(Overlapped))
        self.get_result = api(kernel, "GetOverlappedResult", w.BOOL, w.HANDLE,
                              c.POINTER(Overlapped), DWORD_PTR, w.BOOL)
        self.wait = api(kernel, "WaitForSingleObject", w.DWORD, w.HANDLE, w.DWORD)
        self.cancel = api(kernel, "CancelIoEx", w.BOOL, w.HANDLE, c.POINTER(Overlapped))
        path = self.interface_path(SHARED_GUID if shared_wifi else PROBE_GUID)
        self.handle = create_file(path, 0xc0000000, 3, None, 3, 0x40000000, None)
        if self.handle == INVALID_HANDLE:
            raise c.WinError(c.get_last_error())
        cleanup.callback(self.close, self.handle)

    @staticmethod
    def interface_path(interface=PROBE_GUID):
        cfg = c.WinDLL("cfgmgr32")
        size = api(cfg, "CM_Get_Device_Interface_List_SizeW", w.ULONG,
                   c.POINTER(w.ULONG), c.c_void_p, w.LPCWSTR, w.ULONG)
        get = api(cfg, "CM_Get_Device_Interface_ListW", w.ULONG, c.c_void_p,
                  w.LPCWSTR, w.LPWSTR, w.ULONG, w.ULONG)
        guid = c.create_string_buffer(interface.bytes_le)
        count = w.ULONG()
        status = size(c.byref(count), guid, None, 0)
        if status:
            raise RuntimeError(f"Reading device interface size failed (CONFIGRET 0x{status:x})")
        paths = c.create_unicode_buffer(count.value)
        status = get(guid, None, paths, count.value, 0)
        if status:
            raise RuntimeError(f"Reading device interfaces failed (CONFIGRET 0x{status:x})")
        present = [path for path in paths[:].split("\0") if path]
        if len(present) != 1:
            mode = "WiFiCx shared" if interface == SHARED_GUID else "probe"
            raise RuntimeError(f"Expected one active MT7922 {mode} interface, found {len(present)}")
        return present[0]

    def call(self, operation, data=b"", size=0, timeout_ms=5000):
        with ExitStack() as cleanup:
            request = Request(self, cleanup, operation, data, size)
            return request.result(timeout_ms)

    def run_link(self, firmware_and_target, receiving, trace_ack):
        # Keep the overlapped request alive through the driver's receive window and firmware startup.
        timeout_ms = 1120000 if receiving else 640000 if self.shared_wifi else 160000
        report_size = LINK_REPORT.size
        if self.shared_wifi:
            report_size += SHARED_TX_REPORT.size + SYN_ACK_TIMING_MAX * (SYN_ACK_TIMING.size + SYN_ACK_QUEUE_TIMING.size)
            report_size += (SHARED_BA_REPORT.size + SHARED_ACK_REPORT.size + SHARED_AMSDU_REPORT.size
                            + SHARED_HE_REPORT.size)
            if trace_ack:
                report_size += ACK_TRACE_HEADER.size + ACK_TRACE_MAX * ACK_TRACE_ENTRY.size
            report_size += MDNS_TIMING.size if receiving else UPLOAD_TX_REPORT.size + UPLOAD_BA_REPORT.size
            if not receiving and trace_ack:
                report_size += UPLOAD_WTBL_REPORT_BYTES + UPLOAD_ADDBA_TIMING.size + UPLOAD_BW_REPORT_BYTES
        return self.call(START, data=firmware_and_target, size=report_size, timeout_ms=timeout_ms)

    def stop(self, session, *, receiving):
        if session.cancel():
            return
        if not session.done():
            try:
                self.call(STOP)
            except OSError as error:
                if error.winerror != ERROR_NOT_READY:
                    raise
                self.cancel(self.handle, None)
        result = session.result()
        missing_tx_status = None
        syn_ack_radio = None
        syn_ack_timing = b""
        syn_ack_queues = []
        ba_firmware = None
        shared_ack = None
        shared_amsdu = None
        shared_he = None
        ack_trace = None
        mdns_timing = None
        upload_tx_timeout = None
        upload_ba = None
        upload_wtbl = None
        upload_addba = None
        upload_bandwidth = None
        if self.shared_wifi:
            if not receiving:
                bw_offset = len(result) - UPLOAD_BW_REPORT_BYTES
                if bw_offset >= LINK_REPORT.size + SHARED_TX_REPORT.size + UPLOAD_WTBL_REPORT_BYTES:
                    magic, count = UPLOAD_BW_HEADER.unpack_from(result, bw_offset)
                    if magic == UPLOAD_BW_MAGIC and 0 < count <= UPLOAD_BW_MAX_SAMPLES:
                        start = bw_offset + UPLOAD_BW_HEADER.size
                        end = start + count * UPLOAD_BW_SAMPLE.size
                        upload_bandwidth = list(UPLOAD_BW_SAMPLE.iter_unpack(result[start:end]))
                        result = result[:bw_offset]
                addba_offset = len(result) - UPLOAD_ADDBA_TIMING.size
                wtbl_offset = addba_offset - UPLOAD_WTBL_REPORT_BYTES
                if (wtbl_offset >= LINK_REPORT.size + SHARED_TX_REPORT.size
                        and UPLOAD_WTBL_HEADER.unpack_from(result, wtbl_offset)[0] == UPLOAD_WTBL_REPORT_MAGIC):
                    magic, *timing = UPLOAD_ADDBA_TIMING.unpack_from(result, addba_offset)
                    if magic == UPLOAD_ADDBA_TIMING_MAGIC:
                        upload_addba = timing
                        result = result[:addba_offset]
                for layout in (UPLOAD_WTBL_HEADER, UPLOAD_WTBL_HEADER_V1):
                    offset = len(result) - layout.size - 4 * UPLOAD_WTBL_SNAPSHOT.size
                    if offset < LINK_REPORT.size + SHARED_TX_REPORT.size:
                        continue
                    magic, window, *request_tx = layout.unpack_from(result, offset)
                    if magic == UPLOAD_WTBL_REPORT_MAGIC:
                        begin = offset + layout.size
                        upload_wtbl = window, request_tx, list(UPLOAD_WTBL_SNAPSHOT.iter_unpack(result[begin:]))
                        result = result[:offset]
                        break
                for layout in (UPLOAD_BA_REPORT, UPLOAD_BA_REPORT_V1):
                    ba_offset = len(result) - layout.size
                    if ba_offset < LINK_REPORT.size + SHARED_TX_REPORT.size:
                        continue
                    magic, *ba = layout.unpack_from(result, ba_offset)
                    if magic == UPLOAD_BA_REPORT_MAGIC:
                        upload_ba = ba
                        result = result[:ba_offset]
                        break
            if not receiving and len(result) >= LINK_REPORT.size + SHARED_TX_REPORT.size + UPLOAD_TX_REPORT.size:
                magic, kind, sequence, age_us = UPLOAD_TX_REPORT.unpack_from(result, len(result) - UPLOAD_TX_REPORT.size)
                if magic == UPLOAD_TX_REPORT_MAGIC:
                    upload_tx_timeout = kind, sequence, age_us
                    result = result[:-UPLOAD_TX_REPORT.size]
            extra_size = len(result) - LINK_REPORT.size
            if extra_size >= SHARED_TX_REPORT.size:
                missing_tx_status, *syn_ack_radio = SHARED_TX_REPORT.unpack_from(result, LINK_REPORT.size)
                timing_data = result[LINK_REPORT.size + SHARED_TX_REPORT.size:]
                if receiving and len(timing_data) >= MDNS_TIMING.size:
                    trailer = MDNS_TIMING.unpack_from(timing_data, len(timing_data) - MDNS_TIMING.size)
                    if trailer[0] == MDNS_TIMING_MAGIC:
                        mdns_timing = trailer[1:]
                        timing_data = timing_data[:-MDNS_TIMING.size]
                count = min(syn_ack_radio[0], SYN_ACK_TIMING_MAX)
                timing_bytes = count * SYN_ACK_TIMING.size
                queue_bytes = count * SYN_ACK_QUEUE_TIMING.size
                timing_end = timing_bytes + queue_bytes
                ba_end = timing_end + SHARED_BA_REPORT.size
                ack_end = ba_end + SHARED_ACK_REPORT.size
                amsdu_end = ack_end + SHARED_AMSDU_REPORT.size
                he_end = amsdu_end + SHARED_HE_REPORT.size
                trace_entry_start = he_end + ACK_TRACE_HEADER.size
                if len(timing_data) >= trace_entry_start:
                    magic, count, omitted, stride = ACK_TRACE_HEADER.unpack_from(timing_data, he_end)
                    if (magic != ACK_TRACE_MAGIC or count > ACK_TRACE_MAX
                            or stride not in (ACK_TRACE_ENTRY_V1.size, ACK_TRACE_ENTRY.size)
                            or trace_entry_start + count * stride != len(timing_data)):
                        raise RuntimeError("MT7922 returned an invalid ACK timing report")
                    has_dma_observation = stride == ACK_TRACE_ENTRY.size
                    trace_format = ACK_TRACE_ENTRY if has_dma_observation else ACK_TRACE_ENTRY_V1
                    entries = list(trace_format.iter_unpack(timing_data[trace_entry_start:]))
                    if has_dma_observation:
                        entries = [(*entry[:14], entry[17], *entry[14:17]) for entry in entries]
                    else:
                        entries = [(*entry, 0, 0, 0, 0, 0) for entry in entries]
                    ack_trace = entries, omitted, has_dma_observation
                    timing_data = timing_data[:he_end]
                if len(timing_data) == he_end:
                    shared_he = SHARED_HE_REPORT.unpack_from(timing_data, amsdu_end)
                    timing_data = timing_data[:amsdu_end]
                if len(timing_data) == amsdu_end:
                    shared_amsdu = SHARED_AMSDU_REPORT.unpack_from(timing_data, ack_end)
                    timing_data = timing_data[:ack_end]
                if len(timing_data) == ack_end:
                    shared_ack = SHARED_ACK_REPORT.unpack_from(timing_data, ba_end)
                    timing_data = timing_data[:ba_end]
                if len(timing_data) == ba_end:
                    ba_firmware = SHARED_BA_REPORT.unpack_from(timing_data, timing_end)
                    timing_data = timing_data[:timing_end]
                if len(timing_data) not in (0, timing_bytes, timing_bytes + queue_bytes):
                    raise RuntimeError(f"MT7922 returned an unexpected SYN-ACK report size: {len(timing_data)} bytes")
                syn_ack_timing = timing_data[:timing_bytes]
                syn_ack_queues = list(SYN_ACK_QUEUE_TIMING.iter_unpack(timing_data[timing_bytes:]))
                result = result[:LINK_REPORT.size]
            elif extra_size == SHARED_TX_STATUS_MISSING.size:
                missing_tx_status, = SHARED_TX_STATUS_MISSING.unpack_from(result, LINK_REPORT.size)
                result = result[:LINK_REPORT.size]
        has_rx_radio_stats = len(result) >= 184
        has_reorder_stats = len(result) == LINK_REPORT.size and receiving
        if len(result) in (104, 108, 112, 124, 128, 136, 144, 184, 200, 208):
            result = result.ljust(LINK_REPORT.size, b"\0")
        if len(result) != LINK_REPORT.size:
            raise RuntimeError(f"MT7922 driver returned an unexpected link report size: {len(result)} bytes")
        (status, stage, recovery, peer_sync, ba_req, ba_tx, ba_ack, mdns_tx, mdns_rx,
         ping_tx, ping_rx, syn, syn_ack, refused, reset, submitted, acked, no_ack,
         peer_ipv6, local_ipv6, received, drops, configured_rate, probe_rate,
         tunnel_rate, shared_mif, announced, service_mif, peer_data, local_data,
         skipped_rx, upload_window_replies, fast_polls, late_polls,
         recovery_sent, recovery_acked, vht_adverts, rx_legacy, rx_ht, rx_vht, rx_vht80,
         rx_other, rx_unknown, ampdu_frames, ampdu_bursts, ampdu_max,
         fast_ack_submitted, fast_ack_acked, fast_ack_no_ack, fast_ack_rate,
         short_polls, short_poll_us, reorder_sessions, reorder_direct, reorder_released,
         reorder_duplicates, reorder_holes, reorder_bars, reorder_timeouts,
         rx_queue_depth, rx_queue_peak, rx_fetch_batches, rx_fetch_packets,
         rx_fetch_gap_count, rx_fetch_gap_sum_us, rx_fetch_gap_min_us,
         rx_fetch_gap_max_us) = LINK_REPORT.unpack(result)
        print(f"MT7922 link ended: stage={stage} status=0x{status:08x} cleanup=0x{recovery:08x}", flush=True)
        print(f"MT7922 control probe: mDNS={mdns_rx}/{mdns_tx} Ping={ping_rx}/{ping_tx} "
              f"SYN={syn} SYN-ACK={syn_ack} refused={refused} reset={reset}", flush=True)
        print(f"MT7922 radio: peer-sync={peer_sync} submitted={submitted} acked={acked} no-ack={no_ack} "
              f"peer-IPv6={peer_ipv6} local-IPv6={local_ipv6} queued-RX={received} RX-drops={drops}", flush=True)
        if receiving and rx_queue_depth:
            average_batch = rx_fetch_packets / rx_fetch_batches if rx_fetch_batches else 0.0
            average_gap = rx_fetch_gap_sum_us / rx_fetch_gap_count if rx_fetch_gap_count else 0.0
            print(f"MT7922 RX queue: depth={rx_queue_depth} peak={rx_queue_peak} "
                  f"fetch-batches={rx_fetch_batches} fetch-packets={rx_fetch_packets} "
                  f"avg-batch={average_batch:.1f} gap-us count={rx_fetch_gap_count} "
                  f"min={rx_fetch_gap_min_us} avg={average_gap:.0f} max={rx_fetch_gap_max_us}", flush=True)
        if missing_tx_status is not None:
            print(f"MT7922 shared TX: tx-status-missing={missing_tx_status}", flush=True)
        if shared_ack is not None and (receiving or shared_ack[0]):
            submitted, released, peak, errors = shared_ack
            label = "TCP ACK" if receiving else "upload TX"
            print(f"MT7922 shared {label}: submitted={submitted} released={released} "
                  f"peak-in-flight={peak} release-errors={errors}", flush=True)
        if upload_ba is not None:
            requests, responses, peer_status, firmware_status, eligible, acks, largest, peer_he, *tx = upload_ba
            air = f" TXS-HE80={tx[0]} TXS-A-MPDU={tx[1]}" if tx else ""
            print(f"MT7922 upload Block Ack: requests={requests} responses={responses} "
                  f"peer-status=0x{peer_status:08x} firmware-status=0x{firmware_status:08x} peer-HE={peer_he} "
                  f"aggregation-eligible={eligible} peer-BA={acks} max-bitmap-bits={largest}{air}", flush=True)
        if upload_bandwidth is not None:
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", newline="", delete=False,
                                                 dir=ROOT / "build", prefix="awdl-upload-bw-",
                                                 suffix=".csv") as output:
                    writer = csv.writer(output)
                    writer.writerow(("sampled_at_100ns", "peer_matched", "dw9", "dw10", "dw16", "dw17",
                                     "dw18", "dw19", "in_flight", "he20", "he40", "he80", "he_other"))
                    writer.writerows(upload_bandwidth)
                print(f"MT7922 upload bandwidth samples: count={len(upload_bandwidth)} file={output.name}",
                      flush=True)
            except OSError as error:
                print(f"MT7922 upload bandwidth sampling file unavailable: {error}", flush=True)
        if upload_addba is not None:
            syncs, windows, deferrals, expired = upload_addba
            reasons = {**ACK_WAIT_REASONS, 0x40: "MIF", 0x80: "mDNS", 0x100: "TX-ring"}
            deferred = ",".join(label for bit, label in reasons.items() if deferrals & bit) or "none"
            print(f"MT7922 upload ADDBA scheduling: fresh-syncs={syncs} regular-windows={windows} "
                  f"deferred={deferred} expired-before-submit={expired}", flush=True)
        if upload_wtbl is not None:
            window, request_tx, snapshots = upload_wtbl
            if request_tx:
                completed, acked = request_tx
                print(f"MT7922 upload ADDBA: tx-completed={completed} tx-acked={acked}", flush=True)
            for phase, snapshot in zip(("before", "after-wtbl", "after-sta", "finish"), snapshots):
                state, caps, agg, ba, control, bw, rates = snapshot
                if state == 0:
                    continue
                if state != 2:
                    print(f"MT7922 upload WTBL {phase}: peer address mismatch", flush=True)
                    continue
                print(f"MT7922 upload WTBL {phase}: negotiated-window={window} tid0-code={ba & 0xf} "
                      f"DW2=0x{caps:08x} DW5=0x{agg:08x} DW6=0x{ba:08x} DW7=0x{control:08x} "
                      f"DW9=0x{bw:08x} DW10=0x{rates:08x}", flush=True)
        if upload_tx_timeout is not None and upload_tx_timeout[0]:
            kind, sequence, age_us = upload_tx_timeout
            label = {1: "control", 2: "upload data"}.get(kind, str(kind))
            print(f"MT7922 TX-free timeout: {label} wlan-seq={sequence} age={age_us / 1000:.3f} ms", flush=True)
        if ack_trace is not None:
            report_ack_trace(*ack_trace, receiving=receiving)
        if shared_amsdu is not None:
            negotiated, frames, subframes, split, discarded, dma = shared_amsdu
            print(f"MT7922 A-MSDU: negotiations={negotiated} frames={frames} subframes={subframes} "
                  f"split-records={split} discarded={discarded} dma-reassembled={dma}", flush=True)
        if shared_he is not None:
            adverts, peer, configured, status, frames, frames80, mcs10, mcs11 = shared_he
            print(f"MT7922 HE receive: adverts={adverts} peer={peer} firmware={configured} "
                  f"status=0x{status:08x} frames={frames} HE80={frames80} MCS10={mcs10} MCS11={mcs11}", flush=True)
        if syn_ack_radio is not None:
            sent_syn_ack, acked_syn_ack, failed_syn_ack, retried_syn_ack = syn_ack_radio
            print(f"MT7922 SYN-ACK radio: submitted={sent_syn_ack} acked={acked_syn_ack} "
                  f"failed={failed_syn_ack} retries={retried_syn_ack}", flush=True)
        for index, (sequence, peer_tsf, age_us, flags) in enumerate(SYN_ACK_TIMING.iter_unpack(syn_ack_timing)):
            altx = bool(flags & 8)
            print(f"MT7922 SYN-ACK timing: wlan-seq={sequence} peer-tsf=0x{peer_tsf:08x} "
                  f"sync-age={age_us}us recent-window={int(bool(flags & 1))} "
                  f"projected-window={int(bool(flags & 2))} retry={int(bool(flags & 4))} "
                  f"tx-queue={16 if altx else 1}", flush=True)
            if index < len(syn_ack_queues):
                dma_us, release_us, sampled, nonempty = syn_ack_queues[index]
                dma = f"{dma_us}us" if sampled else "unobserved"
                release = f"{release_us}us" if release_us else "unobserved"
                states = "n/a" if altx else "/".join(
                    "pending" if nonempty & bit else "empty" if sampled & bit else "-"
                    for bit in (1, 2, 4, 8))
                print(f"MT7922 SYN-ACK queue: wlan-seq={sequence} dma={dma} release={release} "
                      f"PLE@dma/10/50/100ms={states}", flush=True)
        if recovery_sent:
            print(f"MT7922 listener recovery: echo-sent={recovery_sent} wireless-acked={recovery_acked}",
                  flush=True)
        print(f"MT7922 Block Ack: requests={ba_req} responses={ba_tx} acked={ba_ack}", flush=True)
        if ba_firmware is not None and receiving:
            attempted, configured, ba_status, ba_reply, ba_cleanup = ba_firmware
            print(f"MT7922 Block Ack firmware: attempted={attempted} configured={configured} "
                  f"status=0x{ba_status:08x} reply=0x{ba_reply:08x} cleanup=0x{ba_cleanup:08x}", flush=True)
        if has_reorder_stats:
            print(f"MT7922 Block Ack reorder: sessions={reorder_sessions} direct={reorder_direct} "
                  f"released={reorder_released} duplicates={reorder_duplicates} holes={reorder_holes} "
                  f"BAR={reorder_bars} timeouts={reorder_timeouts}", flush=True)
        print(f"MT7922 shared-window MIF: released={shared_mif}", flush=True)
        if mdns_timing is not None:
            queued, submitted, sequence, wait_us, dma_us, release_us, samples, _ = mdns_timing
            sequence_text = str(sequence) if submitted else "unseen"
            wait = f"{wait_us / 1000:.3f}ms" if submitted else "unseen"
            dma = f"{dma_us / 1000:.3f}ms" if samples & 16 else "unseen"
            release = f"{release_us / 1000:.3f}ms" if release_us else "unseen"
            print(f"MT7922 first mDNS timing: queued={queued} wlan-seq={sequence_text} "
                  f"queue-to-submit={wait} submit-to-DMA={dma} submit-to-TX-free={release}", flush=True)
        if announced:
            print(f"MT7922 AirDrop mDNS announcements: released={announced} service-MIF={service_mif}", flush=True)
            print(f"MT7922 AWDL data frames: peer={peer_data} to-us={local_data} skipped-RX={skipped_rx}", flush=True)
            window_label = "peer-window" if self.shared_wifi else "upload-window"
            print(f"MT7922 {window_label} replies: {upload_window_replies}", flush=True)
            if not self.shared_wifi:
                print(f"MT7922 upload polling: precise={fast_polls} waited-at-least-4TU={late_polls}", flush=True)
        if self.shared_wifi and fast_polls:
            direction = "receive" if receiving else "send"
            print(f"MT7922 shared {direction} polling: passes={fast_polls} gaps-at-least-4ms={late_polls}", flush=True)
        if has_rx_radio_stats:
            print(f"MT7922 RX radio: VHT-adverts={vht_adverts} legacy={rx_legacy} HT={rx_ht} "
                  f"VHT={rx_vht} VHT80={rx_vht80} other={rx_other} unknown={rx_unknown} "
                  f"A-MPDU-frames={ampdu_frames} bursts={ampdu_bursts} largest={ampdu_max}", flush=True)
        if fast_ack_submitted:
            print(f"MT7922 upload ACK rate: submitted={fast_ack_submitted} acked={fast_ack_acked} "
                  f"no-ack={fast_ack_no_ack} report=0x{fast_ack_rate:04x}", flush=True)
        if short_polls:
            print(f"MT7922 upload short polling: requests={short_polls} "
                  f"target=250us average={short_poll_us / short_polls:.0f}us", flush=True)
        print(f"MT7922 OFDM rate codes: configured=0x{configured_rate:04x} "
              f"probe=0x{probe_rate:04x} tunnel=0x{tunnel_rate:04x}", flush=True)
        if status or recovery:
            raise RuntimeError(f"MT7922 link ended at stage {stage} (0x{status:08x}, cleanup 0x{recovery:08x})")


class Request:
    def __init__(self, driver, cleanup, operation, data, size):
        self.driver = driver
        self.input = c.create_string_buffer(data, len(data)) if data else None
        self.output = c.create_string_buffer(size) if size else None
        self.length = w.DWORD()
        self.overlapped = Overlapped()
        self.overlapped.event = driver.create_event(None, True, False, None)
        if not self.overlapped.event:
            raise c.WinError(c.get_last_error())
        cleanup.callback(driver.close, self.overlapped.event)
        self.pending = not driver.control(driver.handle, operation, self.input, len(data), self.output,
                                          size, c.byref(self.length), c.byref(self.overlapped))
        if self.pending:
            error = c.get_last_error()
            if error != ERROR_IO_PENDING:
                raise c.WinError(error)
            cleanup.callback(self.finish_on_exit)


    def result(self, milliseconds):
        if self.pending:
            wait = self.driver.wait(self.overlapped.event, milliseconds)
            if wait == 258:
                raise TimeoutError("Timed out waiting for MT7922 I/O")
            if wait != 0:
                raise c.WinError(c.get_last_error())
            self.pending = False
            if not self.driver.get_result(self.driver.handle, c.byref(self.overlapped),
                                          c.byref(self.length), False):
                raise c.WinError(c.get_last_error())
        return self.output.raw[:self.length.value] if self.output is not None else b""

    def finish_on_exit(self):
        if self.pending:
            self.driver.cancel(self.driver.handle, c.byref(self.overlapped))
            # The OVERLAPPED and packet buffers must outlive the submitted I/O.
            self.driver.get_result(self.driver.handle, c.byref(self.overlapped), c.byref(self.length), True)
            self.pending = False


class SockaddrIn6(c.Structure):
    _fields_ = [
        ("family", w.USHORT), ("port", w.USHORT), ("flow_info", w.ULONG),
        ("ip", c.c_ubyte * 16), ("scope_id", w.ULONG),
    ]


class UnicastIpAddressRow(c.Structure):
    # SOCKADDR_INET has the size and alignment of its SOCKADDR_IN6 member.
    _fields_ = [
        ("address", SockaddrIn6), ("luid", c.c_uint64), ("index", w.ULONG),
        ("prefix_origin", c.c_int), ("suffix_origin", c.c_int),
        ("valid_lifetime", w.ULONG), ("preferred_lifetime", w.ULONG),
        ("prefix_length", c.c_ubyte), ("skip_as_source", c.c_ubyte),
        ("dad_state", c.c_int), ("scope_id", w.ULONG), ("created_at", c.c_int64),
    ]


class InterfaceGuid(c.Structure):
    _fields_ = [
        ("data1", w.ULONG), ("data2", w.USHORT), ("data3", w.USHORT),
        ("data4", c.c_ubyte * 8),
    ]


class MibIfRow2(c.Structure):
    # Windows SDK netioapi.h. GetIfEntry2 requires the complete row.
    _fields_ = [
        ("luid", c.c_uint64), ("index", w.ULONG), ("guid", InterfaceGuid),
        ("alias", w.WCHAR * 257), ("description", w.WCHAR * 257),
        ("physical_address_length", w.ULONG), ("physical_address", c.c_ubyte * 32),
        ("permanent_physical_address", c.c_ubyte * 32),
        ("mtu", w.ULONG), ("type", w.ULONG), ("tunnel_type", c.c_int),
        ("media_type", c.c_int), ("physical_medium_type", c.c_int),
        ("access_type", c.c_int), ("direction_type", c.c_int), ("status_flags", c.c_ubyte),
        ("oper_status", c.c_int), ("admin_status", c.c_int), ("media_connect_state", c.c_int),
        ("network_guid", InterfaceGuid), ("connection_type", c.c_int),
    ] + [(name, c.c_uint64) for name in (
        "transmit_link_speed", "receive_link_speed", "in_octets", "in_ucast_pkts",
        "in_nucast_pkts", "in_discards", "in_errors", "in_unknown_protos",
        "in_ucast_octets", "in_multicast_octets", "in_broadcast_octets",
        "out_octets", "out_ucast_pkts", "out_nucast_pkts", "out_discards",
        "out_errors", "out_ucast_octets", "out_multicast_octets", "out_broadcast_octets", "out_qlen",
    )]


class UploadReplyWait:
    """Wake for a Windows outbound packet, or resume polling after 1 ms."""

    def __init__(self, cleanup, outgoing_event=None):
        kernel = c.WinDLL("kernel32", use_last_error=True)
        create = api(kernel, "CreateWaitableTimerExW", w.HANDLE, c.c_void_p, w.LPCWSTR, w.DWORD, w.DWORD)
        self.set_timer = api(kernel, "SetWaitableTimer", w.BOOL, w.HANDLE, c.POINTER(c.c_longlong),
                             w.LONG, c.c_void_p, c.c_void_p, w.BOOL)
        self.wait_events = api(kernel, "WaitForMultipleObjects", w.DWORD, w.DWORD,
                               c.POINTER(w.HANDLE), w.BOOL, w.DWORD)
        close = api(kernel, "CloseHandle", w.BOOL, w.HANDLE)
        self.timer = create(None, None, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_MODIFY_STATE | SYNCHRONIZE)
        if not self.timer:
            raise c.WinError(c.get_last_error())
        cleanup.callback(close, self.timer)
        self.events = (w.HANDLE * 2)(outgoing_event, self.timer)
        self.delay = c.c_longlong(-10_000)  # Relative time in 100 ns units.

    def wait(self):
        if not self.set_timer(self.timer, c.byref(self.delay), 0, None, None, False):
            raise c.WinError(c.get_last_error())
        result = self.wait_events(len(self.events), self.events, False, WAIT_INFINITE)
        if result not in (WAIT_OBJECT_0, WAIT_OBJECT_0 + 1):
            raise c.WinError(c.get_last_error())
        return result == WAIT_OBJECT_0


class Tunnel:
    def __init__(self, ring_capacity=WINTUN_RING_DEFAULT):
        self.ring_capacity = ring_capacity
        path = ROOT / "tools" / "wintun" / "bin" / "amd64" / "wintun.dll"
        dll = c.WinDLL(str(path), use_last_error=True)
        handle = c.c_void_p
        self.create = api(dll, "WintunCreateAdapter", handle, w.LPCWSTR, w.LPCWSTR, c.c_void_p)
        self.close = api(dll, "WintunCloseAdapter", None, handle)
        self.luid = api(dll, "WintunGetAdapterLUID", None, handle, c.POINTER(c.c_uint64))
        self.start = api(dll, "WintunStartSession", handle, handle, w.DWORD)
        self.end = api(dll, "WintunEndSession", None, handle)
        self.read_event = api(dll, "WintunGetReadWaitEvent", w.HANDLE, handle)
        self.receive = api(dll, "WintunReceivePacket", c.c_void_p, handle, DWORD_PTR)
        self.release = api(dll, "WintunReleaseReceivePacket", None, handle, c.c_void_p)
        self.allocate = api(dll, "WintunAllocateSendPacket", c.c_void_p, handle, w.DWORD)
        self.send = api(dll, "WintunSendPacket", None, handle, c.c_void_p)
        ip_helper = c.WinDLL("iphlpapi")
        self.get_if_entry = api(ip_helper, "GetIfEntry2", w.ULONG, c.POINTER(MibIfRow2))
        self.to_index = api(ip_helper, "ConvertInterfaceLuidToIndex", w.ULONG,
                            c.POINTER(c.c_uint64), c.POINTER(w.ULONG))
        row_ptr = c.POINTER(UnicastIpAddressRow)
        self.init_address = api(ip_helper, "InitializeUnicastIpAddressEntry", None, row_ptr)
        self.create_address = api(ip_helper, "CreateUnicastIpAddressEntry", w.ULONG, row_ptr)
        self.get_address = api(ip_helper, "GetUnicastIpAddressEntry", w.ULONG, row_ptr)
        ntdll = c.WinDLL("ntdll")
        self.busy_error = api(ntdll, "RtlNtStatusToDosError", w.ULONG, w.ULONG)(0x80000011)

    def open(self, cleanup):
        self.name = "MT7922 AWDL " + uuid.uuid4().hex[:8]
        adapter = self.create(self.name, "AWDL", None)
        if not adapter:
            raise c.WinError(c.get_last_error())
        cleanup.callback(self.close, adapter)
        self.session = self.start(adapter, self.ring_capacity)
        if not self.session:
            raise c.WinError(c.get_last_error())
        cleanup.callback(self.end, self.session)
        self.interface_luid, index = c.c_uint64(), w.ULONG()
        self.luid(adapter, c.byref(self.interface_luid))
        error = self.to_index(c.byref(self.interface_luid), c.byref(index))
        if error:
            raise c.WinError(error)
        return index.value

    def outbound_statistics(self):
        row = MibIfRow2()
        row.luid = self.interface_luid.value
        error = self.get_if_entry(c.byref(row))
        if error:
            raise c.WinError(error)
        return row.out_ucast_pkts, row.out_discards, row.out_errors

    def add_ipv6_address(self, local):
        row = UnicastIpAddressRow()
        self.init_address(c.byref(row))
        row.address.family = socket.AF_INET6
        row.address.ip[:] = local.packed
        row.luid = self.interface_luid.value
        row.prefix_length = 64
        error = self.create_address(c.byref(row))
        if error not in (0, ERROR_OBJECT_ALREADY_EXISTS):
            raise OSError(f"Adding {local} to Wintun failed (Win32 {error}): {c.FormatError(error).strip()}")

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            error = self.get_address(c.byref(row))
            if error == 0:
                if row.dad_state == DAD_PREFERRED:
                    return
                if row.dad_state != DAD_TENTATIVE:
                    raise RuntimeError(f"Wintun IPv6 address {local} is unusable (DAD state {row.dad_state})")
            elif error != ERROR_NOT_FOUND:
                raise OSError(f"Reading {local} on Wintun failed (Win32 {error}): {c.FormatError(error).strip()}")
            time.sleep(0.25)
        raise TimeoutError(f"Wintun IPv6 address {local} did not become usable within 10 seconds")

    def outgoing(self):
        length = w.DWORD()
        packet = self.receive(self.session, c.byref(length))
        if packet:
            try:
                return c.string_at(packet, length.value)
            finally:
                self.release(self.session, packet)
        error = c.get_last_error()
        if error != ERROR_NO_MORE_ITEMS:
            raise c.WinError(error)
        return None

    def incoming(self, data):
        packet = self.allocate(self.session, len(data))
        if not packet:
            error = c.get_last_error()
            if error == ERROR_BUFFER_OVERFLOW:
                return False
            raise c.WinError(error)
        c.memmove(packet, data, len(data))
        self.send(self.session, packet)
        return True


def configure_tunnel(index, mtu):
    command = (
        "$ErrorActionPreference='Stop'; "
        f"Set-NetIPInterface -InterfaceIndex {index} -AddressFamily IPv6 "
        f"-NlMtuBytes {mtu} -RouterDiscovery Disabled -PolicyStore ActiveStore"
    )
    subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", command],
                   check=True, timeout=30)


def wait_for_peer(driver, session, desktop=None):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if desktop is not None:
            desktop.check()
        if session.done():
            raise RuntimeError("MT7922 link ended before the selected peer was ready")
        try:
            reply = driver.call(STATUS, size=36)
        except OSError as error:
            if error.winerror != ERROR_NOT_READY:
                raise
        else:
            state, local, peer = struct.unpack("<I16s16s", reply)
            if state == 2:
                return ipaddress.IPv6Address(local), ipaddress.IPv6Address(peer)
        time.sleep(0.05)
    raise TimeoutError("Waiting for the selected AWDL peer on channel 149")


def for_link(packet, source, destination, mtu):
    return (40 <= len(packet) <= mtu and packet[0] >> 4 == 6
            and int.from_bytes(packet[4:6], "big") == len(packet) - 40
            and packet[8:24] == source.packed and packet[24:40] == destination.packed)


class ScopedConnection(sender.Connection):
    def __init__(self, local, peer, port, index, context, timeout, tcp_trace=None):
        super().__init__(str(peer), port, index, context, timeout)
        self.local = str(local)
        self.tcp_trace = tcp_trace

    def close(self):
        if self.tcp_trace is not None:
            self.tcp_trace.stop(self)
        super().close()

    def connect(self):
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.bind((self.local, 0, 0, self.scope))
            sock.connect((self.host, self.port, 0, self.scope))
            print("AirDrop TCP connected", flush=True)
            self.sock = self.context.wrap_socket(sock)
            print(f"AirDrop TLS established: {self.sock.version()}", flush=True)
        except OSError:
            sock.close()
            raise


def discover(local, peer, port, index, context):
    connection = ScopedConnection(local, peer, port, index, context, timeout=20)
    try:
        status, body = sender.post_plist(connection, "/Discover", {})
        print(f"AirDrop /Discover response: HTTP {status}", flush=True)
        if status != 200:
            raise RuntimeError(f"AirDrop refused discovery (HTTP {status})")
        response = plistlib.loads(body)
        name = response.get("ReceiverComputerName") if isinstance(response, dict) else None
        if not isinstance(name, str) or not name:
            raise RuntimeError("AirDrop /Discover did not advertise a receiver name")
        print(f"AirDrop receiver: {name!r}, model={response.get('ReceiverModelName')!r}", flush=True)
    finally:
        connection.close()


class UploadBodyTiming:
    LIMIT = 60_000

    def __init__(self):
        self.rows = []
        self.omitted = 0

    def add(self, event, started_ns, size):
        ended_ns = time.monotonic_ns()
        if len(self.rows) < self.LIMIT:
            self.rows.append((event, started_ns, ended_ns, size))
        else:
            self.omitted += 1

    def save(self, local_port):
        if not self.rows:
            return
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", newline="", delete=False,
                                             dir=ROOT / "build", prefix="awdl-upload-body-", suffix=".csv") as trace:
                writer = csv.writer(trace)
                writer.writerow(("event", "local_port", "started_ns", "ended_ns", "bytes"))
                writer.writerows((event, local_port, start, end, size) for event, start, end, size in self.rows)
            print(f"AirDrop upload body timing file: {trace.name} ({len(self.rows)} events, "
                  f"{self.omitted} omitted, local-port={local_port})", flush=True)
        except OSError as error:
            print(f"AirDrop upload body timing file unavailable: {error}", flush=True)


class WintunOutboundTrace:
    INTERVAL_NS = 100_000_000
    LIMIT = 6_000

    def __init__(self, tunnel):
        self.tunnel = tunnel
        self.next_sample_ns = 0
        self.rows = []
        self.error = None
        self.omitted = 0

    def sample(self, *, final=False):
        if self.error is not None:
            return
        now = time.monotonic_ns()
        if not final and now < self.next_sample_ns:
            return
        self.next_sample_ns = now + self.INTERVAL_NS
        if not final and len(self.rows) >= self.LIMIT:
            self.omitted += 1
            return
        try:
            counters = self.tunnel.outbound_statistics()
        except OSError as error:
            self.error = error
            return
        self.rows.append((now, time.monotonic_ns(), *counters))

    def save(self):
        if self.error is not None:
            print(f"AirDrop Wintun outbound counters stopped: {self.error}", flush=True)
        if not self.rows:
            return
        if len(self.rows) >= 2 and self.error is None:
            first, last = self.rows[0], self.rows[-1]
            print(f"AirDrop Wintun outbound: unicast={last[2] - first[2]} "
                  f"discards={last[3] - first[3]} errors={last[4] - first[4]}", flush=True)
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", newline="", delete=False,
                                             dir=ROOT / "build", prefix="awdl-upload-wintun-", suffix=".csv") as trace:
                writer = csv.writer(trace)
                writer.writerow(("sample_started_ns", "sample_ended_ns", "out_ucast_pkts",
                                 "out_discards", "out_errors"))
                writer.writerows(self.rows)
            print(f"AirDrop Wintun outbound timing file: {trace.name} ({len(self.rows)} samples, "
                  f"{self.omitted} omitted)", flush=True)
        except OSError as error:
            print(f"AirDrop Wintun outbound timing file unavailable: {error}", flush=True)


class UploadTcpSocketTrace:
    """Read Winsock counters while the separate upload worker can block in TLS."""

    # mstcpip.h defines SIO_TCP_INFO as _WSAIORW(IOC_VENDOR, 39).
    CONTROL_CODE = 0xD8000027
    FORMATS = (struct.Struct("<IIQB3x7IQQ5IB3x"), struct.Struct("<IIQB3x7IQQ5IB3xIIQIIQIIQ"))
    COUNTERS = (
        "state", "mss", "connection_ms", "timestamps_enabled", "rtt_us", "min_rtt_us",
        "bytes_in_flight", "cwnd", "snd_wnd", "rcv_wnd", "rcv_buf", "bytes_out", "bytes_in",
        "bytes_reordered", "bytes_retrans", "fast_retrans", "dup_acks_in", "timeout_episodes",
        "syn_retrans", "rwin_limit_transitions", "rwin_limit_ms", "rwin_limit_bytes",
        "cwnd_limit_transitions", "cwnd_limit_ms", "cwnd_limit_bytes",
        "sender_limit_transitions", "sender_limit_ms", "sender_limit_bytes",
    )
    INTERVAL_NS = 20_000_000
    LIMIT = 30_000

    def __init__(self):
        self.active = None
        self.lock = Lock()
        self.next_sample_ns = 0
        self.version = 1
        self.rows = []
        self.errors = {}
        self.omitted = 0
        winsock = c.WinDLL("ws2_32")
        self.ioctl = winsock.WSAIoctl
        self.ioctl.argtypes = (c.c_size_t, w.DWORD, c.c_void_p, w.DWORD, c.c_void_p,
                               w.DWORD, c.POINTER(w.DWORD), c.c_void_p, c.c_void_p)
        self.ioctl.restype = c.c_int
        self.last_error = winsock.WSAGetLastError
        self.last_error.argtypes = ()
        self.last_error.restype = c.c_int
        self.out = c.create_string_buffer(self.FORMATS[1].size)

    def start(self, connection, local_port):
        with self.lock:
            self.version = 1
            self.next_sample_ns = 0
            self.active = connection, local_port

    def stop(self, connection):
        with self.lock:
            if self.active is not None and self.active[0] is connection:
                self.active = None

    def read(self, sock, version):
        request = w.DWORD(version)
        returned = w.DWORD()
        layout = self.FORMATS[version]
        result = self.ioctl(sock.fileno(), self.CONTROL_CODE, c.byref(request), c.sizeof(request),
                            self.out, layout.size, c.byref(returned), None, None)
        if result != 0:
            raise OSError(self.last_error(), "SIO_TCP_INFO failed")
        if returned.value != layout.size:
            raise OSError(f"SIO_TCP_INFO v{version} returned {returned.value} of {layout.size} bytes")
        return layout.unpack_from(self.out)

    def sample(self):
        if self.active is None:
            return
        now = time.monotonic_ns()
        if now < self.next_sample_ns:
            return
        self.next_sample_ns = now + self.INTERVAL_NS
        # ScopedConnection.close clears the active socket under the same lock.
        with self.lock:
            if self.active is None:
                return
            connection, port = self.active
            if len(self.rows) >= self.LIMIT:
                self.omitted += 1
                return
            try:
                try:
                    values = self.read(connection.sock, self.version)
                except OSError:
                    if self.version == 0:
                        raise
                    self.version = 0
                    values = self.read(connection.sock, self.version)
            except OSError as error:
                self.errors[port] = error
                self.active = None
                return
            self.rows.append((now, time.monotonic_ns(), port, self.version, *values))

    def save(self):
        for port, error in self.errors.items():
            print(f"AirDrop Windows TCP counters unavailable for local-port={port}: {error}", flush=True)
        if not self.rows:
            return
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", newline="", delete=False,
                                             dir=ROOT / "build", prefix="awdl-upload-tcp-info-", suffix=".csv") as trace:
                writer = csv.writer(trace)
                writer.writerow(("sample_started_ns", "sample_ended_ns", "local_port", "version", *self.COUNTERS))
                writer.writerows(self.rows)
            versions = ",".join(str(version) for version in sorted({row[3] for row in self.rows}))
            print(f"AirDrop Windows TCP counters file: {trace.name} "
                  f"({len(self.rows)} samples, {self.omitted} omitted, versions={versions})", flush=True)
        except OSError as error:
            print(f"AirDrop Windows TCP counters file unavailable: {error}", flush=True)


class SendWatch(sender.Watch):
    def __init__(self, trace_upload_flow=False, desktop=None):
        self.desktop = desktop
        self.desktop_progress = None
        self.body_sent_at = None
        self.trace_upload_flow = trace_upload_flow
        self.tcp_trace = UploadTcpSocketTrace() if trace_upload_flow else None
        self.connection = None
        self.uploading = Event()
        self.local_port = None
        self.active_upload = None

    def connected(self, connection):
        self.connection = connection
        self.local_port = connection.sock.getsockname()[1]
        if self.desktop is not None:
            self.desktop.emit("waiting")
        print(f"AirDrop /Ask waiting for acceptance on the receiver (local-port={self.local_port})", flush=True)

    def accepted(self, total):
        started_ns = time.monotonic_ns()
        if self.desktop is not None:
            self.body_sent_at = None
            self.desktop_progress = DesktopProgress(self.desktop, total, started_ns / 1e9,
                                                    separate_body_samples=True)
        self.active_upload = (self.local_port, started_ns)
        if self.tcp_trace is not None:
            self.tcp_trace.start(self.connection, self.local_port)
        print(f"AirDrop /Ask accepted ({total} file bytes)", flush=True)
        self.uploading.set()

    def body_written(self, done):
        if self.desktop_progress is not None:
            self.desktop_progress.body_written(done)

    def uploaded(self):
        if self.desktop_progress is not None:
            self.body_sent_at = time.monotonic()
        print("AirDrop upload body sent, waiting for the receiver", flush=True)


def discover_and_send(local, peer, port, index, context, files, watch, prepare_driver=None,
                      wait_ba=None):
    discover(local, peer, port, index, context)
    if not files:
        return
    if prepare_driver is not None:
        prepare_driver.call(UPLOAD_DISCOVERED)
        print("AirDrop /Discover completed. Early Block Ack negotiation enabled.", flush=True)
        if wait_ba is not None:
            wait_ba()

    def connection_factory(address, port, scope, context, timeout):
        return ScopedConnection(local, address, port, scope, context, min(timeout, 45),
                                tcp_trace=watch.tcp_trace)

    name = socket.gethostname().split(".", 1)[0]
    timing = UploadBodyTiming() if watch.trace_upload_flow else None
    try:
        outcome = sender.deliver(str(peer), port, index, context, name, files, watch,
                                 connection_factory=connection_factory,
                                 upload_timing=timing.add if timing is not None else None,
                                 body_progress=watch.body_written if watch.desktop is not None else None)
        if outcome == sender.DECLINED:
            raise RuntimeError("The receiver did not accept AirDrop /Ask")
        if outcome != sender.SENT:
            raise RuntimeError("The receiver did not confirm AirDrop /Upload")
        print("AirDrop /Upload response: HTTP 200", flush=True)
        print(f"AirDrop file sent: {files[0].name!r} ({files[0].size} bytes)", flush=True)
        if watch.desktop_progress is not None:
            watch.desktop_progress.finish(watch.body_sent_at)
    finally:
        if timing is not None:
            timing.save(watch.local_port)


def read_ba_status(driver):
    return BA_STATUS_STRUCT.unpack(driver.call(BA_STATUS, size=BA_STATUS_STRUCT.size))


def describe_ba_status(values):
    (state, requested, pending, sta_gen, wcid, wcid_gen, tid, disable, enable,
     delba, delba_sub, delba_tx, delba_acked, low, high, basic, he, ra,
     wtbl_reg, wtbl_reset) = values
    when = ((high << 32) | low) / 1e7
    return (f"state={BA_STATE_NAMES.get(state, state)} requested={requested} pending={pending} "
            f"sta-gen={sta_gen} wcid={wcid} wcid-gen={wcid_gen} tid={tid} "
            f"disable=0x{disable:08x} enable=0x{enable:08x} delba=0x{delba:08x} "
            f"delba-tx={delba_tx}/{delba_sub} delba-acked={delba_acked} "
            f"updates(basic/he/ra)={basic}/{he}/{ra} wtbl(reg/reset)={wtbl_reg}/{wtbl_reset} "
            f"at={when:.3f}s")


def wait_ba_state(driver, wanted, label, timeout=3.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        values = read_ba_status(driver)
        state = values[0]
        if state != last:
            print(f"AirDrop BA {label}: {describe_ba_status(values)}", flush=True)
            last = state
        if state == wanted:
            return values
        if state == 5:
            raise RuntimeError(f"AirDrop BA {label} failed: {describe_ba_status(values)}")
        time.sleep(0.01)
    raise RuntimeError(f"AirDrop BA {label} did not reach state {wanted}")


def send_repeated(local, peer, port, index, context, files, watch, prepare_driver, count, gap,
                  driver, reset_mode):
    mode = {"none": 0, "ba": BA_RESET_BA_ONLY, "peer": BA_RESET_PEER}[reset_mode]
    if mode:
        print(f"AirDrop repeat: BA reset mode {reset_mode}; "
              f"initial {describe_ba_status(read_ba_status(driver))}", flush=True)
    for number in range(1, count + 1):
        if number > 1 and gap > 0:
            print(f"AirDrop repeat: waiting {gap:g} s before transfer {number}/{count}", flush=True)
            time.sleep(gap)
        print(f"AirDrop repeat: transfer {number}/{count} starting", flush=True)
        wait_ba = None
        if number > 1 and mode:
            wait_ba = lambda: wait_ba_state(driver, 1, "rebuild")
        discover_and_send(local, peer, port, index, context, files, watch, prepare_driver,
                          wait_ba=wait_ba)
        print(f"AirDrop repeat: transfer {number}/{count} complete", flush=True)
        if mode and number < count:
            before = read_ba_status(driver)
            print(f"AirDrop repeat: BA before reset {number}: {describe_ba_status(before)}", flush=True)
            driver.call(BA_RESET, data=struct.pack("<I", mode))
            after = wait_ba_state(driver, 3, f"reset {number}")
            if mode == BA_RESET_BA_ONLY:
                if after[3] != before[3] or after[4] != before[4] or after[5] != before[5]:
                    raise RuntimeError("BA-only reset changed the STA or WCID generation")
                if after[15:20] != before[15:20]:
                    raise RuntimeError("BA-only reset touched STA_REC or WTBL records")
            print(f"AirDrop repeat: BA reset {number} complete: {describe_ba_status(after)}", flush=True)


def split_radio_packets(data, limit, mtu):
    """Read the packed version-6 datagrams returned by the driver."""
    packets = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < 40 or len(packets) == limit or data[offset] >> 4 != 6:
            raise RuntimeError("MT7922 returned a malformed receive batch")
        size = 40 + int.from_bytes(data[offset + 4:offset + 6], "big")
        if size > mtu or offset + size > len(data):
            raise RuntimeError("MT7922 returned an invalid packet length in a receive batch")
        packets.append(data[offset:offset + size])
        offset += size
    return packets


def upload_batch_phase(acked_bytes):
    return next((name, limit) for threshold, name, limit in reversed(UPLOAD_BATCH_COMPARISON)
                if acked_bytes >= threshold)


@dataclass
class UploadBatchPhaseTiming:
    limit: int
    first_acked_bytes: int
    first_call_ns: int
    last_return_ns: int = 0
    requests: int = 0
    packets: int = 0
    full: int = 0


def relay(driver, session, tunnel, local, peer, port, operation, announcement=None,
          receive_watch=None, receive_batch_size=None, upload_reply_wait=None,
          batch_radio_receive=False, upload_ack_rate=None, radio_batch_size=DEFAULT_RADIO_BATCH_PACKETS,
          winawdl_merge=None, trace_upload_flow=False, pipeline_receive=False, mtu=MTU, quic_probe=None,
          send_watch=None, upload_batch_size=0, compare_send_batches=False, desktop=None):
    pending = None
    upload_packets = []
    upload_payload = b""
    upload_batches = upload_packets_sent = upload_batch_max = upload_batch_full = 0
    pending_batch_limit = upload_batch_size
    pending_phase = None
    pending_phase_progress = 0
    phase_runs = {}
    pending_ack_read_ns = pending_send_read_ns = None
    upload_reads_ns = []
    sent = received = skipped = drops = announced = 0
    syn = syn_ack = rst = corrected = pure_ack = 0
    injected = combined = absorbed = rejected_batches = 0
    outgoing_wakes = timer_wakes = 0
    radio_reads = radio_nonempty = radio_packets = radio_max = radio_full = 0
    busy_fallbacks = busy_retry_ns = 0
    upload_idle_waits_skipped = 0
    ack_exchange = EXCHANGE_UPLOAD_ACK.get(upload_ack_rate)
    batch = winawdl_merge
    batch_limit = WINAWDL_MAX_SEGMENTS if winawdl_merge is not None else receive_batch_size
    if batch is None and receive_batch_size is not None:
        batch = TcpReceiveBatch(port, receive_batch_size)
    batch_sizes = [0] * (batch_limit + 1) if batch_limit is not None else None
    upload_data_segments = upload_data_pauses = 0
    last_upload_data = None
    longest_upload_data_gap = 0.0
    longest_gap_after = 0.0
    receiving = receive_watch is not None
    upload_close = UploadTcpClose() if receiving else None
    upload_flow = UploadTcpFlow() if receiving and trace_upload_flow else None
    ack_timing = UploadAckTiming() if receiving and trace_upload_flow else None
    send_timing = UploadAckTiming() if send_watch is not None and trace_upload_flow else None
    tcp_trace = send_watch.tcp_trace if send_watch is not None else None
    wintun_trace = WintunOutboundTrace(tunnel) if tcp_trace is not None and driver.shared_wifi else None
    send_flows = {}
    trace_receive = receiving and (driver.shared_wifi or trace_upload_flow)
    handshakes = {}
    tx_port_offset, rx_port_offset = (40, 42) if receiving else (42, 40)
    service_port = struct.pack("!H", port)
    prefetch_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="awdl-read") if pipeline_receive else None
    prefetched = None
    priority_exchange = None
    prefetch_reads = prefetch_nonempty = prefetch_ready = priority_tx_requests = 0

    def trace_tcp(packet, flags, connection_port, *, from_peer):
        trace = handshakes.get(connection_port)
        if trace is None:
            if len(handshakes) >= 32:
                return
            trace = handshakes[connection_port] = TcpHandshakeTrace()
        trace.observe(packet, flags, from_peer=from_peer)

    def trace_upload_send(packet, flags, local_port, observed_ns, *, from_peer, segments=1,
                          call_ns=None, return_ns=None):
        active_upload = send_watch.active_upload
        if active_upload is None or active_upload[0] != local_port:
            return
        flow = send_flows.get(local_port)
        if flow is None:
            flow = send_flows[local_port] = UploadTcpFlow()
        seconds = (observed_ns - active_upload[1]) / 1e9
        if from_peer:
            if not flags & TCP_ACK or flags & (TCP_SYN | TCP_RST):
                return
            handshake = handshakes.get(local_port)
            scale = handshake.peer_window_scale if handshake is not None else None
            has_sack = flow.sent(packet, seconds, scale)
            if send_timing is not None:
                send_timing.add("rx_sack" if has_sack else "rx_ack", packet, local_port, 1, observed_ns)
            if send_watch.desktop_progress is not None:
                send_watch.desktop_progress.update(flow.acked_bytes, observed_ns / 1e9)
        else:
            payload = len(packet) - 40 - (packet[52] >> 4) * 4
            if payload <= 0:
                return
            flow.received(payload, seconds)
            send_timing.add("tx_queued", packet, local_port, segments, observed_ns, call_ns, return_ns)

    def inject_into_windows(packet, wire_tail, segments):
        accepted = tunnel.incoming(packet)
        if (accepted and ack_timing is not None and tcp_flags(wire_tail) is not None
                and wire_tail[42:44] == service_port):
            peer_port = int.from_bytes(wire_tail[40:42], "big")
            if (peer_port == receive_watch.upload_data_port
                    and receive_watch.upload_reply_port is None
                    and len(wire_tail) > 40 + (wire_tail[52] >> 4) * 4):
                ack_timing.add("rx_injected", wire_tail, peer_port, segments, time.monotonic_ns())
        return accepted

    def inject(segments, merged=None):
        nonlocal received, drops, injected, combined, absorbed, rejected_batches
        if len(segments) > 1:
            try:
                accepted = inject_into_windows(merged if merged is not None else coalesced_packet(segments),
                                               segments[-1], len(segments))
            except OSError as error:
                if error.winerror != ERROR_INVALID_PARAMETER:
                    raise
                accepted = False
            if accepted:
                received += len(segments)
                injected += 1
                combined += 1
                absorbed += len(segments) - 1
                batch_sizes[len(segments)] += 1
                return
            rejected_batches += 1
        for packet in segments:
            if inject_into_windows(packet, packet, 1):
                received += 1
                injected += 1
            else:
                drops += 1

    def inject_batches(batches):
        for item in batches:
            if winawdl_merge is None:
                inject(item)
            else:
                inject(*item)

    def take_outgoing():
        nonlocal pending, pending_ack_read_ns, pending_send_read_ns, skipped, corrected
        if pending is not None:
            return
        packet = tunnel.outgoing()
        if packet is not None:
            if for_link(packet, local, peer, mtu):
                pending = packet
                flags = tcp_flags(packet)
                if flags is not None and packet[tx_port_offset:tx_port_offset + 2] == service_port:
                    if (ack_timing is not None and flags == TCP_ACK
                            and len(packet) == 40 + (packet[52] >> 4) * 4
                            and receive_watch.upload_reply_port is None
                            and int.from_bytes(packet[42:44], "big") == receive_watch.upload_data_port):
                        pending_ack_read_ns = time.monotonic_ns()
                    if send_timing is not None and send_watch.active_upload is not None:
                        pending_send_read_ns = time.monotonic_ns()
                    pending = with_tcp_checksum(packet)
                    corrected += pending != packet
            else:
                skipped += 1
        if pending is None and receiving and announcement.due():
            pending = announcement.packet

    def take_upload_batch(limit):
        nonlocal pending, pending_send_read_ns, upload_payload
        for _ in range(limit):
            take_outgoing()
            if pending is None:
                break
            upload_packets.append(pending)
            if send_timing is not None:
                upload_reads_ns.append(pending_send_read_ns)
            pending = pending_send_read_ns = None
        upload_payload = b"".join(upload_packets)

    def upload_batch_active():
        return (batch_radio_receive and receive_watch.upload_data_port is not None
                and receive_watch.upload_reply_port is None and not receive_watch.result.done())

    def exchange_packets(exchange, capacity, packet, timed):
        nonlocal busy_fallbacks, busy_retry_ns
        call_ns = time.monotonic_ns() if timed else None
        try:
            incoming = driver.call(exchange, data=packet or b"", size=capacity)
        except OSError as error:
            if error.winerror not in (ERROR_BUSY, tunnel.busy_error):
                raise
            retry_ns = time.monotonic_ns()
            incoming = driver.call(exchange, size=capacity)
            busy_fallbacks += 1
            busy_retry_ns += time.monotonic_ns() - retry_ns
            return incoming, False, None, None
        return incoming, True, call_ns, time.monotonic_ns() if timed else None

    try:
        while not operation.done():
            if desktop is not None and desktop.stopping.is_set():
                if send_watch is not None and send_watch.connection is not None:
                    try:
                        send_watch.connection.sock.shutdown(socket.SHUT_RDWR)
                    except (AttributeError, OSError):
                        pass
                raise Stopped()
            if (quic_probe is not None and quic_probe.request_ready.is_set()
                    and not receive_watch.ask_started.is_set()):
                print("AirDrop QUIC probe recorded an HTTP/3 request and returned HTTP 501", flush=True)
                break
            if session.done():
                raise RuntimeError("MT7922 link stopped during the AirDrop operation")
            if tcp_trace is not None:
                tcp_trace.sample()
            if wintun_trace is not None:
                wintun_trace.sample()
            if batch is not None:
                inject_batches(batch.flush_due(time.monotonic()))
            used_read_ahead = prefetched is not None or priority_exchange is not None
            sending_upload = batching_upload = False
            if prefetched is not None:
                radio_batch = True
                ready = prefetched.done()
                incoming, submitted, driver_call_ns, driver_return_ns = prefetched.result()
                prefetch_nonempty += bool(incoming)
                prefetch_ready += ready and bool(incoming)
                prefetched = None
            elif priority_exchange is not None:
                radio_batch = True
                incoming, submitted, driver_call_ns, driver_return_ns = priority_exchange
                priority_exchange = None
            else:
                sending_upload = (driver.shared_wifi and send_watch is not None
                                  and send_watch.uploading.is_set())
                batching_upload = sending_upload and upload_batch_size > 0
                if batching_upload:
                    if not upload_packets:
                        if compare_send_batches:
                            active = send_watch.active_upload
                            flow = send_flows.get(active[0]) if active is not None else None
                            pending_phase_progress = flow.acked_bytes if flow is not None else 0
                            pending_phase, pending_batch_limit = upload_batch_phase(pending_phase_progress)
                        take_upload_batch(pending_batch_limit)
                else:
                    take_outgoing()
                radio_batch = sending_upload or upload_batch_active()
                exchange = EXCHANGE_BATCH if radio_batch else EXCHANGE
                if (radio_batch and ack_exchange is not None and pending is not None and tcp_flags(pending) == TCP_ACK
                        and int.from_bytes(pending[42:44], "big") == receive_watch.upload_data_port):
                    exchange = ack_exchange
                capacity = mtu * radio_batch_size if radio_batch else mtu
                outgoing = upload_payload if batching_upload else pending
                timed_send = send_timing is not None and sending_upload and bool(outgoing)
                incoming, submitted, driver_call_ns, driver_return_ns = exchange_packets(
                    exchange, capacity, outgoing, pending_ack_read_ns is not None or timed_send)
            upload_submitted = sending_upload and submitted and (bool(upload_packets) if batching_upload else pending is not None)
            radio_reads += radio_batch * (1 if submitted else 2)
            if submitted:
                if pending_ack_read_ns is not None:
                    ack_timing.add("tx_ack_queued", pending, int.from_bytes(pending[42:44], "big"), 1,
                                   pending_ack_read_ns, driver_call_ns, driver_return_ns)
                    pending_ack_read_ns = None
                if receiving and pending is announcement.packet:
                    announcement.submitted()
                    announced += 1
                else:
                    if batching_upload:
                        submitted_packets = upload_packets
                    else:
                        submitted_packets = (pending,) if pending is not None else ()
                    for packet_index, packet in enumerate(submitted_packets):
                        sent += 1
                        if quic_probe is not None:
                            quic_probe.observe_outgoing(packet)
                        flags = tcp_flags(packet)
                        if flags is not None and packet[tx_port_offset:tx_port_offset + 2] == service_port:
                            if receiving:
                                syn_ack += (flags & 0x12) == 0x12
                                pure_ack += flags == TCP_ACK and len(packet) == 40 + (packet[52] >> 4) * 4
                                remote_port = int.from_bytes(packet[42:44], "big")
                                if trace_receive and not receive_watch.uploading.is_set():
                                    trace_tcp(packet, flags, remote_port, from_peer=False)
                                if remote_port == receive_watch.upload_reply_port:
                                    upload_close.sent(packet, flags)
                                elif (upload_flow is not None and remote_port == receive_watch.upload_data_port
                                      and flags == TCP_ACK and not receive_watch.result.done()):
                                    handshake = handshakes.get(remote_port)
                                    scale = handshake.windows_window_scale if handshake is not None else None
                                    submitted_at = (driver_return_ns / 1e9 if driver_return_ns is not None
                                                    else time.monotonic())
                                    upload_flow.sent(packet, submitted_at - receive_watch.upload_started_at, scale)
                            else:
                                syn += (flags & 0x12) == 0x02
                                if driver.shared_wifi:
                                    local_port = int.from_bytes(packet[40:42], "big")
                                    trace_tcp(packet, flags, local_port, from_peer=False)
                                    if send_timing is not None and sending_upload:
                                        read_ns = (upload_reads_ns[packet_index] if batching_upload
                                                   else pending_send_read_ns)
                                        if read_ns is not None:
                                            trace_upload_send(packet, flags, local_port, read_ns, from_peer=False,
                                                              segments=len(submitted_packets),
                                                              call_ns=driver_call_ns, return_ns=driver_return_ns)
                if batching_upload and upload_packets:
                    count = len(upload_packets)
                    upload_batches += 1
                    upload_packets_sent += count
                    upload_batch_max = max(upload_batch_max, count)
                    upload_batch_full += count == pending_batch_limit
                    if compare_send_batches:
                        phase = phase_runs.get(pending_phase)
                        if phase is None:
                            phase = phase_runs[pending_phase] = UploadBatchPhaseTiming(
                                pending_batch_limit, pending_phase_progress, driver_call_ns)
                        phase.last_return_ns = driver_return_ns
                        phase.requests += 1
                        phase.packets += count
                        phase.full += count == pending_batch_limit
                    upload_packets.clear()
                    upload_reads_ns.clear()
                    upload_payload = b""
                pending = pending_send_read_ns = None
            if radio_batch:
                packets = split_radio_packets(incoming, radio_batch_size, mtu)
                radio_nonempty += bool(packets)
                radio_packets += len(packets)
                radio_max = max(radio_max, len(packets))
                full_batch = len(packets) == radio_batch_size
                radio_full += full_batch
                if prefetch_pool is not None and full_batch and upload_batch_active():
                    take_outgoing()
                    capacity = mtu * radio_batch_size
                    if pending is None:
                        prefetched = prefetch_pool.submit(exchange_packets, EXCHANGE_BATCH, capacity, None, False)
                        prefetch_reads += 1
                    else:
                        # Submit ready ACKs on this thread. Deliver any returned RX after the current batch.
                        priority_exchange = exchange_packets(
                            EXCHANGE_BATCH, capacity, pending, pending_ack_read_ns is not None)
                        priority_tx_requests += 1
            else:
                packets = (incoming,) if incoming else ()
            for packet in packets:
                if receiving and announcement.saw_query(packet, peer):
                    continue
                if for_link(packet, peer, local, mtu):
                    if quic_probe is not None:
                        quic_probe.observe_incoming(packet)
                    flags = tcp_flags(packet)
                    if flags is not None and packet[rx_port_offset:rx_port_offset + 2] == service_port:
                        if receiving:
                            syn += (flags & 0x12) == 0x02
                            if receive_watch.first_syn_at is None and (flags & 0x12) == 0x02:
                                receive_watch.first_syn_at = time.monotonic()
                            remote_port = int.from_bytes(packet[40:42], "big")
                            if trace_receive and not receive_watch.uploading.is_set():
                                trace_tcp(packet, flags, remote_port, from_peer=True)
                            if remote_port == receive_watch.upload_reply_port:
                                upload_close.received(packet, flags)
                            elif (remote_port == receive_watch.upload_data_port and not receive_watch.result.done()
                                  and len(packet) > 40 + (packet[52] >> 4) * 4):
                                now = time.monotonic()
                                if last_upload_data is not None:
                                    gap = now - last_upload_data
                                    if gap > longest_upload_data_gap:
                                        longest_upload_data_gap = gap
                                        longest_gap_after = last_upload_data - receive_watch.upload_started_at
                                    upload_data_pauses += gap >= 0.5
                                last_upload_data = now
                                upload_data_segments += 1
                                if upload_flow is not None:
                                    payload = len(packet) - 40 - (packet[52] >> 4) * 4
                                    upload_flow.received(payload, now - receive_watch.upload_started_at)
                        else:
                            syn_ack += (flags & 0x12) == 0x12
                            if driver.shared_wifi:
                                local_port = int.from_bytes(packet[42:44], "big")
                                trace_tcp(packet, flags, local_port, from_peer=True)
                                if send_timing is not None or (send_watch is not None and desktop is not None):
                                    trace_upload_send(packet, flags, local_port, time.monotonic_ns(), from_peer=True)
                        rst += (flags & 0x04) != 0
                    if (batch is not None and receive_watch.uploading.is_set()
                            and not receive_watch.result.done() and receive_watch.upload_reply_port is None):
                        inject_batches(batch.offer(packet, time.monotonic()))
                    else:
                        if batch is not None:
                            inject_batches(batch.flush())
                        inject((packet,))
            if not packets:
                # An empty receive does not mean the upload has run out of outgoing data.
                if upload_submitted:
                    upload_idle_waits_skipped += 1
                    continue
                if winawdl_merge is not None:
                    inject_batches(batch.flush_due(time.monotonic()))
                    if batch.pending:
                        continue
                elif batch is not None:
                    inject_batches(batch.flush())
                if (upload_reply_wait is not None and receive_watch.upload_data_port is not None
                        and receive_watch.upload_reply_port is None and not receive_watch.result.done()):
                    if upload_reply_wait.wait():
                        outgoing_wakes += 1
                    else:
                        timer_wakes += 1
                elif not used_read_ahead:
                    time.sleep(0.001)
        if batch is not None:
            inject_batches(batch.flush())
        if operation.done():
            operation.result()
    finally:
        if wintun_trace is not None:
            wintun_trace.sample(final=True)
        if prefetch_pool is not None:
            prefetch_pool.shutdown(wait=True, cancel_futures=True)
        print(f"AWDL IPv6 relay: queued-TX={sent} delivered-RX={received} skipped={skipped} dropped={drops}", flush=True)
        if receiving:
            print(f"AirDrop receiver mDNS: queued={announced} queries={announcement.queries}", flush=True)
            print(f"AirDrop discovery timing since ready: {receive_watch.discovery_timing(announcement)}", flush=True)
        if quic_probe is not None:
            quic_probe.report()
        print(f"Windows TCP: SYN={syn} SYN-ACK={syn_ack} RST={rst} checksum-corrected={corrected}", flush=True)
        if trace_receive:
            print(f"AirDrop TCP diagnostics: connections={len(handshakes)}", flush=True)
            for remote_port, trace in handshakes.items():
                print(f"AirDrop TCP peer-port={remote_port}: {trace.summary()}", flush=True)
        elif handshakes:
            for local_port, trace in handshakes.items():
                print(f"AirDrop send TCP local-port={local_port}: {trace.data_summary()}", flush=True)
        if upload_batch_size:
            avg = upload_packets_sent / max(upload_batches, 1)
            limit = "16/32" if compare_send_batches else upload_batch_size
            print(f"AirDrop radio send batches: requests={upload_batches} packets={upload_packets_sent} "
                  f"avg={avg:.1f} largest={upload_batch_max} full={upload_batch_full} limit={limit}", flush=True)
        for name, phase in phase_runs.items():
            print(f"AirDrop upload batch comparison: phase={name} limit={phase.limit} "
                  f"first-acked-bytes={phase.first_acked_bytes} first-call-ns={phase.first_call_ns} "
                  f"last-return-ns={phase.last_return_ns} requests={phase.requests} "
                  f"packets={phase.packets} full={phase.full}", flush=True)
        if send_watch is not None and driver.shared_wifi and trace_upload_flow:
            print(f"AirDrop send path: idle-waits-skipped={upload_idle_waits_skipped} "
                  f"busy-fallbacks={busy_fallbacks} retry-wait-ms={busy_retry_ns / 1e6:.1f}", flush=True)
        for local_port, flow in send_flows.items():
            for interval in flow.summaries(payload_label="TX-queued-bytes"):
                print(f"AirDrop upload send flow: local-port={local_port} {interval}", flush=True)
        if tcp_trace is not None:
            tcp_trace.save()
        if wintun_trace is not None:
            wintun_trace.save()
        if send_timing is not None and send_timing.rows:
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", newline="", delete=False,
                                                 dir=ROOT / "build", prefix="awdl-upload-relay-", suffix=".csv") as trace:
                    send_timing.write(trace, port_column="local_port")
                print(f"AirDrop upload relay timing file: {trace.name} ({len(send_timing.rows)} events, "
                      f"{send_timing.omitted} omitted)", flush=True)
            except OSError as error:
                print(f"AirDrop upload relay timing file unavailable: {error}", flush=True)
        if receiving:
            print(f"AirDrop TCP receive: injected={injected} combined-packets={combined} "
                  f"segments-saved={absorbed} batch-fallbacks={rejected_batches} outbound-ACK-only={pure_ack}", flush=True)
            if batch_radio_receive:
                print(f"AirDrop radio receive batches: requests={radio_reads} nonempty={radio_nonempty} "
                      f"packets={radio_packets} largest={radio_max} full={radio_full}", flush=True)
            print(f"AirDrop receive path: busy-fallbacks={busy_fallbacks} "
                  f"retry-wait-ms={busy_retry_ns / 1e6:.1f}", flush=True)
            if pipeline_receive:
                print(f"AirDrop receive pipeline: prefetch-requests={prefetch_reads} "
                      f"nonempty={prefetch_nonempty} ready-at-handoff={prefetch_ready} "
                      f"priority-tx-requests={priority_tx_requests}", flush=True)
            if upload_reply_wait is not None:
                print(f"AirDrop upload reply wake: outgoing-ready={outgoing_wakes} timer={timer_wakes}", flush=True)
            if combined:
                counts = " ".join(f"{size}={batch_sizes[size]}" for size in range(2, len(batch_sizes))
                                  if batch_sizes[size])
                print(f"AirDrop receive batch sizes: {counts}", flush=True)
            if upload_data_segments:
                print(f"AirDrop upload TCP arrivals: data-segments={upload_data_segments} "
                      f"longest-gap={longest_upload_data_gap:.3f}s gaps-at-least-0.5s={upload_data_pauses} "
                      f"longest-gap-after={longest_gap_after:.3f}s", flush=True)
            if upload_flow is not None:
                for interval in upload_flow.summaries():
                    print(f"AirDrop upload TCP flow: {interval}", flush=True)
            if ack_timing is not None and ack_timing.rows:
                try:
                    with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", newline="", delete=False,
                                                     dir=ROOT / "build", prefix="awdl-ack-", suffix=".csv") as trace:
                        ack_timing.write(trace)
                    print(f"AirDrop ACK timing file: {trace.name} ({len(ack_timing.rows)} events, "
                          f"{ack_timing.omitted} omitted)", flush=True)
                except OSError as error:
                    print(f"AirDrop ACK timing file unavailable: {error}", flush=True)
            stage_timing = receive_watch.upload_stage_trace
            if stage_timing is not None:
                for summary in stage_timing.summaries():
                    print(f"AirDrop upload stage: {summary}", flush=True)
                try:
                    with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", newline="", delete=False,
                                                     dir=ROOT / "build", prefix="awdl-upload-stages-", suffix=".csv") as trace:
                        stage_timing.write_csv(trace)
                    print(f"AirDrop upload stage file: {trace.name} ({len(stage_timing.rows)} events, "
                          f"{stage_timing.omitted} omitted)", flush=True)
                except OSError as error:
                    print(f"AirDrop upload stage file unavailable: {error}", flush=True)
            if receive_watch.upload_reply_port is not None:
                print(f"AirDrop upload TCP close: reply-segments={upload_close.reply_segments} "
                      f"FIN-queued={int(upload_close.fin_ack is not None)} FIN-acked={int(upload_close.fin_acked)} "
                      f"peer-FIN={int(upload_close.peer_fin)} peer-RST={int(upload_close.peer_reset)}", flush=True)


def record_startup(log, stage, observe=False):
    if log is None:
        return
    now = datetime.now().astimezone().isoformat(timespec="milliseconds")
    with log.open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerow((now, stage))
    hold = f" (observing for {STARTUP_OBSERVATION_SECONDS} seconds)" if observe else ""
    print(f"AirDrop startup: {now} {stage}{hold}", flush=True)
    if observe:
        time.sleep(STARTUP_OBSERVATION_SECONDS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("peer_mac")
    parser.add_argument("port", type=int)
    parser.add_argument("--tx-rate", type=int, choices=(6, 12), default=6, help="fixed OFDM rate in Mbps")
    parser.add_argument("--wake-peer", action="store_true", help="advertise AirDrop availability over Bluetooth")
    parser.add_argument("--shared-wifi", action="store_true", help="use the associated WiFiCx station")
    parser.add_argument("--desktop-events", action="store_true", help=argparse.SUPPRESS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--send-file", type=Path, help="send this file after discovering the selected peer")
    mode.add_argument("--receive-into", type=Path, help="receive one approved transfer into this folder")
    parser.add_argument("--coalesce-receive", action="store_true", help="merge adjacent TCP data during an upload")
    parser.add_argument("--receive-batch-size", type=int, choices=tuple(BATCH_BYTE_LIMITS),
                        help="maximum consecutive TCP segments merged per upload packet")
    parser.add_argument("--wake-on-receive-ack", action="store_true",
                        help="wake early when Windows queues an outbound packet during upload")
    parser.add_argument("--advertise-vht", action="store_true",
                        help="advertise two-stream 80 MHz VHT capabilities when receiving")
    parser.add_argument("--batch-radio-receive", action="store_true",
                        help="read multiple queued radio packets at once during upload")
    parser.add_argument("--pipeline-receive", action="store_true",
                        help="prefetch after a full AWDL batch and submit queued replies directly")
    parser.add_argument("--radio-batch-size", type=int, choices=RADIO_BATCH_SIZES,
                        help="maximum number of queued radio packets per read")
    parser.add_argument("--fast-upload-ack", action="store_const", const=24, dest="upload_ack_rate",
                        help="transmit pure upload ACKs at 24 Mbps")
    parser.add_argument("--upload-ack-rate", type=int, choices=tuple(EXCHANGE_UPLOAD_ACK),
                        help="OFDM rate for pure upload ACKs in Mbps")
    parser.add_argument("--short-upload-poll", action="store_true",
                        help="request 250 microsecond driver polls during active upload bursts")
    parser.add_argument("--winawdl-merge", action="store_true",
                        help="merge upload bursts with the native WinAWDL TCP merger")
    parser.add_argument("--send-fast-profile", action="store_true",
                        help="use the shared Wi-Fi fast sender with MTU 1500 and up to 64 in-flight frames")
    parser.add_argument("--send-he-2-stream", action="store_true",
                        help="allow HE MCS 0-11 on two streams for a verified fast-send peer")
    parser.add_argument("--send-amsdu-3", action="store_true",
                        help="allow up to three queued IPv6 packets per A-MSDU with a compatible peer")
    parser.add_argument("--prepare-upload-early", action="store_true",
                        help="prepare the HE peer early and request Block Ack after /Discover succeeds")
    parser.add_argument("--batch-radio-send", action="store_true",
                        help="submit ready upload packets together with the fast sender")
    parser.add_argument("--radio-send-batch-size", type=int,
                        choices=(DEFAULT_UPLOAD_BATCH_PACKETS, 32, MAX_UPLOAD_BATCH_PACKETS),
                        help="maximum packets per fast-sender request (default 16)")
    parser.add_argument("--compare-send-batches", action="store_true",
                        help="compare 16, 32 and 16 packet limits in one traced upload")
    parser.add_argument("--send-repeat", type=int, default=1,
                        help="send the selected file N times on one AWDL link (default 1)")
    parser.add_argument("--send-repeat-gap", type=float, default=0.0,
                        help="seconds to idle between repeated sends")
    parser.add_argument("--send-repeat-reset", choices=("none", "ba", "peer"), default="none",
                        help="BA lifecycle reset between repeated sends")
    shared_mtu = parser.add_mutually_exclusive_group()
    shared_mtu.add_argument("--shared-mtu-1450", dest="shared_mtu", action="store_const",
                            const=WINAWDL_MTU, help="use WinAWDL's IPv6 MTU on the shared receive link")
    shared_mtu.add_argument("--shared-mtu-1500", dest="shared_mtu", action="store_const",
                            const=APPLE_PROFILE_MTU, help="use a 1500-byte IPv6 MTU on the shared receive link")
    parser.add_argument("--use-rx-reorder", action="store_true",
                        help="use PulseIt Block Ack reordering for received QoS data")
    parser.add_argument("--advertise-he", action="store_true",
                        help="advertise HE capability on the shared 80 MHz receive link")
    parser.add_argument("--rx-queue-256", action="store_true",
                        help="size the shared receive ring for 256 queued packets (default 128)")
    parser.add_argument("--use-rx-amsdu", action="store_true",
                        help="negotiate A-MSDU reception within shared Wi-Fi Block Ack sessions")
    parser.add_argument("--peer-wcid-ack", action="store_true",
                        help="use the registered Wi-Fi peer for pure AirDrop upload ACKs")
    wintun_ring = parser.add_mutually_exclusive_group()
    wintun_ring.add_argument("--wintun-ring-kib", type=int, choices=(256, 4096),
                             help="override Wintun ring capacity for a shared-Wi-Fi send")
    wintun_ring.add_argument("--large-wintun-ring", dest="wintun_ring_kib", action="store_const", const=4096,
                             help="use 4 MiB Wintun rings for a shared-Wi-Fi send")
    parser.add_argument("--trace-upload-flow", action="store_true",
                        help="trace TCP data, ACK progress and driver queue admission during an upload every five seconds")
    parser.add_argument("--startup-log", type=Path,
                        help="record Wi-Fi observation periods before Bluetooth and radio startup")
    parser.add_argument("--no-startup-hold", action="store_true",
                        help="record startup stages without waiting between them")
    parser.add_argument("--decode-during-receive", action="store_true",
                        help="keep the decoded archive during an AirDrop upload")
    parser.add_argument("--memory-archive", action="store_true",
                        help="spool the decoded archive in memory up to 64 MiB before using disk")
    parser.add_argument("--fast-upload-tail", action="store_true",
                        help="wait 100 ms for the HTTP end marker after a complete archive")
    parser.add_argument("--receive-buffer-mib", type=int, choices=(1, 2, 4, 8), default=0,
                        help="set SO_RCVBUF on the AirDrop receiver before the handshake")
    parser.add_argument("--quic-probe", action="store_true",
                        help="advertise HTTP/3 and record the first request from the selected peer")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    desktop = DesktopSession() if args.desktop_events else None
    if args.quic_probe and (not args.shared_wifi or args.receive_into is None):
        parser.error("--quic-probe requires --shared-wifi and --receive-into")
    if args.startup_log is not None and (not args.shared_wifi or args.receive_into is None or args.check):
        parser.error("--startup-log requires a live shared Wi-Fi receiver")
    if args.no_startup_hold and args.startup_log is None:
        parser.error("--no-startup-hold requires --startup-log")
    if args.shared_wifi:
        args.wake_peer = True
        if args.tx_rate != 6 or args.upload_ack_rate is not None or args.short_upload_poll:
            parser.error("Shared Wi-Fi uses 6 Mbps OFDM and the associated station's radio profile")
    peer_mac = bytes.fromhex(args.peer_mac.replace(":", ""))
    if len(peer_mac) != 6 or not any(peer_mac) or peer_mac[0] & 1 or not 1 <= args.port <= 65535:
        parser.error("Expected a unicast peer MAC and a TCP port from 1 to 65535")
    destination = args.receive_into.resolve() if args.receive_into is not None else None
    if destination is not None and destination.exists() and not destination.is_dir():
        parser.error("Receiving requires a destination folder")
    if args.shared_mtu is not None and not (args.shared_wifi and destination is not None):
        parser.error("Shared receive MTU requires --shared-wifi and --receive-into")
    if args.send_fast_profile and not (args.shared_wifi and args.send_file and destination is None):
        parser.error("--send-fast-profile requires --shared-wifi and --send-file")
    if args.send_repeat < 1 or args.send_repeat > 10:
        parser.error("--send-repeat must be between 1 and 10")
    if args.send_repeat > 1 and args.send_file is None:
        parser.error("--send-repeat requires --send-file")
    if args.send_repeat_gap < 0 or args.send_repeat_gap > 60:
        parser.error("--send-repeat-gap must be between 0 and 60 seconds")
    if args.send_repeat_reset != "none" and args.send_repeat < 2:
        parser.error("--send-repeat-reset requires --send-repeat of at least 2")
    if args.send_repeat_reset != "none" and not (args.shared_wifi and args.send_file):
        parser.error("--send-repeat-reset requires --shared-wifi and --send-file")
    if args.send_he_2_stream and not args.send_fast_profile:
        parser.error("--send-he-2-stream requires --send-fast-profile")
    if args.send_amsdu_3 and not args.send_fast_profile:
        parser.error("--send-amsdu-3 requires --send-fast-profile")
    if args.prepare_upload_early and not args.send_fast_profile:
        parser.error("--prepare-upload-early requires --send-fast-profile")
    if args.batch_radio_send and not args.send_fast_profile:
        parser.error("--batch-radio-send requires --send-fast-profile")
    if args.radio_send_batch_size is not None and not args.batch_radio_send:
        parser.error("--radio-send-batch-size requires --batch-radio-send")
    upload_batch_size = (args.radio_send_batch_size or DEFAULT_UPLOAD_BATCH_PACKETS) if args.batch_radio_send else 0
    if args.compare_send_batches and not (args.shared_wifi and args.send_file and args.trace_upload_flow
                                          and upload_batch_size == DEFAULT_UPLOAD_BATCH_PACKETS
                                          and args.send_repeat == 1):
        parser.error("--compare-send-batches requires one traced shared-Wi-Fi upload with the default batch size")
    mtu = APPLE_PROFILE_MTU if args.send_fast_profile else args.shared_mtu or MTU
    if args.winawdl_merge and (destination is None or args.coalesce_receive):
        parser.error("--winawdl-merge requires --receive-into and selects its own TCP merger")
    if args.coalesce_receive and destination is None:
        parser.error("--coalesce-receive requires --receive-into")
    if args.use_rx_reorder and destination is None:
        parser.error("--use-rx-reorder requires --receive-into")
    if args.advertise_he and not (args.shared_wifi and args.use_rx_reorder and destination is not None):
        parser.error("--advertise-he requires --shared-wifi, --use-rx-reorder and --receive-into")
    if args.rx_queue_256 and not (args.shared_wifi and destination is not None):
        parser.error("--rx-queue-256 requires --shared-wifi and --receive-into")
    if args.use_rx_amsdu and not (args.shared_wifi and args.use_rx_reorder and destination is not None):
        parser.error("--use-rx-amsdu requires --shared-wifi, --use-rx-reorder and --receive-into")
    if args.peer_wcid_ack and not (args.shared_wifi and args.use_rx_reorder and destination is not None):
        parser.error("--peer-wcid-ack requires --shared-wifi, --use-rx-reorder and --receive-into")
    if args.wintun_ring_kib is not None and not (args.shared_wifi and args.send_file):
        parser.error("Changing Wintun ring capacity requires a shared-Wi-Fi --send-file")
    if args.trace_upload_flow and destination is None and not (args.shared_wifi and args.send_file):
        parser.error("--trace-upload-flow requires --receive-into or a shared-Wi-Fi --send-file")
    if args.decode_during_receive and destination is None:
        parser.error("--decode-during-receive requires --receive-into")
    if args.memory_archive and not args.decode_during_receive:
        parser.error("--memory-archive requires --decode-during-receive")
    if args.fast_upload_tail and not (args.shared_wifi and args.decode_during_receive):
        parser.error("--fast-upload-tail requires --shared-wifi and --decode-during-receive")
    if args.receive_buffer_mib and destination is None:
        parser.error("--receive-buffer-mib requires --receive-into")
    if args.short_upload_poll and not args.batch_radio_receive:
        parser.error("--short-upload-poll requires --batch-radio-receive")
    if args.pipeline_receive and not (args.shared_wifi and args.batch_radio_receive and destination is not None):
        parser.error("--pipeline-receive requires --shared-wifi, --batch-radio-receive and --receive-into")
    if args.radio_batch_size is not None and not args.batch_radio_receive:
        parser.error("--radio-batch-size requires --batch-radio-receive")
    if not args.shared_wifi and args.radio_batch_size is not None and args.radio_batch_size > 16:
        parser.error("Receive batches larger than 16 packets require WiFiCx shared mode")
    if args.upload_ack_rate is not None and not args.batch_radio_receive:
        parser.error("A custom upload ACK rate requires --batch-radio-receive")
    if args.batch_radio_receive and destination is None:
        parser.error("--batch-radio-receive requires --receive-into")
    if args.advertise_vht and destination is None:
        parser.error("--advertise-vht requires --receive-into")
    if args.wake_on_receive_ack and destination is None:
        parser.error("--wake-on-receive-ack requires --receive-into")
    if args.receive_batch_size is not None and not args.coalesce_receive:
        parser.error("--receive-batch-size requires --coalesce-receive")
    receive_batch_size = (args.receive_batch_size or DEFAULT_BATCH_SEGMENTS) if args.coalesce_receive else None
    files = []
    if args.send_file is not None:
        try:
            files = sender.outgoing([str(args.send_file)])
        except sender.Unsendable as error:
            parser.error(str(error))
        if args.compare_send_batches and sum(file.size for file in files) < UPLOAD_BATCH_COMPARISON[-1][0]:
            parser.error("--compare-send-batches requires a file of at least 52 MiB")
        print(f"AirDrop file selected: {files[0].name!r} ({files[0].size} bytes)", flush=True)

    image = b"" if args.shared_wifi else args.image.read_bytes()
    if not args.shared_wifi and len(image) != 892880:
        parser.error("Expected the public MT7922 reference firmware")
    standard_send = (args.shared_wifi and args.send_file and args.send_fast_profile
                     and args.send_he_2_stream and args.prepare_upload_early and not args.send_amsdu_3
                     and upload_batch_size == DEFAULT_UPLOAD_BATCH_PACKETS
                     and args.send_repeat == 1 and not args.compare_send_batches)
    ring_capacity = WINTUN_RING_LARGE if standard_send else WINTUN_RING_DEFAULT
    if args.wintun_ring_kib is not None:
        ring_capacity = args.wintun_ring_kib * 1024
    with ExitStack() as cleanup:
        tunnel = Tunnel(ring_capacity)
        winawdl_merge = WinAwdlReceiveBatch(args.port) if args.winawdl_merge else None
        keys = cleanup.enter_context(tempfile.TemporaryDirectory(dir=args.image.resolve().parent))
        context = (awdl_receiver.receiver.tls_context(keys) if destination is not None
                   else sender.client_context(keys))
        quic_probe = None
        quic_port = None
        if args.quic_probe:
            sys.path.insert(0, str(ROOT / "build" / "python-quic"))
            try:
                from awdl_quic_probe import PORT as quic_port, QuicProbe
            except ImportError as error:
                raise RuntimeError(f"AirDrop QUIC dependency failed to load: {error}") from error
            certificate, key = awdl_receiver.receiver.identity.ensure_self_signed(keys)
            quic_probe = QuicProbe(certificate, key, mtu)
        if args.check:
            if quic_probe is not None:
                print("AirDrop QUIC preflight: aioquic, HTTP/3 and local TLS certificate ready", flush=True)
            if winawdl_merge is not None:
                print("WinAWDL native TCP merge preflight: DLL loaded", flush=True)
            if args.wake_on_receive_ack:
                UploadReplyWait(cleanup)
                print("AirDrop upload reply wake preflight: high-resolution timer ready", flush=True)
            if args.wake_peer:
                dry_run = subprocess.run(
                    [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned",
                     "-File", sender.BEACON_SCRIPT, "-DryRun"],
                    capture_output=True, text=True, errors="replace", timeout=15)
                message = dry_run.stdout.strip()
                if dry_run.returncode or not message.startswith("would advertise "):
                    raise RuntimeError(f"WinAWDL Bluetooth preflight failed: {message or dry_run.stderr.strip()}")
                print(f"WinAWDL Bluetooth preflight: {message}", flush=True)
            if destination is not None:
                awdl_receiver.firewall_command(POWERSHELL, "Get-Command New-NetFirewallRule,Remove-NetFirewallRule | Out-Null")
                print("AirDrop receive preflight: Wintun DLL, WinAWDL TLS server and firewall commands ready", flush=True)
            else:
                print("AirDrop /Discover preflight: Wintun DLL and WinAWDL TLS client ready", flush=True)
                if args.send_file:
                    print(f"AirDrop send preflight: selected Wintun rings {ring_capacity // 1024} KiB per direction",
                          flush=True)
            return
        driver = Driver(cleanup, shared_wifi=True) if args.shared_wifi else None
        if args.startup_log is not None:
            with args.startup_log.open("x", encoding="utf-8", newline="") as stream:
                csv.writer(stream).writerow(("time", "stage"))
        record_startup(args.startup_log, "Wintun starting")
        index = tunnel.open(cleanup)
        if args.send_file:
            print(f"Wintun session ring capacity: {tunnel.ring_capacity // 1024} KiB per direction", flush=True)
        upload_reply_wait = None
        if args.wake_on_receive_ack:
            outgoing_event = tunnel.read_event(tunnel.session)
            if not outgoing_event:
                raise RuntimeError("Wintun did not provide its outbound-packet event")
            upload_reply_wait = UploadReplyWait(cleanup, outgoing_event)
        configure_tunnel(index, mtu)
        record_startup(args.startup_log, "Wintun configured", observe=not args.no_startup_hold)
        if driver is None:
            driver = Driver(cleanup)
        if args.wake_peer:
            record_startup(args.startup_log, "Bluetooth starting")
            beacon = cleanup.enter_context(sender.Beacon())
            if not beacon.on:
                raise RuntimeError(f"WinAWDL Bluetooth advertisement did not start: {beacon.said}")
            print("AirDrop Bluetooth advertisement started", flush=True)
            record_startup(args.startup_log, "Bluetooth advertising", observe=not args.no_startup_hold)
        executor = cleanup.enter_context(ThreadPoolExecutor(max_workers=3 if quic_probe is not None else 2))
        radio = "shared with WLAN" if args.shared_wifi else "80 MHz"
        waiting = f"Waiting for AWDL peer {args.peer_mac} on channel 149 ({radio})"
        if destination is None or args.wake_peer:
            waiting += ", then checking AirDrop reachability for up to 30 seconds on the same link"
        else:
            waiting += ". Open the AirDrop share screen on this Apple device."
        print(waiting, flush=True)
        print(f"Configured AWDL transmit rate: {args.tx_rate} Mbps OFDM", flush=True)
        rate_byte = args.tx_rate | (SHARED_PEER_WCID_ACK if args.peer_wcid_ack else 0)
        if args.send_fast_profile:
            rate_byte |= SHARED_UPLOAD_FAST
        if args.send_he_2_stream:
            rate_byte |= SHARED_UPLOAD_HE_2_STREAM
        if args.prepare_upload_early:
            rate_byte |= SHARED_UPLOAD_EARLY
        if upload_batch_size:
            rate_byte |= SHARED_UPLOAD_BATCH
            if upload_batch_size > DEFAULT_UPLOAD_BATCH_PACKETS or args.compare_send_batches:
                rate_byte |= SHARED_UPLOAD_BATCH64
        target = peer_mac + struct.pack("!HB", args.port, rate_byte)
        if destination is not None:
            service_id_path = ROOT / "build" / "x64" / "mt7922-airdrop-service-id.txt"
            service_id = awdl_receiver.stable_service_id(service_id_path)
            mode_byte = RECEIVER_WARMUP_MODE if args.wake_peer else RECEIVER_MODE
            if args.shared_mtu is not None:
                mode_byte = RECEIVER_WINAWDL_MODE
                if mtu == APPLE_PROFILE_MTU:
                    mode_byte |= SHARED_RECEIVER_MTU1500
            if args.advertise_vht:
                mode_byte |= RECEIVER_VHT
            if args.short_upload_poll:
                mode_byte |= RECEIVER_SHORT_POLL
            if args.use_rx_reorder:
                mode_byte |= RECEIVER_REORDER
            if args.use_rx_amsdu:
                mode_byte |= RECEIVER_AMSDU
            if args.advertise_he:
                mode_byte |= RECEIVER_HE
            if args.rx_queue_256:
                mode_byte |= RECEIVER_RX256
            target += bytes((mode_byte,)) + service_id.encode("ascii")
        elif args.send_amsdu_3:
            target += bytes((3,))
        if desktop is not None:
            desktop.check()
        record_startup(args.startup_log, "AWDL link requested")
        session = executor.submit(driver.run_link, image + target, destination is not None, args.trace_upload_flow)
        cleanup.callback(driver.stop, session, receiving=destination is not None)
        try:
            local, peer = wait_for_peer(driver, session, desktop)
        except TimeoutError:
            if desktop is not None and destination is not None:
                desktop.emit("idle")
                return
            raise
        record_startup(args.startup_log, "AWDL peer ready")
        if not local.is_link_local or not peer.is_link_local:
            raise RuntimeError("Driver returned an address outside the IPv6 link-local range")
        if destination is None:
            print(f"AWDL link ready: [{local}%{index}] -> [{peer}%{index}]:{args.port}", flush=True)
        else:
            print(f"AWDL link ready: [{local}%{index}]:{args.port} <- [{peer}%{index}]", flush=True)
        tunnel.add_ipv6_address(local)
        print(f"Wintun IPv6 configured on interface {index} (MTU {mtu})", flush=True)
        record_startup(args.startup_log, "Wintun IPv6 address configured")
        if destination is not None:
            control_port = None
            if quic_probe is not None:
                awdl_receiver.allow_inbound(cleanup, POWERSHELL, tunnel.name, local, peer, quic_port, "UDP")
                executor.submit(quic_probe.run, local, index)
                cleanup.callback(quic_probe.stop)
                control_port = quic_probe.ready.result(timeout=10)
                awdl_receiver.allow_inbound(cleanup, POWERSHELL, tunnel.name, local, peer, control_port, "TCP")
                print("AirDrop QUIC mDNS service: _asquic._udp.local", flush=True)
            watcher, announcement = awdl_receiver.start_receiver(
                cleanup, local, peer, args.port, index, tunnel.name, destination, context, POWERSHELL,
                service_id, shared_wifi=args.shared_wifi, quic_port=quic_port, control_port=control_port,
                decode_during_receive=args.decode_during_receive, fast_upload_tail=args.fast_upload_tail,
                receive_buffer_bytes=args.receive_buffer_mib * (1024 ** 2),
                trace_upload_flow=args.trace_upload_flow, memory_archive=args.memory_archive, desktop=desktop)
            record_startup(args.startup_log, "Receiver ready")
            operation = executor.submit(awdl_receiver.wait_for_file, watcher, destination)
            relay(driver, session, tunnel, local, peer, args.port, operation, announcement, watcher,
                  receive_batch_size=receive_batch_size, upload_reply_wait=upload_reply_wait,
                  batch_radio_receive=args.batch_radio_receive, upload_ack_rate=args.upload_ack_rate,
                  radio_batch_size=args.radio_batch_size or DEFAULT_RADIO_BATCH_PACKETS,
                  winawdl_merge=winawdl_merge, trace_upload_flow=args.trace_upload_flow,
                  pipeline_receive=args.pipeline_receive, mtu=mtu, quic_probe=quic_probe, desktop=desktop)
        else:
            watch = SendWatch(trace_upload_flow=args.trace_upload_flow, desktop=desktop)
            if args.send_repeat > 1:
                operation = executor.submit(send_repeated, local, peer, args.port, index, context, files, watch,
                                            driver if args.prepare_upload_early else None,
                                            args.send_repeat, args.send_repeat_gap,
                                            driver, args.send_repeat_reset)
            else:
                operation = executor.submit(discover_and_send, local, peer, args.port, index, context, files, watch,
                                            prepare_driver=driver if args.prepare_upload_early else None)
            relay(driver, session, tunnel, local, peer, args.port, operation,
                  trace_upload_flow=args.trace_upload_flow, mtu=mtu, send_watch=watch,
                  upload_batch_size=upload_batch_size, compare_send_batches=args.compare_send_batches,
                  desktop=desktop)


if __name__ == "__main__":
    try:
        main()
    except Stopped:
        pass
    except Exception as failure:
        print(f"AirDrop operation failed: {type(failure).__name__}: {failure}", flush=True)
        sys.exit(1)
