"""Look for contiguous native action frames identified by Qualcomm RX log text."""

import argparse
from collections import Counter
from datetime import timedelta
from pathlib import Path
import re

from dissect.etl import ETL


RX = re.compile(rb"ind category (\d+) action (\d+) DA\[([0-9a-f:]{17})\]: "
                rb"Freq\[(\d+)\],Action Frame seq\[0x([0-9a-f]+)\]", re.I)
STATUS = re.compile(rb"WdiSendIndication:.*?StatusCode:ACTION_FRAME_RECEIVED,.*?StatusBufferSize\((\d+)\)")


def frame_pattern(address, sequence, category, action):
    # Match either ordinary management header or Order bit plus four HT-control bytes.
    suffix = re.escape(bytes((category, action)))
    prefix = b".{2}" + re.escape(address) + b".{12}" + re.escape(sequence.to_bytes(2, "little"))
    return re.compile(b"\xd0(?:[\x00-\x7f]" + prefix + suffix
                      + b"|[\x80-\xff]" + prefix + b".{4}" + suffix + b")", re.DOTALL)


def inspect(path):
    classifications = []
    statuses = []
    with path.open("rb") as stream:
        for record in ETL(stream):
            h = record.header
            data = bytes(h.payload)
            match = RX.search(data)
            if match:
                cat, act, da, freq, seq = match.groups()
                key = (bytes.fromhex(da.decode().replace(":", "")), int(seq, 16), int(cat), int(act))
                classifications.append((h.timestamp, key, int(freq)))
            status = STATUS.search(data)
            if status:
                statuses.append((h.timestamp, int(status[1])))
    patterns = {key: frame_pattern(*key) for _, key, _ in classifications}
    matches = Counter()
    if patterns:
        with path.open("rb") as stream:
            for record in ETL(stream):
                data = bytes(record.header.payload)
                if b"\xd0" in data:
                    for key, pattern in patterns.items():
                        if pattern.search(data):
                            matches[key] += 1
    print(f"{path.name}: classifications={len(classifications)}, status indications={len(statuses)}")
    for stamp, size in statuses:
        nearby = [(key, freq, stamp - t) for t, key, freq in classifications
                  if timedelta(0) <= stamp - t <= timedelta(milliseconds=1)]
        if len(nearby) != 1:
            print(f"  status={size} bytes, candidate classifications within 1 ms={len(nearby)}")
            continue
        key, freq, delay = nearby[0]
        _, seq, cat, act = key
        print(f"  category={cat} action={act} seq_field=0x{seq:04x} MHz={freq} "
              f"delay_us={delay.microseconds} status_bytes={size} matching_records={matches[key]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("etl", type=Path)
    inspect(parser.parse_args().etl)
