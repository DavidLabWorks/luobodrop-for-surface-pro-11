"""Check one Windows wireless ETL for a contiguous radio-measurement action body."""
import argparse
from pathlib import Path
import re

from dissect.etl import ETL


def inspect(path):
    # WiFiCx TLV type (uint16), size (uint16), then category and action.
    candidate = re.compile(rb'\xbe\x00(..)\x05\x00', re.DOTALL)
    indication = re.compile(rb'WdiSendIndication:.*?StatusCode:ACTION_FRAME_RECEIVED,.*?StatusBufferSize\((\d+)\)')
    marks = []
    candidates = []
    records = 0
    with path.open('rb') as stream:
        for record in ETL(stream):
            header = record.header
            data = bytes(header.payload)
            records += 1
            mark = indication.search(data)
            if mark:
                marks.append((header.timestamp, int(mark[1])))
            for match in candidate.finditer(data):
                length = int.from_bytes(match[1], 'little')
                if length >= 5 and match.start() + 4 + length <= len(data):
                    candidates.append((header.timestamp, length))
    print(f"Records: {records}; indication markers: {len(marks)}")
    print(f"Complete category 5/action 0 TLV candidates in trace: {len(candidates)}")
    for index, (stamp, size) in enumerate(marks, 1):
        nearby = sum(abs((when - stamp).total_seconds()) <= 0.1 and length + 4 <= size
                     for when, length in candidates)
        print(f"Indication {index}: status bytes={size}, candidates within 100 ms={nearby}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('etl', type=Path)
    inspect(parser.parse_args().etl)
