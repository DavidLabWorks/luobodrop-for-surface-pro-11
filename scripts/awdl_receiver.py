"""Receive one approved AirDrop transfer from the selected AWDL peer."""

import argparse
from concurrent.futures import Future
import csv
from functools import partial
import ipaddress
import msvcrt
from awdl_desktop import Progress as DesktopProgress
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import uuid

import mdns
import receiver
import transfer

GROUP = ipaddress.IPv6Address(mdns.GROUP)
PORT = receiver.DEFAULT_PORT
ANNOUNCE_SECONDS = 0.75
WAIT_FOR_ASK_SECONDS = 120
# Allow the metadata reader's progress budget and a final socket idle period.
WAIT_FOR_ASK_BODY_SECONDS = (transfer.INITIAL_READ_SECONDS + transfer.READ_IDLE_TIMEOUT_SECONDS
                             + transfer.MAX_METADATA_BYTES // transfer.MIN_READ_BYTES_PER_SECOND)
WAIT_FOR_APPROVAL_SECONDS = 45
WAIT_FOR_UPLOAD_SECONDS = 85
UPLOAD_LIMIT_SECONDS = 15 * 60
UPLOAD_REPORT_SECONDS = 5
UPLOAD_CLOSE_DRAIN_SECONDS = 10
UPLOAD_TLS_SHUTDOWN_SECONDS = 3
SHARED_UPLOAD_END_MARKER_WAIT_SECONDS = 0.5
FAST_UPLOAD_END_MARKER_WAIT_SECONDS = 0.1
UPLOAD_ARCHIVE_MEMORY_BYTES = 64 * transfer.MIB
MIN_AWDL_BYTES_PER_SECOND = 4 * transfer.KIB


def stable_service_id(path):
    """Keep the Bonjour instance and MIF service label stable across runs."""
    if not path.exists():
        with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", dir=path.parent,
                                         prefix=path.stem + "-", delete=False) as pending:
            temporary = Path(pending.name)
            pending.write(secrets.token_hex(6) + "\n")
        try:
            # Windows rename does not replace another process's established ID.
            temporary.rename(path)
        except FileExistsError:
            pass
        finally:
            temporary.unlink(missing_ok=True)
    label = path.read_text(encoding="ascii").strip()
    if len(label) != 12 or any(ch not in "0123456789abcdef" for ch in label):
        raise ValueError(f"Invalid AirDrop service ID in {path}")
    return label


def multicast_datagram(source, message):
    length = 8 + len(message)
    udp = struct.pack("!HHHH", mdns.PORT, mdns.PORT, length, 0) + message
    checksum_input = source.packed + GROUP.packed + struct.pack("!I3xB", length, socket.IPPROTO_UDP) + udp
    if len(checksum_input) % 2:
        checksum_input += b"\0"
    total = sum(word for (word,) in struct.iter_unpack("!H", checksum_input))
    while total > 0xffff:
        total = (total & 0xffff) + (total >> 16)
    checksum = (total ^ 0xffff) or 0xffff
    return (struct.pack("!IHBB", 0x60000000, length, socket.IPPROTO_UDP, 255)
            + source.packed + GROUP.packed + udp[:6] + struct.pack("!H", checksum) + udp[8:])


class Announcement:
    def __init__(self, local, port, label, quic_port=None, control_port=None):
        host = label + ".local"
        tcp = mdns.Service(receiver.SERVICE_TYPE, label, host, str(local), port,
                           f"flags={receiver.DEFAULT_FLAGS}")
        services = [tcp]
        if quic_port is not None:
            services.append(mdns.Service("_asquic._udp.local", str(uuid.uuid4()).upper(), host,
                                         str(local), quic_port, ""))
        if control_port is not None:
            services.append(mdns.Service("_companion-link._tcp.local", f"CLink-{label}", host,
                                         str(local), control_port, ""))
        self.names = set().union(*(service.names() for service in services))
        replies = [service.response(ttl=30) for service in services]
        # These responses contain uncompressed answer records and no other sections.
        answers = sum(struct.unpack_from("!H", reply, 6)[0] for reply in replies)
        header = replies[0][:6] + struct.pack("!H", answers) + replies[0][8:mdns.HEADER_BYTES]
        message = header + b"".join(reply[mdns.HEADER_BYTES:] for reply in replies)
        self.packet = multicast_datagram(local, message)
        self.local = local
        self.next_send = 0.0
        self.queries = 0
        self.first_query_at = None
        self.first_queued_at = None

    def saw_query(self, packet, peer):
        if (len(packet) < 60 or packet[6] != socket.IPPROTO_UDP
                or packet[8:24] != peer.packed
                or packet[24:40] not in (GROUP.packed, self.local.packed)
                or int.from_bytes(packet[42:44], "big") != mdns.PORT):
            return False
        length = int.from_bytes(packet[44:46], "big")
        if length != len(packet) - 40:
            return False
        if not (mdns.question_names(packet[48:]) & self.names):
            return False
        now = time.monotonic()
        if self.first_query_at is None:
            self.first_query_at = now
        self.queries += 1
        self.next_send = min(self.next_send, now + 0.1)
        return True

    def due(self):
        return time.monotonic() >= self.next_send

    def submitted(self):
        now = time.monotonic()
        if self.first_queued_at is None:
            self.first_queued_at = now
        self.next_send = now + ANNOUNCE_SECONDS


def console_accept(ask, left, stopping):
    names = ", ".join(repr(str(file.get("FileName", ""))[:150]) for file in ask["Files"])
    print(f"AirDrop request from {ask.get('SenderComputerName')!r}: {names}", flush=True)
    print("Press Y to receive these files or N to decline.", flush=True)
    deadline = time.monotonic() + WAIT_FOR_APPROVAL_SECONDS
    while time.monotonic() < deadline and not stopping.is_set() and not left():
        if msvcrt.kbhit():
            answer = msvcrt.getwch().lower()
            if answer in ("y", "n"):
                return answer == "y"
        stopping.wait(0.1)
    return False


class UploadStageTrace:
    """Keep slow operations in the same clock as the Wintun ACK trace."""

    MIN_EVENT_NS = 1_000_000
    MAX_EVENTS = 4096

    def __init__(self, peer_port):
        self.peer_port = peer_port
        self.rows = []
        self.omitted = 0
        self.totals = {}

    def measure(self, stage, operation, *args):
        start = time.monotonic_ns()
        try:
            return operation(*args)
        finally:
            end = time.monotonic_ns()
            duration = end - start
            totals = self.totals.setdefault(stage, [0, 0, 0])
            totals[0] += 1
            totals[1] += duration
            totals[2] = max(totals[2], duration)
            if duration >= self.MIN_EVENT_NS:
                if len(self.rows) < self.MAX_EVENTS:
                    self.rows.append((self.peer_port, stage, start, end, duration))
                else:
                    self.omitted += 1

    def reader(self, stream):
        return SimpleNamespace(
            read=partial(self.measure, "tls-read", stream.read),
            read1=partial(self.measure, "tls-read", getattr(stream, "read1", stream.read)),
            readline=partial(self.measure, "tls-read", stream.readline),
        )

    def writer(self, stream):
        return SimpleNamespace(write=partial(self.measure, "temp-write", stream.write))

    def write_csv(self, file):
        output = csv.writer(file)
        output.writerow(("peer_port", "stage", "started_ns", "ended_ns", "duration_ns"))
        output.writerows(sorted(self.rows, key=lambda row: row[2]))

    def summaries(self):
        for stage, (count, total, longest) in self.totals.copy().items():
            yield f"{stage}: calls={count} total={total / 1e6:.1f}ms max={longest / 1e6:.1f}ms"


class ReceiveWatch(receiver.Watcher):
    def __init__(self, accept=console_accept, desktop=None):
        self.accept = accept
        self.desktop = desktop
        self.progress = None
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.result = Future()
        self.token = object()
        self.approved = False
        self.ask_started = threading.Event()
        self.ask_ready = threading.Event()
        self.approval_ready = threading.Event()
        self.uploading = threading.Event()
        self.upload_started_at = 0.0
        self.next_report = 0.0
        self.last_progress = None
        self.stored_at = None
        self.upload_tail_timing = None
        self.upload_stage_trace = None
        self.last_progress_bytes = 0
        self.progress_pauses = 0
        self.longest_pause = 0.0
        self.total = None
        self.names = ()
        self.upload_data_port = None
        self.upload_reply_port = None
        self.ready_at = None
        self.first_syn_at = None
        self.first_discover_at = None

    def discovery_timing(self, announcement):
        def elapsed(timestamp):
            return "unseen" if timestamp is None else f"{timestamp - self.ready_at:.3f}s"

        return (f"first-mDNS-queued={elapsed(announcement.first_queued_at)} "
                f"first-mDNS-query={elapsed(announcement.first_query_at)} "
                f"first-TCP-SYN={elapsed(self.first_syn_at)} "
                f"first-Discover={elapsed(self.first_discover_at)}")

    def asked(self, ask, address, left):
        files = ask.get("Files")
        kinds = ask.get("TransferType") or {}
        if (not isinstance(files, list) or not files or any(not isinstance(file, dict) for file in files)
                or "links" in kinds or not self.lock.acquire(blocking=False)):
            return None
        try:
            if self.approved or self.stopping.is_set():
                return None
            self.ask_ready.set()
            approved = (self.desktop.accept(ask, left, self.stopping, WAIT_FOR_APPROVAL_SECONDS)
                        if self.desktop is not None else self.accept(ask, left, self.stopping))
            if not approved:
                return None
            self.approved = True
            self.approval_ready.set()
            print("AirDrop /Ask accepted on Windows", flush=True)
            return self.token
        finally:
            self.lock.release()

    def upload_started(self, accepted, tid, total):
        with self.lock:
            if accepted is not self.token or self.uploading.is_set() or self.stopping.is_set():
                return None
            self.total = total
            self.upload_started_at = time.monotonic()
            size = f"{total:,} file bytes" if total is not None else "size unknown"
            print(f"AirDrop upload started ({size})", flush=True)
            if self.desktop is not None:
                self.progress = DesktopProgress(self.desktop, total, self.upload_started_at)
            self.uploading.set()
            return self.token

    def upload_progress(self, token, done):
        if done <= self.last_progress_bytes:
            return
        self.last_progress_bytes = done
        now = time.monotonic()
        if self.progress is not None:
            self.progress.update(done, now)
        if self.last_progress is not None:
            pause = now - self.last_progress
            self.longest_pause = max(self.longest_pause, pause)
            self.progress_pauses += pause >= 0.5
        self.last_progress = now
        if done > 0 and now >= self.next_report:
            self.next_report = now + UPLOAD_REPORT_SECONDS
            total = f" / {self.total:,}" if self.total is not None else ""
            rate = done / transfer.KIB / max(now - self.upload_started_at, 0.1)
            print(f"AirDrop upload progress: {done:,}{total} bytes ({rate:.1f} KiB/s average)", flush=True)

    def stored(self, token, names):
        self.names = tuple(names)
        self.stored_at = time.monotonic()

    def upload_confirmed(self, token):
        with self.lock:
            if self.result.done():
                return
            if self.names:
                confirmed_at = time.monotonic()
                duration = confirmed_at - self.upload_started_at
                print(f"AirDrop upload timing: {duration:.2f}s, longest progress gap "
                      f"{self.longest_pause:.2f}s, gaps >= 0.5s: {self.progress_pauses}", flush=True)
                if self.last_progress is not None and self.stored_at is not None:
                    print(f"AirDrop upload finish: last-progress-at={self.last_progress - self.upload_started_at:.2f}s "
                          f"last-progress-to-stored={self.stored_at - self.last_progress:.2f}s "
                          f"stored-to-HTTP-200={confirmed_at - self.stored_at:.3f}s", flush=True)
                if self.upload_tail_timing is not None:
                    log_time, close_time, response_time = self.upload_tail_timing
                    print(f"AirDrop upload finish detail: tail-log={log_time:.3f}s "
                          f"archive-return-to-response={close_time:.3f}s "
                          f"response-headers={response_time:.3f}s", flush=True)
                if self.progress is not None:
                    self.progress.finish(self.last_progress, self.names)
                self.result.set_result(self.names)
            else:
                self.result.set_exception(RuntimeError("AirDrop upload contained no files"))

    def upload_failed(self, token, reason):
        with self.lock:
            if not self.result.done():
                self.result.set_exception(RuntimeError(reason))

    def stop(self):
        self.stopping.set()
        with self.lock:
            self.result.cancel()
            for event in (self.ask_started, self.ask_ready, self.approval_ready, self.uploading):
                event.set()


class MemoryUploadArchive(tempfile.SpooledTemporaryFile):
    rolled_to_disk = False

    def rollover(self):
        super().rollover()
        self.rolled_to_disk = True


class ReceiveHandler(receiver.Handler):
    upload_reply_started = False
    upload_reply_sent = False
    upload_archive_timing = None
    upload_response_started_at = None

    def setup(self):
        try:
            super().setup()
        except receiver.HandshakeFailed as error:
            if self.server.trace_handshake:
                cause = type(error.__cause__).__name__
                print(f"AirDrop TLS peer-port={self.client_address[1]}: handshake failed ({cause})", flush=True)
            raise
        if self.server.trace_handshake:
            print(f"AirDrop TLS peer-port={self.client_address[1]}: {self.request.version()}", flush=True)

    def do_POST(self):
        watcher = self.server.watcher
        if self.path == "/Ask":
            watcher.ask_started.set()
        elif self.path == "/Discover" and watcher.first_discover_at is None:
            watcher.first_discover_at = time.monotonic()
        if self.server.trace_handshake and self.path in ("/Discover", "/Ask", "/Upload"):
            print(f"AirDrop HTTP peer-port={self.client_address[1]}: {self.path} received", flush=True)
        super().do_POST()

    def send_response(self, code, message=None):
        self.upload_reply_started = self.path == "/Upload" and code == 200
        if self.upload_reply_started:
            self.upload_response_started_at = time.monotonic()
            self.server.watcher.upload_reply_port = self.client_address[1]
        super().send_response(code, message)
        if code == 200 and self.command == "POST" and self.path == "/Discover" and self.server.quic_port:
            advertisement = f'h3=":{self.server.quic_port}"; ma=30'
            self.send_header("Alt-Svc", advertisement)
            print(f"AirDrop /Discover Alt-Svc: {advertisement}", flush=True)

    def end_headers(self):
        super().end_headers()
        self.upload_reply_sent = self.upload_reply_started
        if self.upload_reply_sent and self.upload_archive_timing is not None:
            finished, returned = self.upload_archive_timing
            started = self.upload_response_started_at
            self.server.watcher.upload_tail_timing = (
                returned - finished, started - returned, time.monotonic() - started)

    def finish(self):
        super().finish()
        if self.upload_reply_sent:
            self.request.settimeout(UPLOAD_TLS_SHUTDOWN_SECONDS)
            try:
                # Send TLS close_notify before the server shuts down the TCP socket.
                self.request.unwrap()
            except OSError as error:
                print(f"AirDrop upload TLS close: peer reply unavailable ({type(error).__name__})", flush=True)
            else:
                print("AirDrop upload TLS close: peer close_notify received", flush=True)

    def receive_archive(self, raw, budget, plan, deadline, tid, on_progress):
        watcher = self.server.watcher
        watcher.upload_data_port = self.client_address[1]
        if self.server.trace_upload_flow:
            watcher.upload_stage_trace = UploadStageTrace(self.client_address[1])
        files = super().receive_archive(raw, budget, plan, deadline, tid, on_progress,
                                        decode_during_receive=self.server.decode_during_receive,
                                        timing=watcher.upload_stage_trace)
        finished = time.monotonic()
        if self.server.trace_handshake and watcher.last_progress is not None:
            framing = plan[0]
            end = "timeout" if self.upload_end_marker_timed_out else "received"
            print(f"AirDrop upload tail: framing={framing} body-end={end} "
                  f"last-data-to-body-end={self.upload_body_finished_at - watcher.last_progress:.3f}s "
                  f"extract-and-store={finished - self.upload_body_finished_at:.3f}s", flush=True)
            self.upload_archive_timing = finished, time.monotonic()
        if self.server.receive_buffer_bytes:
            size = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            print(f"AirDrop upload socket receive buffer: {size:,} bytes", flush=True)
        if isinstance(raw, MemoryUploadArchive):
            storage = "disk after rollover" if raw.rolled_to_disk else "memory"
            print(f"AirDrop temporary archive: {storage} (64 MiB memory threshold)", flush=True)
        return files

    def open_upload_archive(self, dest):
        if self.server.memory_archive:
            return MemoryUploadArchive(max_size=UPLOAD_ARCHIVE_MEMORY_BYTES, dir=dest)
        return super().open_upload_archive(dest)

    def upload_deadline(self):
        return transfer.ReadDeadline(min_bytes_per_second=MIN_AWDL_BYTES_PER_SECOND,
                                     end_marker_wait_seconds=self.server.settings.end_marker_wait_seconds)

    def log_request(self, code="-", size="-"):
        if self.path in ("/Discover", "/Ask", "/Upload"):
            print(f"AirDrop receiver {self.path}: HTTP {code}", flush=True)


class ScopedReceiver(receiver.Receiver):
    def __init__(self, local, peer, port, index, destination, name, watcher, *,
                 shared_wifi=False, quic_port=None, decode_during_receive=False, fast_upload_tail=False,
                 receive_buffer_bytes=0, trace_upload_flow=False, memory_archive=False):
        self.peer = peer
        self.trace_handshake = shared_wifi
        self.trace_upload_flow = trace_upload_flow
        self.quic_port = quic_port
        self.decode_during_receive = decode_during_receive
        self.receive_buffer_bytes = receive_buffer_bytes
        self.memory_archive = memory_archive
        end_marker_wait_seconds = SHARED_UPLOAD_END_MARKER_WAIT_SECONDS if shared_wifi else None
        if fast_upload_tail:
            end_marker_wait_seconds = FAST_UPLOAD_END_MARKER_WAIT_SECONDS
        settings = argparse.Namespace(name=name, model=receiver.DEFAULT_MODEL, dest=str(destination),
                                      end_marker_wait_seconds=end_marker_wait_seconds,
                                      max_receive_percent=receiver.DEFAULT_MAX_RECEIVE_PERCENT,
                                      interface_index=index, redact=False)
        super().__init__(port, settings, watcher, bind_address=str(local))
        self.RequestHandlerClass = ReceiveHandler

    def server_bind(self):
        if self.receive_buffer_bytes:
            # Set the option before TCP negotiates the receive window with the sender.
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.receive_buffer_bytes)
        super().server_bind()

    def verify_request(self, request, client_address):
        return (super().verify_request(request, client_address)
                and ipaddress.IPv6Address(client_address[0].split("%")[0]) == self.peer)


def firewall_command(powershell, command):
    subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command",
                    "$ErrorActionPreference='Stop'; " + command], check=True, timeout=20)


def allow_inbound(cleanup, powershell, alias, local, peer, port, protocol):
    rule = "MT7922-AirDrop-" + secrets.token_hex(8)
    # Tunnel generates the interface alias, and the addresses are IPv6 objects.
    firewall_command(powershell, f"New-NetFirewallRule -Name '{rule}' -DisplayName '{rule}' "
                     f"-InterfaceAlias '{alias}' -Direction Inbound -Action Allow -Protocol {protocol} "
                     f"-LocalPort {port} -LocalAddress '{local}' -RemoteAddress '{peer}' | Out-Null")
    cleanup.callback(firewall_command, powershell, f"Remove-NetFirewallRule -Name '{rule}'")


def start_receiver(cleanup, local, peer, port, index, alias, destination, tls, powershell, service_id,
                   *, shared_wifi=False, quic_port=None, control_port=None, decode_during_receive=False,
                   fast_upload_tail=False, receive_buffer_bytes=0, trace_upload_flow=False,
                   memory_archive=False, desktop=None):
    destination.mkdir(parents=True, exist_ok=True)
    watcher = ReceiveWatch(desktop=desktop)
    name = socket.gethostname().split(".", 1)[0]
    server = ScopedReceiver(local, peer, port, index, destination, name, watcher,
                            shared_wifi=shared_wifi, quic_port=quic_port,
                            decode_during_receive=decode_during_receive, fast_upload_tail=fast_upload_tail,
                            receive_buffer_bytes=receive_buffer_bytes, trace_upload_flow=trace_upload_flow,
                            memory_archive=memory_archive)
    cleanup.callback(server.server_close)

    server.socket = tls.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)

    allow_inbound(cleanup, powershell, alias, local, peer, port, "TCP")
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    watcher.ready_at = time.monotonic()
    thread.start()
    cleanup.callback(server.reset_sessions)
    cleanup.callback(thread.join)
    cleanup.callback(server.shutdown)
    cleanup.callback(watcher.stop)
    print(f"AirDrop receiver ready as {name!r}, saving into {destination}", flush=True)
    if desktop is not None:
        desktop.emit("ready")
    return watcher, Announcement(local, port, service_id, quic_port, control_port)


def wait_for_file(watcher, destination):
    phases = (
        (watcher.ask_started, WAIT_FOR_ASK_SECONDS, "No AirDrop /Ask arrived"),
        (watcher.ask_ready, WAIT_FOR_ASK_BODY_SECONDS, "No valid AirDrop file request became ready"),
        # Allow the console prompt and approval callback to finish before the relay exits.
        (watcher.approval_ready, WAIT_FOR_APPROVAL_SECONDS + 2, "No AirDrop request was approved"),
        (watcher.uploading, WAIT_FOR_UPLOAD_SECONDS, "No AirDrop upload began after approval"),
    )
    for event, timeout, message in phases:
        if not event.wait(timeout):
            if watcher.desktop is not None and event is not watcher.uploading:
                watcher.desktop.emit("idle")
                return
            raise TimeoutError(f"{message} within {timeout} seconds")
        if watcher.stopping.is_set():
            return
    try:
        names = watcher.result.result(timeout=UPLOAD_LIMIT_SECONDS)
    except TimeoutError as error:
        raise TimeoutError(f"AirDrop upload did not finish within {UPLOAD_LIMIT_SECONDS} seconds of starting") from error
    for name in names:
        print(f"AirDrop file received: {str(Path(destination) / name)!r}", flush=True)
    # Leave the AWDL relay up for the final TCP response and orderly close.
    watcher.stopping.wait(UPLOAD_CLOSE_DRAIN_SECONDS)
