"""Replay the MT7922 boot protocol against the local factory firmware backup."""

from collections import deque
from pathlib import Path
from runpy import run_path
from struct import pack, unpack_from
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from firmware_boot import FirmwareLoadError, boot_firmware

BACKUP = ROOT / "backup" / "mediatek-3.5.2.1376"
parse = run_path(str(ROOT / "scripts" / "inspect-firmware.py"))
rom = parse["read_firmware"](BACKUP, parse["ROM_NAME"])
ram = parse["read_firmware"](BACKUP, parse["RAM_NAME"])
rom_info = parse["inspect_rom"](rom)
ram_info = parse["inspect_ram"](ram)

GET = pack("<I", 1)
RELEASE = pack("<I", 0)
PATCH_TARGET = pack("<III", 0x00900000, 137472, 0x80000009)
FACTORY_RAM_LAYOUT = [
    (0x0091B000, 341904), (0x02015C00, 290768), (0x00404400, 15312),
    (0x0210A000, 8144), (0xE0200000, 96080),
]


def command(identifier, payload, reply=0, sequence_adjustment=0):
    return ("command", identifier, payload, reply, sequence_adjustment)


def chunks(data, offset, length):
    end = offset + length
    return [("scatter", data[start:min(start + 4096, end)])
            for start in range(offset, end, 4096)]


def patch_steps():
    return [
        command(0x10, GET, reply=2),
        command(0x05, PATCH_TARGET),
        *chunks(rom, 160, 137472),
        command(0x07, bytes(4)),
        command(0x10, RELEASE, reply=3),
    ]


def ram_steps(ready=True):
    steps = []
    offset = 0
    for target, length in FACTORY_RAM_LAYOUT:
        steps.append(command(0x01, pack("<III", target, length, 0x80000009)))
        steps.extend(chunks(ram, offset, length))
        offset += length
    steps.extend([command(0x02, pack("<II", 1, 0x0091B000)), ("ready", ready)])
    return steps


class Replay:
    def __init__(self, expected):
        self.expected = deque(expected)
        self.commands = 0
        self.packets = 0
        self.payload_bytes = 0

    def exchange(self, frame):
        operation, identifier, payload, result, sequence_adjustment = self.expected.popleft()
        assert operation == "command"
        sequence = self.commands % 15 + 1
        assert unpack_from("<II", frame) == (0x41000000 | (64 + len(payload)), 0x80010000)
        assert frame[8:32] == bytes(24)
        assert unpack_from("<HH4B", frame, 32) == (
            32 + len(payload), 0x8000, identifier, 0xA0, 3, sequence
        )
        assert frame[40:64] == bytes(24) and frame[64:] == payload
        self.commands += 1
        reply = bytearray(36)
        reply[29] = (sequence + sequence_adjustment) & 0xFF
        reply[32] = result
        return reply

    def send_scatter(self, packet):
        operation, expected = self.expected.popleft()
        assert packet == expected
        if operation == "timeout":
            raise TimeoutError("DMA scatter completion timed out")
        assert operation == "scatter"
        self.packets += 1
        self.payload_bytes += len(packet)

    def wait_for_firmware(self, mask, value, timeout_ms):
        operation, state = self.expected.popleft()
        assert operation == "ready"
        assert (mask, value, timeout_ms) == (3, 3, 1500)
        return state


def exercise(steps, error_type=None, error_text=None, ram_metadata=ram_info):
    replay = Replay(steps)
    try:
        boot_firmware(replay, rom, rom_info, ram, ram_metadata)
    except Exception as error:
        if error_type is None or not isinstance(error, error_type):
            raise
        assert error_text in str(error)
        assert not replay.expected
        return error
    assert error_type is None and not replay.expected
    return replay


def main():
    complete = exercise(patch_steps() + ram_steps())
    assert (complete.commands, complete.packets, complete.payload_bytes) == (10, 219, 889680)

    installed = exercise([command(0x10, GET, reply=1)] + ram_steps())
    assert (installed.commands, installed.packets, installed.payload_bytes) == (7, 185, 752208)

    exercise([command(0x10, GET)], FirmwareLoadError, "did not grant")
    exercise([command(0x10, GET, reply=2, sequence_adjustment=1)],
             FirmwareLoadError, "mismatched reply")

    exercise([
        command(0x10, GET, reply=2), command(0x05, PATCH_TARGET),
        ("timeout", rom[160:4256]), command(0x10, RELEASE, reply=3),
    ], TimeoutError, "scatter completion timed out")

    error = exercise([
        *patch_steps()[:-2], command(0x07, bytes(4), reply=9),
        command(0x10, RELEASE, reply=0),
    ], FirmwareLoadError, "rejected the patch finish")
    assert "cleanup failed" in error.__notes__[0]

    exercise(patch_steps() + ram_steps(ready=False),
             FirmwareLoadError, "ready bits did not appear")

    invalid = {"regions": [dict(ram_info["regions"][0], offset=len(ram))]}
    exercise([], ValueError, "Invalid firmware region", ram_metadata=invalid)

    print("Eight firmware boot scenarios passed.")
    print(f"Factory-image replay: {complete.commands} commands, "
          f"{complete.packets} data packets, {complete.payload_bytes} bytes.")


if __name__ == "__main__":
    main()
