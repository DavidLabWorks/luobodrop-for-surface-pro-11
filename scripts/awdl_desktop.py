"""Desktop transfer events and user decisions over the local process pipes."""

import json
import sys
from threading import Event, Lock, Thread
import time
from uuid import uuid4


class Stopped(Exception):
    pass


class DesktopSession:
    def __init__(self):
        self.stopping = Event()
        self.lock = Lock()
        self.offer = None
        self.answer = None
        self.answered = Event()
        Thread(target=self.read_commands, daemon=True).start()

    def emit(self, kind, **fields):
        line = json.dumps({"event": kind, **fields}, ensure_ascii=True, separators=(",", ":"))
        print("AIRDROP_UI " + line, flush=True)

    def read_commands(self):
        # Keep blocking reads off the buffered Python streams during interpreter exit.
        try:
            with open(sys.stdin.fileno(), "rb", buffering=0, closefd=False) as commands:
                for line in commands:
                    try:
                        command = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(command, dict):
                        continue
                    if command.get("command") == "stop":
                        break
                    if command.get("command") == "answer":
                        with self.lock:
                            if (self.offer is not None and command.get("id") == self.offer
                                    and type(command.get("accept")) is bool and not self.answered.is_set()):
                                self.answer = command["accept"]
                                self.answered.set()
        finally:
            self.stopping.set()

    def check(self):
        if self.stopping.is_set():
            raise Stopped()

    def accept(self, ask, left, receiver_stopping, timeout):
        files = ask["Files"]
        sizes = [file.get("FileSize") for file in files]
        total = sum(sizes) if all(type(size) is int and size >= 0 for size in sizes) else None
        token = uuid4().hex
        with self.lock:
            self.offer = token
            self.answer = None
            self.answered.clear()
        self.emit("offer", id=token, sender=str(ask.get("SenderComputerName") or "")[:150],
                  files=[str(file.get("FileName") or "")[:180] for file in files[:12]],
                  count=len(files), total=total)
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                if self.stopping.is_set() or receiver_stopping.is_set() or left():
                    break
                if self.answered.wait(0.1):
                    self.emit("answer", accepted=self.answer)
                    return self.answer
            self.emit("expired")
            return False
        finally:
            with self.lock:
                self.offer = None


class RateSamples:
    def __init__(self, started):
        self.last_at = started
        self.last_bytes = 0
        self.peak = None

    def update(self, done, now):
        elapsed = now - self.last_at
        if elapsed < 0.2:
            return None
        rate = max(done - self.last_bytes, 0) / elapsed / 1_000_000
        if rate > 0:
            self.peak = max(self.peak or 0, rate)
        self.last_at, self.last_bytes = now, done
        return rate


class Progress:
    def __init__(self, desktop, total, started, *, separate_body_samples=False):
        self.desktop = desktop
        self.total = total
        self.started = started
        self.live_samples = RateSamples(started)
        self.body_samples = RateSamples(started) if separate_body_samples else self.live_samples
        desktop.emit("transfer", total=total)

    def update(self, done, now):
        rate = self.live_samples.update(done, now)
        if rate is not None:
            self.desktop.emit("progress", done=done, total=self.total, rate=rate)

    def body_written(self, done):
        self.body_samples.update(done, time.monotonic())

    def finish(self, body_finished_at, files=()):
        seconds = time.monotonic() - self.started
        body_seconds = None
        average = None
        if body_finished_at is not None and body_finished_at > self.started:
            body_seconds = body_finished_at - self.started
            if self.total is not None:
                average = self.total / body_seconds / 1_000_000
        # The full body is one sample, including when it ends before the first 200 ms sample.
        peak = self.body_samples.peak
        if average is not None:
            peak = max(peak or 0, average)
        self.desktop.emit("complete", files=files, total=self.total, seconds=seconds,
                          body_seconds=body_seconds, rate=average, peak=peak)
