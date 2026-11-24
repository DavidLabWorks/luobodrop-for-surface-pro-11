"""Look for a contiguous ath12k management header and frame array in an ETW payload."""
import argparse
import re
import struct
from pathlib import Path

from dissect.etl import ETL

# The WMI command word and any outer transport header may be absent.
# Start at either required TLV, allowing intervening extensions.
START = re.compile(rb'(?=..(?:\x2c\x00|\x11\x00))', re.DOTALL)
TLV = struct.Struct('<HH')


def contains_management_pair(data):
    for match in START.finditer(data):
        pos = match.start()
        size_needed = None
        array_offset = None
        while pos + TLV.size <= len(data):
            size, tag = TLV.unpack_from(data, pos)
            pos += TLV.size
            if size > len(data) - pos:
                break
            if tag == 44:
                if size < 68 or size_needed is not None:
                    break
                size_needed = struct.unpack_from('<I', data, pos + 16)[0]
                if size_needed < 24:
                    break
            elif tag == 17 and array_offset is None:
                array_offset, capacity = pos, size
                # Version 0, frame type management, at least a basic MAC header.
                if size < 24 or data[pos] & 0x0f:
                    break
            if size_needed is not None and array_offset is not None:
                if size_needed <= capacity:
                    return True
                break
            pos += size
    return False


def inspect(path):
    count = notices = candidates = 0
    with path.open('rb') as stream:
        for record in ETL(stream):
            data = bytes(record.header.payload)
            count += 1
            notices += b'WMI_MGMT_RX_EVENTID' in data
            candidates += contains_management_pair(data)
    print(f'{path.name}: records={count} event_mentions={notices} candidate_records={candidates}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('etl', type=Path, nargs='+')
    for trace in parser.parse_args().etl:
        inspect(trace)
