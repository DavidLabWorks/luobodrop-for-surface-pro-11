"""Reassemble explicitly labelled Qualcomm management dumps and report counts."""

import argparse
from collections import Counter
from pathlib import Path
import re
from uuid import UUID

from dissect.etl import ETL


LOGGER = UUID("bb6f5b93-635c-47be-816f-e895e77064a8")
PROVIDERS = {LOGGER, UUID(bytes=LOGGER.bytes_le)}
LABEL = re.compile(rb"_dump_mgmt_pkts:.*?\b(recv|send|candidate) mgmt subtype: "
                   rb"0x([0-9a-f]+), length: (\d+)\s*$", re.I)
ROW = re.compile(rb"OL:\s*([0-9a-f]+)\s+((?:[0-9a-f]{2}\s+)*[0-9a-f]{2})", re.I)


def read_frames(messages):
    pending = None
    data = bytearray()
    for message in messages:
        label = LABEL.search(message)
        if label:
            direction, subtype, length = label.groups()
            pending = direction.decode().lower(), int(subtype, 16), int(length)
            data.clear()
            continue
        if pending is None:
            continue
        row = ROW.fullmatch(message)
        if row is None or int(row[1], 16) != len(data):
            pending = None
            continue
        data.extend(bytes.fromhex(row[2].decode()))
        direction, subtype, length = pending
        if len(data) >= length:
            pending = None
            if len(data) == length and length >= 24 and data[0] == subtype and subtype & 15 == 0:
                yield direction, subtype, bytes(data)


def inspect(path):
    counts = Counter()
    with path.open("rb") as stream:
        messages = (bytes(r.header.payload).partition(b"\0")[0].strip() for r in ETL(stream)
                    if r.header.provider_id in PROVIDERS)
        for direction, subtype, frame in read_frames(messages):
            counts[direction, subtype, len(frame)] += 1
    print(f"{path.name}: complete dumps={sum(counts.values())}")
    for (direction, subtype, length), count in sorted(counts.items()):
        print(f"  {direction} subtype=0x{subtype:02x} bytes={length} count={count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("etl", type=Path, nargs="+")
    for trace in parser.parse_args().etl:
        inspect(trace)
