"""Prepare and sequence MT7922 Connac2 boot commands for a supplied MCU transport."""

from dataclasses import dataclass
from struct import Struct, pack

PCI_DMA_PAGE_BYTES = 4096
FW_FEATURE_NON_DL = 0x40

TARGET_ADDRESS_LEN = 0x01
FIRMWARE_START = 0x02
PATCH_START = 0x05
PATCH_FINISH = 0x07
PATCH_SEMAPHORE = 0x10
SEM_RELEASE = 0
SEM_GET = 1
PATCH_PRESENT = 1
SEM_ACQUIRED = 2
SEM_RELEASED = 3

DOWNLOAD_NEEDS_REPLY = 0x80000000
DOWNLOAD_ENCRYPT = 0x01
DOWNLOAD_RESET_IV = 0x08
DOWNLOAD_SCRAMBLE = 0x40
FIRMWARE_READY_MASK = 0x03
FIRMWARE_READY_MS = 1500

# Connac2 legacy descriptor. The first 32 bytes are the hardware TXD.
MCU_TXD = Struct("<8IHH8B20x")
MCU_RX_SEQUENCE = 29
MCU_RX_PATCH_STATUS = 32
TXD0_COMMAND_WM = 0x41000000
TXD1_LONG_COMMAND = 0x80010000
MCU_PACKET_ID = 0xA0


class FirmwareLoadError(RuntimeError):
    """The firmware loader could not complete the command sequence."""


@dataclass(frozen=True)
class DownloadRegion:
    target: int
    mode: int
    data: memoryview


def scatter_packets(data, offset, length, max_payload=PCI_DMA_PAGE_BYTES):
    """Yield one bounded view per PCIe FW_SCATTER descriptor."""
    if offset < 0 or length <= 0 or offset + length > len(data):
        raise ValueError("Firmware scatter region is outside the image")
    if not 0 < max_payload <= PCI_DMA_PAGE_BYTES:
        raise ValueError("Firmware packet size exceeds the PCIe DMA buffer")
    view = memoryview(data)
    end = offset + length
    for start in range(offset, end, max_payload):
        yield view[start:min(start + max_payload, end)]


def _patch_mode(security):
    encoding = security >> 24
    mode = DOWNLOAD_NEEDS_REPLY
    if security == 0xFFFFFFFF or encoding == 0:
        return mode
    if encoding == 1:
        return mode | DOWNLOAD_ENCRYPT | DOWNLOAD_RESET_IV | ((security & 3) << 1)
    if encoding == 2:
        return mode | DOWNLOAD_ENCRYPT | DOWNLOAD_RESET_IV | DOWNLOAD_SCRAMBLE
    raise ValueError(f"Unsupported ROM patch encryption type {encoding:#x}")


def _ram_mode(features):
    mode = DOWNLOAD_NEEDS_REPLY | (features & 0x06)
    if features & 0x01:
        mode |= DOWNLOAD_ENCRYPT | DOWNLOAD_RESET_IV
    if features & 0x10:
        mode |= DOWNLOAD_SCRAMBLE
    return mode


def _region(data, metadata, mode):
    offset = metadata["offset"]
    length = metadata["length"]
    target = int(metadata["target"], 16)
    if (offset < 0 or length <= 0 or offset + length > len(data)
            or target < 0 or target + length > 1 << 32):
        raise ValueError("Invalid firmware region address or length")
    return DownloadRegion(target, mode, memoryview(data)[offset:offset + length])


def _encode_command(command, sequence, payload):
    total_bytes = MCU_TXD.size + len(payload)
    if not 1 <= sequence <= 15 or total_bytes > PCI_DMA_PAGE_BYTES:
        raise ValueError("Invalid MCU command sequence or length")
    return MCU_TXD.pack(
        TXD0_COMMAND_WM | total_bytes, TXD1_LONG_COMMAND, 0, 0, 0, 0, 0, 0,
        total_bytes - 32, 0x8000, command, MCU_PACKET_ID, 3, sequence, 0, 0, 0, 0,
    ) + payload


class _Loader:
    def __init__(self, transport):
        self._transport = transport
        self._sequence = 0

    def command(self, command, payload):
        self._sequence = self._sequence % 15 + 1
        reply = self._transport.exchange(_encode_command(command, self._sequence, payload))
        if len(reply) <= MCU_RX_SEQUENCE or reply[MCU_RX_SEQUENCE] != self._sequence:
            raise FirmwareLoadError(f"Missing or mismatched reply for MCU command {command:#04x}")
        if command in (PATCH_SEMAPHORE, PATCH_FINISH):
            if len(reply) <= MCU_RX_PATCH_STATUS:
                raise FirmwareLoadError(f"Truncated patch reply for MCU command {command:#04x}")
            return reply[MCU_RX_PATCH_STATUS]
        return 0

    def download(self, region):
        command = PATCH_START if region.target in (0x00200000, 0x00900000) else TARGET_ADDRESS_LEN
        self.command(command, pack("<III", region.target, len(region.data), region.mode))
        for packet in scatter_packets(region.data, 0, len(region.data)):
            self._transport.send_scatter(packet)

    def release_patch(self):
        reply = self.command(PATCH_SEMAPHORE, pack("<I", SEM_RELEASE))
        if reply != SEM_RELEASED:
            raise FirmwareLoadError(f"ROM did not release the patch semaphore ({reply:#x})")

    def load_patch(self, regions):
        reply = self.command(PATCH_SEMAPHORE, pack("<I", SEM_GET))
        if reply == PATCH_PRESENT:
            return
        if reply != SEM_ACQUIRED:
            raise FirmwareLoadError(f"ROM did not grant the patch semaphore ({reply:#x})")
        try:
            for region in regions:
                self.download(region)
            finish = self.command(PATCH_FINISH, bytes(4))
            if finish != 0:
                raise FirmwareLoadError(f"ROM rejected the patch finish request ({finish:#x})")
        except Exception as error:
            try:
                self.release_patch()
            except Exception as release_error:
                error.add_note(f"Patch semaphore cleanup failed: {release_error}")
            raise
        self.release_patch()


def _download_regions(rom, rom_info, ram, ram_info):
    patch = [
        _region(rom, metadata, _patch_mode(int(metadata["security"], 16)))
        for metadata in rom_info["regions"]
    ]
    ram_regions = []
    entry = 0
    for metadata in ram_info["regions"]:
        features = int(metadata["features"], 16)
        if features & FW_FEATURE_NON_DL:
            continue
        region = _region(ram, metadata, _ram_mode(features))
        if features & 0x20:
            entry = region.target
        ram_regions.append(region)
    if not patch or not ram_regions:
        raise ValueError("ROM patch or RAM firmware contains no downloadable regions")
    return patch, ram_regions, entry


def pack_driver_image(rom, rom_info, ram, ram_info):
    """Return the validated ROM and WM region bytes in their DMA download order."""
    patch, ram_regions, _ = _download_regions(rom, rom_info, ram, ram_info)
    return b"".join(region.data for region in patch + ram_regions)


def boot_firmware(transport, rom, rom_info, ram, ram_info):
    """Run the boot sequence over a transport already in ROM download mode.

    exchange() sends one frame and returns its matched MCU response.
    send_scatter() consumes a payload before returning.
    wait_for_firmware() polls CONN_ON_MISC and returns whether the masked bits match.
    """
    patch, ram_regions, entry = _download_regions(rom, rom_info, ram, ram_info)

    loader = _Loader(transport)
    loader.load_patch(patch)
    for region in ram_regions:
        loader.download(region)
    loader.command(FIRMWARE_START, pack("<II", int(entry != 0), entry))
    if not transport.wait_for_firmware(FIRMWARE_READY_MASK, FIRMWARE_READY_MASK, FIRMWARE_READY_MS):
        raise FirmwareLoadError("Firmware start was acknowledged, but the ready bits did not appear")
